/**
 * ci-reconcile-obligations.ts -- the obligation register's control loop
 * (ADR 0088, migration 0117).
 *
 * WHAT IT DOES, in order:
 *   1. IMPORT. Derive obligations from every source a machine can enumerate --
 *      client-labelled GitHub issues, open fleet_alert_state rows, operator
 *      change requests. Derived capture is the half that cannot be forgotten;
 *      the CLI covers only letters, which no source exposes.
 *   2. PROBE. For every non-terminal row, re-read the real surface and decide
 *      verified / still open / overdue / cannot-evaluate. Never reads a `done`
 *      flag: a status column is a claim about the world, and the world is what
 *      this checks (Law 14).
 *   3. CENSUS. Count artifacts per source class against obligations sourced
 *      from each. This is the only control here that can catch its own
 *      silence -- everything else validates rows that exist.
 *   4. ESCALATE. Write overdue / unverifiable / capture-gap findings into
 *      cost_anomaly_alerts, which already emails the ops inbox for any
 *      non-cost source (workers/fleet-alerts/src/sink-notify.ts:57).
 *
 * LAYOUT. `reconcile` below reads as those phases, one function each. The pure
 * rules (verdicts, severities, the census, the alert row) live in
 * scripts/lib/obligation-reconcile/verdicts.ts and the reach into GitHub and
 * D1 (importers, probes, certification) in .../sources.ts. The split landed
 * 2026-09-25 when lint first reached scripts/ and found `main` at 249 lines
 * and complexity 50; the run's output is byte-identical across it.
 *
 * WHY TYPESCRIPT, AND WHY IT SHARES THE CONSOLE'S MODULE. The first draft was a
 * .mjs script with its SQL written inline, which meant the state machine and the
 * upsert semantics existed twice: once in src/lib/db/obligations.ts, which the
 * admin console reads, and once as strings here, which the reconciler wrote.
 * Two implementations of one rule drift silently, and this repo has already paid
 * for that shape more than once. scripts/lib/wrangler-d1.ts supplies a
 * D1-shaped handle over the wrangler CLI so this process uses the real module.
 *
 * WHY THE UNIVERSE IS D1 ROWS, NEVER FILES. Same reason
 * ci-reconcile-customer-configs.sh:99-102 gives: looping over rows means the
 * reconciler structurally cannot invent an obligation. It closes and escalates
 * what exists; it never manufactures work.
 *
 * TWO DENOMINATORS. `total` is every row, `universe` is the non-terminal rows
 * this run considered. One count cannot distinguish "nothing to do" from "the
 * selector is broken" -- the case that let the cadence engine sit at 7 of 16
 * overdue while looking healthy. A zero total is cannot-evaluate (exit 1), but
 * only once a prior run has seen rows: alarming from day one would teach the
 * Captain to ignore this, which is the same failure by a different route.
 *
 * EXIT CODES (house protocol, matching customer-config-reconcile):
 *   0  converged
 *   1  cannot evaluate / control broken
 *   2  findings (the workflow opens or updates the rolling issue)
 *
 * Env:
 *   SS_RECONCILE_D1_CMD   override the wrangler invocation (tests)
 *   SS_RECONCILE_GH_CMD   override the gh invocation (tests)
 *   SS_RECONCILE_DB       D1 database name (default ss-console-db)
 *   GITHUB_RUN_URL        the Actions run this came from; its ABSENCE is what
 *                         marks a certification unwitnessed
 *   SS_RECONCILE_DRY_RUN  classify and report, write nothing
 */

import type { D1Database } from '@cloudflare/workers-types'
import { wranglerD1 } from './lib/wrangler-d1'
import {
  countByOriginSource,
  countObligations,
  findUnwitnessedCertifications,
  finishReconcileRun,
  listOpenObligations,
  priorHighWaterMark,
  startReconcileRun,
  upsertObligation,
  type Obligation,
  type UpsertObligationInput,
} from '../src/lib/db/obligations'
import {
  alertFor,
  censusGap,
  classifyRow,
  emptyRegisterIsFailure,
  UNDATED_STALE_DAYS,
  type AlertInsert,
  type Verdict,
} from './lib/obligation-reconcile/verdicts'
import {
  certify,
  importAlertState,
  importChangeRequests,
  importGithubIssues,
  probeEvidence,
  safeAll,
  withdraw,
  writeAlert,
  type Seat,
} from './lib/obligation-reconcile/sources'

const DB_NAME = process.env.SS_RECONCILE_DB || 'ss-console-db'
const DRY_RUN = process.env.SS_RECONCILE_DRY_RUN === '1'

export const EXIT_CONVERGED = 0
export const EXIT_CANNOT_EVALUATE = 1
export const EXIT_FINDINGS = 2

/**
 * A phase that cannot evaluate throws this; `main` prints it and exits 1.
 * The message is the text after "cannot evaluate: ".
 */
class CannotEvaluate extends Error {}

/** Everything the probe, witness and census phases count or raise. */
interface Tally {
  verified: number
  withdrawn: number
  overdue: number
  stale: number
  stillOpen: number
  cannotProbeable: number
  cannotAttested: number
  unwitnessed: number
  gaps: number
  findings: string[]
  alerts: AlertInsert[]
}

function emptyTally(): Tally {
  return {
    verified: 0,
    withdrawn: 0,
    overdue: 0,
    stale: 0,
    stillOpen: 0,
    cannotProbeable: 0,
    cannotAttested: 0,
    unwitnessed: 0,
    gaps: 0,
    findings: [],
    alerts: [],
  }
}

/** A read the run cannot proceed without. */
async function mustRead<T>(what: string, fn: () => Promise<T[]>): Promise<T[]> {
  const read = await safeAll(fn)
  if (!read.ok) throw new CannotEvaluate(`${what} unreadable`)
  return read.rows
}

// ------------------------------------------------------------ 0. seats + run

async function readSeats(db: D1Database): Promise<Seat[]> {
  const seats = await mustRead('customer_configs', async () => {
    const res = await db
      .prepare(`SELECT customer_slug, entity_id FROM customer_configs ORDER BY customer_slug`)
      .all<Seat>()
    return res.results
  })
  if (seats.length === 0) throw new CannotEvaluate('no seats in customer_configs')
  return seats
}

async function openRun(db: D1Database): Promise<string> {
  if (DRY_RUN) return 'dry-run'
  try {
    return await startReconcileRun(db, process.env.GITHUB_RUN_URL || null)
  } catch {
    throw new CannotEvaluate('could not open a reconcile run')
  }
}

// ----------------------------------------------------------------- 1. import

async function deriveObligations(
  db: D1Database,
  seats: readonly Seat[],
  repo: string
): Promise<UpsertObligationInput[]> {
  const seatsBySlug = new Map(seats.map((s) => [s.customer_slug, s]))
  const gh = importGithubIssues(seats, repo)
  if (!gh.ok) throw new CannotEvaluate(`GitHub unreadable (${gh.error})`)

  const alerts = await mustRead('fleet_alert_state', async () => {
    const res = await db
      .prepare(`SELECT customer_slug, condition FROM fleet_alert_state WHERE status = 'open'`)
      .all<{ customer_slug: string; condition: string }>()
    return res.results
  })
  // Consistent with the other two import reads: a source we cannot read is a
  // control failure, not an empty source. Swallowing it would let the census
  // conclude "this source produced nothing" when the truth is "we never
  // looked", the exact conflation the evidence-class split exists to stop.
  const changes = await mustRead('operator_change_requests', async () => {
    const res = await db
      .prepare(
        `SELECT id, customer_slug, summary FROM operator_change_requests
          WHERE status IN ('open','pending')`
      )
      .all<{ id: string; customer_slug: string; summary: string | null }>()
    return res.results
  })
  return [
    ...gh.rows,
    ...importAlertState(alerts, seatsBySlug),
    ...importChangeRequests(changes, seatsBySlug),
  ]
}

async function writeImported(
  db: D1Database,
  imported: readonly UpsertObligationInput[]
): Promise<number> {
  if (DRY_RUN) return 0
  let written = 0
  for (const row of imported) {
    try {
      await upsertObligation(db, row)
      written += 1
    } catch {
      /* a single bad row must not stop the sweep; the census reports the shortfall */
    }
  }
  return written
}

// ----------------------------------------------------------- 2. denominators

async function readDenominators(db: D1Database): Promise<{ total: number; universe: number }> {
  let counts: { total: number; universe: number }
  try {
    counts = await countObligations(db)
  } catch {
    throw new CannotEvaluate('register unreadable')
  }
  const highWaterMark = await priorHighWaterMark(db).catch(() => 0)
  if (emptyRegisterIsFailure(counts.total, highWaterMark)) {
    throw new CannotEvaluate(
      `register is empty but a prior run saw ${highWaterMark} rows — selector or data loss`
    )
  }
  return counts
}

// ------------------------------------------------------------------ 3. probe

async function recordVerified(db: D1Database, row: Obligation, runId: string, tally: Tally) {
  if (row.state === 'verified') return
  tally.verified += 1
  if (DRY_RUN) return
  const refused = await certify(db, row, runId)
  if (refused) {
    tally.findings.push(
      `::error::${row.obligation_id} ${row.customer_slug} not certified: ${refused}`
    )
  }
}

async function recordWithdrawn(db: D1Database, row: Obligation, tally: Tally) {
  tally.withdrawn += 1
  if (DRY_RUN) return
  const refused = await withdraw(db, row)
  if (refused) {
    tally.findings.push(
      `::error::${row.obligation_id} ${row.customer_slug} not cancelled: ${refused}`
    )
  }
}

function recordCannotEvaluate(row: Obligation, attested: boolean, tally: Tally) {
  if (attested) {
    tally.cannotAttested += 1
    tally.findings.push(
      `::warning::${row.obligation_id} ${row.customer_slug} attestation stale — no receipt`
    )
    return
  }
  tally.cannotProbeable += 1
  tally.findings.push(
    `::error::${row.obligation_id} ${row.customer_slug} evidence unreadable (probeable) — control broken`
  )
}

/**
 * NO `row.what` IN A FINDING. Findings become `reconcile.txt`, which the
 * workflow cats into the Actions log AND into a `gh issue create` body in
 * venturecrane/ss-console, a PUBLIC repo. `what` is client-confidential: it is
 * a sentence about a named firm's internal backlog, taken verbatim from
 * correspondence in the private engagements repo. The obligation id is enough
 * to look the row up with `register list` at a private terminal. Pinned by
 * tests/obligation-reconcile.test.ts, whose fixture text is deliberately
 * synthetic for the same reason.
 */
function recordAging(row: Obligation, verdict: Verdict, today: string, tally: Tally) {
  if (verdict.verdict === 'overdue') {
    tally.overdue += 1
    tally.findings.push(
      `::warning::${row.obligation_id} ${row.customer_slug}/${row.kind} overdue ${verdict.days}d`
    )
  } else if (verdict.verdict === 'stale') {
    tally.stale += 1
    tally.findings.push(
      `::warning::${row.obligation_id} ${row.customer_slug}/${row.kind} open ${verdict.days}d with no due date`
    )
  }
  const alert = alertFor(row, verdict, today)
  if (alert) tally.alerts.push(alert)
}

async function probeOpenRows(
  db: D1Database,
  runId: string,
  clock: { now: Date; today: string },
  tally: Tally
): Promise<void> {
  const open = await mustRead('open obligations', async () => listOpenObligations(db))
  for (const row of open) {
    const verdict = classifyRow(row, await probeEvidence(row, db), clock.now)
    switch (verdict.verdict) {
      case 'verified':
        await recordVerified(db, row, runId, tally)
        break
      case 'withdrawn':
        await recordWithdrawn(db, row, tally)
        break
      case 'overdue':
      case 'stale':
        recordAging(row, verdict, clock.today, tally)
        break
      case 'cannot_evaluate':
        recordCannotEvaluate(row, verdict.class === 'attested', tally)
        break
      case 'still_open':
      case 'unverifiable':
        tally.stillOpen += 1
        break
    }
  }
}

// ---------------------------------------------- 4. unwitnessed certifications

async function flagUnwitnessed(db: D1Database, today: string, tally: Tally): Promise<void> {
  const unwitnessed = await safeAll(async () => findUnwitnessedCertifications(db))
  tally.unwitnessed = unwitnessed.rows.length
  for (const row of unwitnessed.rows) {
    tally.findings.push(
      `::error::${row.obligation_id} ${row.customer_slug} certified by a run no CI workflow stands behind`
    )
    const alert = alertFor(row, { verdict: 'unverifiable' }, today)
    if (alert) tally.alerts.push(alert)
  }
}

// ----------------------------------------------------------------- 5. census

async function obligationsBySource(
  db: D1Database,
  seats: readonly Seat[]
): Promise<Map<string, number>> {
  const derived = new Map<string, number>()
  for (const seat of seats) {
    const perSource = await safeAll(async () => [await countByOriginSource(db, seat.customer_slug)])
    for (const [source, n] of Object.entries(perSource.rows[0] ?? {})) {
      derived.set(source, (derived.get(source) ?? 0) + n)
    }
  }
  return derived
}

/**
 * A capture gap means the register may have gone quiet on a whole source.
 * Leaving it in the Actions log only would make the ONE control that can catch
 * this design's own silence the one control the Captain never sees. It is
 * raised per seat because the alert key is per entity.
 */
function gapAlerts(
  seats: readonly Seat[],
  source: string,
  counts: { artifacts: number; obligations: number },
  today: string
): AlertInsert[] {
  return seats.map((seat) => ({
    entity_id: seat.entity_id,
    customer_slug: seat.customer_slug,
    alert_date: today,
    driver: `obligation:${source}:obligation_capture_gap`,
    summary: `Capture gap: ${source} holds ${counts.artifacts} artifacts and produced no obligations.`,
    details_json: JSON.stringify({
      condition: 'obligation_capture_gap',
      source,
      artifacts: counts.artifacts,
      obligations: counts.obligations,
      severity: 'warning',
    }),
  }))
}

async function runCensus(
  db: D1Database,
  seats: readonly Seat[],
  imported: readonly UpsertObligationInput[],
  today: string,
  tally: Tally
): Promise<string[]> {
  const derivedBySource = await obligationsBySource(db, seats)
  const artifactCounts = new Map<string, number>()
  for (const row of imported) {
    artifactCounts.set(row.origin_source, (artifactCounts.get(row.origin_source) ?? 0) + 1)
  }

  const lines: string[] = []
  for (const [source, artifacts] of artifactCounts) {
    const verdict = censusGap(source, artifacts, derivedBySource.get(source) ?? 0)
    lines.push(
      `  ${source.padEnd(24)} artifacts ${String(verdict.artifacts).padStart(4)}  obligations ${String(verdict.obligations).padStart(4)}${verdict.gap ? '   <-- GAP' : ''}`
    )
    if (!verdict.gap) continue
    tally.gaps += 1
    tally.findings.push(
      `::warning::capture gap: ${source} holds ${verdict.artifacts} artifacts and produced no obligations`
    )
    tally.alerts.push(...gapAlerts(seats, source, verdict, today))
  }
  return lines
}

// -------------------------------------------------------- 6. report + settle

interface RunFacts {
  total: number
  universe: number
  imported: number
  written: number
  censusLines: string[]
}

function printSummary(facts: RunFacts, tally: Tally): void {
  const cannot = tally.cannotProbeable + tally.cannotAttested
  for (const line of [
    '── obligation reconcile summary ──',
    `obligations total:       ${String(facts.total).padStart(5)}`,
    `universe (non-terminal): ${String(facts.universe).padStart(5)}`,
    `  verified this run:     ${String(tally.verified).padStart(5)}`,
    `  withdrawn this run:    ${String(tally.withdrawn).padStart(5)}`,
    `  still open (in window):${String(tally.stillOpen).padStart(5)}`,
    `  overdue:               ${String(tally.overdue).padStart(5)}`,
    `  stale (undated, ${UNDATED_STALE_DAYS}d+):${String(tally.stale).padStart(4)}`,
    `imported this run:       ${String(facts.written).padStart(5)} of ${facts.imported} derived`,
    `cannot evaluate:         ${String(cannot).padStart(5)}  (attested: ${tally.cannotAttested}, probeable: ${tally.cannotProbeable})`,
    `unwitnessed certifications: ${tally.unwitnessed}`,
    'coverage census:',
    ...facts.censusLines,
    ...tally.findings,
  ]) {
    console.log(line)
  }
}

/**
 * Decide the outcome BEFORE recording it. An earlier version stamped the run
 * row with "findings" unless a probe broke, so a converged run was recorded as
 * a finding forever and reconcile_runs could never show a healthy pass.
 *
 * `stale` belongs in this sum, and its absence was the whole defect: a verdict
 * that raises an alert but does not count as a finding produces a run that
 * pages the Captain and then records itself CONVERGED. Eleven rows sat
 * untouched behind exactly that arithmetic.
 */
function exitCodeFor(tally: Tally): number {
  const findingCount =
    tally.overdue + tally.stale + tally.cannotAttested + tally.unwitnessed + tally.gaps
  if (tally.cannotProbeable > 0) return EXIT_CANNOT_EVALUATE
  return findingCount > 0 ? EXIT_FINDINGS : EXIT_CONVERGED
}

async function settle(
  db: D1Database,
  runId: string,
  facts: RunFacts,
  tally: Tally,
  exitCode: number
): Promise<void> {
  if (DRY_RUN) return
  for (const alert of tally.alerts) await writeAlert(db, alert)
  try {
    await finishReconcileRun(db, runId, {
      total: facts.total,
      universe: facts.universe,
      verified: tally.verified,
      overdue: tally.overdue,
      cannotEvaluate: tally.cannotProbeable + tally.cannotAttested,
      exitCode,
    })
  } catch {
    /* the run row is provenance, not the result; a failed stamp must not mask findings */
  }
}

function announce(exitCode: number, tally: Tally): number {
  // A broken probeable surface is the control failing, not a finding about the
  // work: it must not be reported as "everything is fine except some rows".
  if (exitCode === EXIT_CANNOT_EVALUATE) {
    console.error(
      `reconcile: ${tally.cannotProbeable} probeable surface(s) unreadable — control broken`
    )
  } else if (exitCode === EXIT_FINDINGS) {
    console.log(
      `reconcile findings: ${tally.overdue} overdue, ${tally.stale} stale undated, ${tally.cannotAttested} stale attestations, ${tally.unwitnessed} unwitnessed, ${tally.gaps} capture gaps.`
    )
  } else {
    console.log('reconcile: converged.')
  }
  return exitCode
}

// --------------------------------------------------------------------- main

async function reconcile(db: D1Database): Promise<number> {
  const repo = process.env.GITHUB_REPOSITORY || 'venturecrane/ss-console'
  const now = new Date()
  const clock = { now, today: now.toISOString().slice(0, 10) }

  const seats = await readSeats(db)
  const runId = await openRun(db)
  const imported = await deriveObligations(db, seats, repo)
  const written = await writeImported(db, imported)
  const { total, universe } = await readDenominators(db)

  const tally = emptyTally()
  await probeOpenRows(db, runId, clock, tally)
  await flagUnwitnessed(db, clock.today, tally)
  const censusLines = await runCensus(db, seats, imported, clock.today, tally)

  const facts: RunFacts = { total, universe, imported: imported.length, written, censusLines }
  printSummary(facts, tally)
  const exitCode = exitCodeFor(tally)
  await settle(db, runId, facts, tally, exitCode)
  return announce(exitCode, tally)
}

export async function main(): Promise<number> {
  const db = wranglerD1({
    database: DB_NAME,
    commandOverride: process.env.SS_RECONCILE_D1_CMD ?? null,
  }) as unknown as D1Database
  try {
    return await reconcile(db)
  } catch (err) {
    if (!(err instanceof CannotEvaluate)) throw err
    console.error(`cannot evaluate: ${err.message}`)
    return EXIT_CANNOT_EVALUATE
  }
}

const invokedDirectly = process.argv[1]?.endsWith('ci-reconcile-obligations.ts')
if (invokedDirectly) {
  main()
    .then((code) => process.exit(code))
    .catch((err: unknown) => {
      console.error(`reconcile: unhandled failure — ${(err as Error)?.message ?? String(err)}`)
      process.exit(EXIT_CANNOT_EVALUATE)
    })
}
