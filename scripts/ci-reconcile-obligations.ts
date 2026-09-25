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

import { execFileSync } from 'node:child_process'
import type { D1Database } from '@cloudflare/workers-types'
import { sqlLiteral, wranglerD1 } from './lib/wrangler-d1'
import { ageInDays } from './lib/sqlite-time.mjs'
import {
  countByOriginSource,
  countObligations,
  findUnwitnessedCertifications,
  finishReconcileRun,
  listOpenObligations,
  priorHighWaterMark,
  startReconcileRun,
  transitionObligation,
  upsertObligation,
  type Obligation,
  type ObligationKind,
  type UpsertObligationInput,
} from '../src/lib/db/obligations'

const DB_NAME = process.env.SS_RECONCILE_DB || 'ss-console-db'
const DRY_RUN = process.env.SS_RECONCILE_DRY_RUN === '1'

export const EXIT_CONVERGED = 0
export const EXIT_CANNOT_EVALUATE = 1
export const EXIT_FINDINGS = 2

interface Seat {
  customer_slug: string
  entity_id: string
}

export type Verdict =
  | { verdict: 'verified' }
  | { verdict: 'still_open' }
  | { verdict: 'overdue'; days: number }
  | { verdict: 'stale'; days: number }
  | { verdict: 'cannot_evaluate'; class: 'probeable' | 'attested' }
  | { verdict: 'unverifiable' }

export type ProbeResult = { status: 'present' | 'absent' | 'unreachable' }

// ---------------------------------------------------------------- pure logic

/**
 * Decide what a row's probe result means.
 *
 * `cannot_evaluate` splits by evidence class deliberately. A probeable surface
 * that will not answer is a BROKEN CONTROL (exit 1) -- we were supposed to be
 * able to read it. A stale attestation is a FINDING (exit 2) -- the seat has
 * stopped confirming something it alone can see. Collapsing the two lets the
 * unprobeable class grow silently behind a green build.
 */
export function classifyRow(row: Obligation, probe: ProbeResult, now: Date): Verdict {
  if (probe.status === 'unreachable') {
    return {
      verdict: 'cannot_evaluate',
      class: row.evidence_class === 'attested' ? 'attested' : 'probeable',
    }
  }
  if (probe.status === 'present') return { verdict: 'verified' }
  if (row.due_at) {
    const due = new Date(row.due_at)
    if (!Number.isNaN(due.getTime()) && due < now) {
      return { verdict: 'overdue', days: Math.floor((now.getTime() - due.getTime()) / 86400000) }
    }
  }
  // An UNDATED row that stays open is the failure this register was built to
  // replace. The predecessor cadence engine reached 7 of 16 items overdue, one
  // by 134 days, while reporting itself healthy -- and until this branch
  // existed, so did this one: `still_open` was counted nowhere, so eleven rows
  // could sit untouched forever behind a converged nightly run.
  //
  // Scoped to `captured` rows on purpose. A derived GitHub row probes `absent`
  // for as long as its issue is merely open, so alarming on those would page on
  // ordinary backlog that `crane_status` already lists. Captured rows -- the
  // letter-stated promises -- are the ones nothing else in the venture watches.
  if (!row.due_at && row.origin === 'captured') {
    // ageInDays, not `new Date(...)`: SQLite writes UTC with no zone marker and
    // JS parses that as local, which reads every row younger than it is and
    // delays the ladder by the machine's offset.
    const days = ageInDays(row.created_at, now.getTime())
    if (days !== null && days >= UNDATED_STALE_DAYS) return { verdict: 'stale', days }
  }
  return { verdict: 'still_open' }
}

/** Severity ladder for an overdue obligation. Warning at due, critical a week on. */
export function overdueSeverity(days: number): 'warning' | 'critical' {
  return days >= 7 ? 'critical' : 'warning'
}

/** The age at which an undated, still-open captured obligation becomes a finding. */
export const UNDATED_STALE_DAYS = 30

/**
 * Severity ladder for an undated obligation that has simply sat there.
 *
 * Thirty days is one billing cycle, which is the shortest period over which
 * "we still owe this" stops being ordinary work in flight. Sixty is the point
 * past which the predecessor's 134-day silence starts to look reachable again.
 */
export function undatedAgeSeverity(days: number): 'warning' | 'critical' {
  return days >= 60 ? 'critical' : 'warning'
}

/**
 * Should a zero-row read be treated as a broken selector?
 *
 * Only when a prior run saw rows. This is the high-water mark, and it is the
 * difference between a control that is trusted and one that cries wolf from the
 * day it ships.
 */
export function emptyRegisterIsFailure(total: number, highWaterMark: number): boolean {
  return total === 0 && highWaterMark > 0
}

export interface CensusVerdict {
  source: string
  artifacts: number
  obligations: number
  gap: boolean
}

/**
 * The coverage census verdict for one source class.
 *
 * Artifacts present and zero obligations derived from them is the signature of
 * capture having silently stopped -- the failure every other control in this
 * design is blind to, because they all validate rows that exist.
 */
export function censusGap(
  sourceClass: string,
  artifactCount: number,
  obligationCount: number
): CensusVerdict {
  return {
    source: sourceClass,
    artifacts: artifactCount,
    obligations: obligationCount,
    gap: artifactCount > 0 && obligationCount === 0,
  }
}

/** A stable key derived from a source identity, so re-import upserts. */
export function importKey(prefix: string, identity: string | number): string {
  return `${prefix}-${String(identity)
    .replace(/[^a-z0-9]+/gi, '-')
    .toLowerCase()}`.slice(0, 60)
}

// ---------------------------------------------------------------- io helpers

function runGh(args: string[]): { ok: true; stdout: string } | { ok: false; error: string } {
  const override = process.env.SS_RECONCILE_GH_CMD
  try {
    const stdout = execFileSync(override ?? 'gh', args, {
      encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024,
    })
    return { ok: true, stdout }
  } catch (err) {
    return { ok: false, error: String((err as Error)?.message ?? err).split('\n')[0] }
  }
}

/**
 * `gh issue list --json number,title`, read field by field: a row without a
 * numeric number and a string title makes the whole read unparseable rather
 * than importing an obligation keyed on `undefined`.
 */
function parseIssueList(stdout: string): { number: number; title: string }[] | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(stdout)
  } catch {
    return null
  }
  if (!Array.isArray(parsed)) return null
  const issues: { number: number; title: string }[] = []
  const rows: unknown[] = parsed
  for (const row of rows) {
    if (typeof row !== 'object' || row === null || Array.isArray(row)) return null
    const { number, title }: Record<string, unknown> = { ...row }
    if (typeof number !== 'number' || typeof title !== 'string') return null
    issues.push({ number, title })
  }
  return issues
}

/** Reads that must not throw: a failed read is a control failure, not a crash. */
async function safeAll<T>(fn: () => Promise<T[]>): Promise<{ ok: boolean; rows: T[] }> {
  try {
    return { ok: true, rows: await fn() }
  } catch {
    return { ok: false, rows: [] }
  }
}

// ------------------------------------------------------------------- import

/**
 * Client-labelled GitHub issues become product_defect obligations.
 *
 * The issue is the source of truth for the DEFECT; the obligation records that a
 * specific client is blocked by it. The probe reads the issue's state, and a
 * closed issue is the evidence `certify` acts on -- so under Law 9 an issue
 * labelled `client:<slug>` is closed only when that client can do the thing,
 * not when the PR merges.
 */
export function importGithubIssues(
  seats: readonly Seat[],
  repo: string
): { ok: true; rows: UpsertObligationInput[] } | { ok: false; error: string } {
  const rows: UpsertObligationInput[] = []
  for (const seat of seats) {
    const result = runGh([
      'issue',
      'list',
      '--repo',
      repo,
      '--label',
      `client:${seat.customer_slug}`,
      '--state',
      'open',
      '--limit',
      '100',
      '--json',
      'number,title',
    ])
    if (!result.ok) return { ok: false, error: result.error }
    const issues = parseIssueList(result.stdout)
    if (!issues) return { ok: false, error: 'github_output_unparseable' }
    for (const issue of issues) {
      rows.push({
        customer_slug: seat.customer_slug,
        entity_id: seat.entity_id,
        stable_key: importKey('gh', issue.number),
        kind: 'product_defect',
        what: issue.title,
        origin: 'imported',
        origin_source: 'github',
        source_kind: 'github',
        source_ref: `${repo}#${issue.number}`,
        evidence_class: 'probeable',
        evidence_surface: 'github',
        evidence_locator: `${repo}#${issue.number}`,
      })
    }
  }
  return { ok: true, rows }
}

/**
 * Open fleet_alert_state conditions become obligations.
 *
 * A token expiring is a renewal we owe; any other condition is an incident we
 * owe remediation for. The alert says something is wrong; the obligation says
 * somebody owes work about it, and stays until the work is proven done.
 */
export function importAlertState(
  alertRows: readonly { customer_slug: string; condition: string }[],
  seatsBySlug: Map<string, Seat>
): UpsertObligationInput[] {
  const rows: UpsertObligationInput[] = []
  for (const alert of alertRows) {
    const seat = seatsBySlug.get(alert.customer_slug)
    if (!seat) continue
    const isRenewal = alert.condition.startsWith('connector_token_expiring:')
    rows.push({
      customer_slug: alert.customer_slug,
      entity_id: seat.entity_id,
      stable_key: importKey('alert', alert.condition),
      kind: (isRenewal ? 'renewal' : 'incident') as ObligationKind,
      what: isRenewal
        ? `Renew the credential behind ${alert.condition.split(':')[1] ?? 'a connector'} before it expires.`
        : `Clear the seat condition "${alert.condition}".`,
      origin: 'imported',
      origin_source: 'fleet_alert_state',
      source_kind: 'alert_state',
      source_ref: `${alert.customer_slug}:${alert.condition}`,
      evidence_class: 'probeable',
      evidence_surface: 'fleet_alert_state',
      evidence_locator: `${alert.customer_slug}:${alert.condition}`,
    })
  }
  return rows
}

/** Operator change requests become external_dependency obligations. */
export function importChangeRequests(
  changeRows: readonly { id: string; customer_slug: string; summary: string | null }[],
  seatsBySlug: Map<string, Seat>
): UpsertObligationInput[] {
  const rows: UpsertObligationInput[] = []
  for (const cr of changeRows) {
    const seat = seatsBySlug.get(cr.customer_slug)
    if (!seat) continue
    rows.push({
      customer_slug: cr.customer_slug,
      entity_id: seat.entity_id,
      stable_key: importKey('cr', cr.id),
      kind: 'external_dependency',
      what: cr.summary || 'Operator change request awaiting resolution.',
      origin: 'imported',
      origin_source: 'operator_change_requests',
      source_kind: 'ledger',
      source_ref: `operator_change_requests:${cr.id}`,
      evidence_class: 'probeable',
      evidence_surface: 'd1',
      evidence_locator: `operator_change_requests:${cr.id}`,
    })
  }
  return rows
}

// -------------------------------------------------------------------- probe

/**
 * Re-read the surface an obligation's evidence points at.
 *
 * Returns 'present' (the thing is really there), 'absent' (it is not, so the
 * obligation stands), or 'unreachable' (we could not tell -- never conflated
 * with absent, because "I could not look" and "it is not there" demand opposite
 * responses).
 */
export async function probeEvidence(row: Obligation, db: D1Database): Promise<ProbeResult> {
  if (!row.evidence_locator) return { status: 'absent' }

  switch (row.evidence_surface) {
    case 'github': {
      const [repo, number] = row.evidence_locator.split('#')
      const result = runGh([
        'issue',
        'view',
        number,
        '--repo',
        repo,
        '--json',
        'state',
        '-q',
        '.state',
      ])
      if (!result.ok) return { status: 'unreachable' }
      return { status: result.stdout.trim() === 'CLOSED' ? 'present' : 'absent' }
    }
    case 'fleet_alert_state': {
      const [slug, ...rest] = row.evidence_locator.split(':')
      const condition = rest.join(':')
      const found = await safeAll(async () => {
        const res = await db
          .prepare(`SELECT status FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?`)
          .bind(slug, condition)
          .all<{ status: string }>()
        return res.results
      })
      if (!found.ok) return { status: 'unreachable' }
      const status = found.rows[0]?.status
      // No row, or a resolved row, means the condition cleared.
      return { status: !status || status === 'resolved' ? 'present' : 'absent' }
    }
    case 'd1': {
      const [table, id] = row.evidence_locator.split(':')
      // The table name cannot be a bound parameter, so it is allow-shaped
      // rather than escaped: anything but a bare identifier is refused.
      if (!/^[a-z_]+$/.test(table)) return { status: 'unreachable' }
      const found = await safeAll(async () => {
        const res = await db
          .prepare(`SELECT status FROM ${table} WHERE id = ?`)
          .bind(id)
          .all<{ status: string }>()
        return res.results
      })
      if (!found.ok) return { status: 'unreachable' }
      const status = found.rows[0]?.status
      return { status: status === 'resolved' || status === 'completed' ? 'present' : 'absent' }
    }
    default:
      // An attested surface has no CI-reachable probe by definition. The
      // receipt's freshness is what stands in, and no receipt proves nothing.
      // Two live today: a Smokeball filing (the seat's receipt) and
      // `engagements` (the sent letter `register deliver` read off the private
      // repo's origin/main, which no CI credential can open).
      if (row.evidence_class === 'attested') {
        return { status: row.evidence_last_verified_at ? 'present' : 'unreachable' }
      }
      return { status: 'unreachable' }
  }
}

/**
 * Move a row whose evidence probed present into `verified`.
 *
 * A `delivered` row goes straight there. An IMPORTED row still sitting in a
 * working state is walked through `delivered` first: its source clearing (the
 * alert resolved, the change request completed, the issue closed) IS the
 * delivery, and nobody else will ever mark it. Before this, those rows were
 * counted in "verified this run" every night and never moved, so the count said
 * one thing and the register another.
 *
 * A captured row is never walked: it reaches `delivered` only through
 * `register deliver`, which cites the letter that kept the promise.
 *
 * Returns null on success, or the refusal. Refusals are reported, never
 * swallowed -- a reconciler that dropped one would read converged while the row
 * sat where it was.
 */
async function certify(db: D1Database, row: Obligation, runId: string): Promise<string | null> {
  if (row.state !== 'delivered') {
    if (row.origin !== 'imported') return `a ${row.origin} row in ${row.state} probed present`
    const step = await transitionObligation(db, {
      obligationId: row.obligation_id,
      to: 'delivered',
    })
    if (!step.ok) return step.error
  }
  const done = await transitionObligation(db, {
    obligationId: row.obligation_id,
    to: 'verified',
    reconcileRunId: runId,
  })
  return done.ok ? null : done.error
}

// ------------------------------------------------------------------- alerts

export interface AlertInsert {
  entity_id: string
  customer_slug: string
  alert_date: string
  driver: string
  summary: string
  details_json: string
}

export function alertFor(row: Obligation, verdict: Verdict, today: string): AlertInsert | null {
  const condition =
    verdict.verdict === 'overdue'
      ? 'obligation_overdue'
      : verdict.verdict === 'stale'
        ? 'obligation_stale'
        : verdict.verdict === 'unverifiable'
          ? 'obligation_unverifiable'
          : null
  if (!condition) return null
  return {
    entity_id: row.entity_id,
    customer_slug: row.customer_slug,
    alert_date: today,
    // The driver is the discriminator: several obligations for one client on
    // one day must not collapse into a single row under the composite key.
    driver: `obligation:${row.obligation_id}:${condition}`,
    // An alert summary MAY carry `what`. This path ends at team@smd.services
    // over Resend and at the admin console, both private. The findings list is
    // the opposite case -- see the redaction note at the overdue finding.
    summary:
      verdict.verdict === 'overdue'
        ? `Overdue ${verdict.days}d: ${row.what}`
        : verdict.verdict === 'stale'
          ? `Open ${verdict.days}d with no due date: ${row.what}`
          : `Certification not witnessed by a CI run: ${row.what}`,
    details_json: JSON.stringify({
      obligation_id: row.obligation_id,
      kind: row.kind,
      due_at: row.due_at,
      severity:
        verdict.verdict === 'overdue'
          ? overdueSeverity(verdict.days)
          : verdict.verdict === 'stale'
            ? undatedAgeSeverity(verdict.days)
            : 'critical',
      source_ref: row.source_ref,
    }),
  }
}

async function writeAlert(db: D1Database, alert: AlertInsert): Promise<void> {
  await db
    .prepare(
      `INSERT INTO cost_anomaly_alerts (entity_id, customer_slug, alert_date, driver, source,
         daily_cents, rolling_avg_cents, ratio_bps, threshold_bps, summary, details_json, detected_at)
       VALUES (?, ?, ?, ?, 'obligation', 0, 0, 0, 0, ?, ?, datetime('now'))
       ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET
         summary = excluded.summary, details_json = excluded.details_json,
         detected_at = excluded.detected_at`
    )
    .bind(
      alert.entity_id,
      alert.customer_slug,
      alert.alert_date,
      alert.driver,
      alert.summary,
      alert.details_json
    )
    .run()
}

// --------------------------------------------------------------------- main

export async function main(): Promise<number> {
  const repo = process.env.GITHUB_REPOSITORY || 'venturecrane/ss-console'
  const today = new Date().toISOString().slice(0, 10)
  const now = new Date()
  const db = wranglerD1({
    database: DB_NAME,
    commandOverride: process.env.SS_RECONCILE_D1_CMD ?? null,
  }) as unknown as D1Database

  const seatsRead = await safeAll(async () => {
    const res = await db
      .prepare(`SELECT customer_slug, entity_id FROM customer_configs ORDER BY customer_slug`)
      .all<Seat>()
    return res.results
  })
  if (!seatsRead.ok) {
    console.error('cannot evaluate: customer_configs unreadable')
    return EXIT_CANNOT_EVALUATE
  }
  const seats = seatsRead.rows
  if (seats.length === 0) {
    console.error('cannot evaluate: no seats in customer_configs')
    return EXIT_CANNOT_EVALUATE
  }
  const seatsBySlug = new Map(seats.map((s) => [s.customer_slug, s]))

  let runId = 'dry-run'
  if (!DRY_RUN) {
    try {
      runId = await startReconcileRun(db, process.env.GITHUB_RUN_URL || null)
    } catch {
      console.error('cannot evaluate: could not open a reconcile run')
      return EXIT_CANNOT_EVALUATE
    }
  }

  // ---- 1. import
  const imported: UpsertObligationInput[] = []
  const gh = importGithubIssues(seats, repo)
  if (!gh.ok) {
    console.error(`cannot evaluate: GitHub unreadable (${gh.error})`)
    return EXIT_CANNOT_EVALUATE
  }
  imported.push(...gh.rows)

  const alertRead = await safeAll(async () => {
    const res = await db
      .prepare(`SELECT customer_slug, condition FROM fleet_alert_state WHERE status = 'open'`)
      .all<{ customer_slug: string; condition: string }>()
    return res.results
  })
  if (!alertRead.ok) {
    console.error('cannot evaluate: fleet_alert_state unreadable')
    return EXIT_CANNOT_EVALUATE
  }
  imported.push(...importAlertState(alertRead.rows, seatsBySlug))

  const crRead = await safeAll(async () => {
    const res = await db
      .prepare(
        `SELECT id, customer_slug, summary FROM operator_change_requests
          WHERE status IN ('open','pending')`
      )
      .all<{ id: string; customer_slug: string; summary: string | null }>()
    return res.results
  })
  if (!crRead.ok) {
    // Consistent with the other two import reads: a source we cannot read is a
    // control failure, not an empty source. Swallowing it would let the census
    // conclude "this source produced nothing" when the truth is "we never
    // looked" — the exact conflation the evidence-class split exists to stop.
    console.error('cannot evaluate: operator_change_requests unreadable')
    return EXIT_CANNOT_EVALUATE
  }
  imported.push(...importChangeRequests(crRead.rows, seatsBySlug))

  let importedWritten = 0
  if (!DRY_RUN) {
    for (const row of imported) {
      try {
        await upsertObligation(db, row)
        importedWritten += 1
      } catch {
        /* a single bad row must not stop the sweep; the census reports the shortfall */
      }
    }
  }

  // ---- 2. denominators
  let total = 0
  let universe = 0
  try {
    const counts = await countObligations(db)
    total = counts.total
    universe = counts.universe
  } catch {
    console.error('cannot evaluate: register unreadable')
    return EXIT_CANNOT_EVALUATE
  }

  let highWaterMark = 0
  try {
    highWaterMark = await priorHighWaterMark(db)
  } catch {
    highWaterMark = 0
  }

  if (emptyRegisterIsFailure(total, highWaterMark)) {
    console.error(
      `cannot evaluate: register is empty but a prior run saw ${highWaterMark} rows — selector or data loss`
    )
    return EXIT_CANNOT_EVALUATE
  }

  // ---- 3. probe
  const openRead = await safeAll(async () => listOpenObligations(db))
  if (!openRead.ok) {
    console.error('cannot evaluate: open obligations unreadable')
    return EXIT_CANNOT_EVALUATE
  }

  const findings: string[] = []
  const alerts: AlertInsert[] = []
  let verified = 0
  let overdue = 0
  let cannotProbeable = 0
  let cannotAttested = 0
  let stillOpen = 0
  let stale = 0

  for (const row of openRead.rows) {
    const verdict = classifyRow(row, await probeEvidence(row, db), now)
    if (verdict.verdict === 'verified') {
      if (row.state === 'verified') continue
      verified += 1
      if (!DRY_RUN) {
        const refused = await certify(db, row, runId)
        if (refused) {
          findings.push(
            `::error::${row.obligation_id} ${row.customer_slug} not certified: ${refused}`
          )
        }
      }
    } else if (verdict.verdict === 'overdue') {
      overdue += 1
      // NO `row.what` IN A FINDING. Findings become `reconcile.txt`, which the
      // workflow cats into the Actions log AND into a `gh issue create` body in
      // venturecrane/ss-console -- a PUBLIC repo. `what` is client-confidential:
      // it is a sentence about a named firm's internal backlog, taken verbatim
      // from correspondence in the private engagements repo. This line never
      // fired only because no row carried a due_at; the stale ladder above is
      // what makes it reachable, so the redaction ships in the same change. The
      // obligation id is enough to look the row up with `register list` at a
      // private terminal. Pinned by tests/obligation-reconcile.test.ts, whose
      // fixture text is deliberately synthetic for the same reason.
      findings.push(
        `::warning::${row.obligation_id} ${row.customer_slug}/${row.kind} overdue ${verdict.days}d`
      )
      const alert = alertFor(row, verdict, today)
      if (alert) alerts.push(alert)
    } else if (verdict.verdict === 'stale') {
      stale += 1
      findings.push(
        `::warning::${row.obligation_id} ${row.customer_slug}/${row.kind} open ${verdict.days}d with no due date`
      )
      const alert = alertFor(row, verdict, today)
      if (alert) alerts.push(alert)
    } else if (verdict.verdict === 'cannot_evaluate') {
      if (verdict.class === 'attested') {
        cannotAttested += 1
        findings.push(
          `::warning::${row.obligation_id} ${row.customer_slug} attestation stale — no receipt`
        )
      } else {
        cannotProbeable += 1
        findings.push(
          `::error::${row.obligation_id} ${row.customer_slug} evidence unreadable (probeable) — control broken`
        )
      }
    } else {
      stillOpen += 1
    }
  }

  // ---- 4. certifications no CI run stands behind
  const unwitnessed = await safeAll(async () => findUnwitnessedCertifications(db))
  for (const row of unwitnessed.rows) {
    findings.push(
      `::error::${row.obligation_id} ${row.customer_slug} certified by a run no CI workflow stands behind`
    )
    const alert = alertFor(row, { verdict: 'unverifiable' }, today)
    if (alert) alerts.push(alert)
  }

  // ---- 5. coverage census
  const derivedBySource = new Map<string, number>()
  for (const seat of seats) {
    const perSource = await safeAll(async () => [await countByOriginSource(db, seat.customer_slug)])
    for (const [source, n] of Object.entries(perSource.rows[0] ?? {})) {
      derivedBySource.set(source, (derivedBySource.get(source) ?? 0) + n)
    }
  }

  const artifactCounts = new Map<string, number>()
  for (const row of imported) {
    artifactCounts.set(row.origin_source, (artifactCounts.get(row.origin_source) ?? 0) + 1)
  }

  const censusLines: string[] = []
  let gaps = 0
  for (const [source, artifacts] of artifactCounts) {
    const verdict = censusGap(source, artifacts, derivedBySource.get(source) ?? 0)
    censusLines.push(
      `  ${source.padEnd(24)} artifacts ${String(verdict.artifacts).padStart(4)}  obligations ${String(verdict.obligations).padStart(4)}${verdict.gap ? '   <-- GAP' : ''}`
    )
    if (verdict.gap) {
      gaps += 1
      findings.push(
        `::warning::capture gap: ${source} holds ${verdict.artifacts} artifacts and produced no obligations`
      )
      // A capture gap means the register may have gone quiet on a whole source.
      // Leaving it in the Actions log only would make the ONE control that can
      // catch this design's own silence the one control the Captain never sees.
      // It is raised per seat because the alert key is per entity.
      for (const seat of seats) {
        alerts.push({
          entity_id: seat.entity_id,
          customer_slug: seat.customer_slug,
          alert_date: today,
          driver: `obligation:${source}:obligation_capture_gap`,
          summary: `Capture gap: ${source} holds ${verdict.artifacts} artifacts and produced no obligations.`,
          details_json: JSON.stringify({
            condition: 'obligation_capture_gap',
            source,
            artifacts: verdict.artifacts,
            obligations: verdict.obligations,
            severity: 'warning',
          }),
        })
      }
    }
  }

  // ---- 6. report
  for (const line of [
    '── obligation reconcile summary ──',
    `obligations total:       ${String(total).padStart(5)}`,
    `universe (non-terminal): ${String(universe).padStart(5)}`,
    `  verified this run:     ${String(verified).padStart(5)}`,
    `  still open (in window):${String(stillOpen).padStart(5)}`,
    `  overdue:               ${String(overdue).padStart(5)}`,
    `  stale (undated, ${UNDATED_STALE_DAYS}d+):${String(stale).padStart(4)}`,
    `imported this run:       ${String(importedWritten).padStart(5)} of ${imported.length} derived`,
    `cannot evaluate:         ${String(cannotProbeable + cannotAttested).padStart(5)}  (attested: ${cannotAttested}, probeable: ${cannotProbeable})`,
    `unwitnessed certifications: ${unwitnessed.rows.length}`,
    'coverage census:',
    ...censusLines,
    ...findings,
  ]) {
    console.log(line)
  }

  // Decide the outcome BEFORE recording it. An earlier version stamped the run
  // row with "findings" unless a probe broke, so a converged run was recorded as
  // a finding forever and reconcile_runs could never show a healthy pass — the
  // ledger disagreeing with the process is the failure this whole register is
  // built to prevent, and it does not get an exemption here.
  // `stale` belongs in this sum, and its absence was the whole defect: a
  // verdict that raises an alert but does not count as a finding produces a run
  // that pages the Captain and then records itself CONVERGED. Eleven rows sat
  // untouched behind exactly that arithmetic. An alert without the count is not
  // a fix; it is the bug wearing a notification.
  const findingCount = overdue + stale + cannotAttested + unwitnessed.rows.length + gaps
  const exitCode =
    cannotProbeable > 0 ? EXIT_CANNOT_EVALUATE : findingCount > 0 ? EXIT_FINDINGS : EXIT_CONVERGED

  if (!DRY_RUN) {
    for (const alert of alerts) await writeAlert(db, alert)
    try {
      await finishReconcileRun(db, runId, {
        total,
        universe,
        verified,
        overdue,
        cannotEvaluate: cannotProbeable + cannotAttested,
        exitCode,
      })
    } catch {
      /* the run row is provenance, not the result; a failed stamp must not mask findings */
    }
  }

  // A broken probeable surface is the control failing, not a finding about the
  // work -- it must not be reported as "everything is fine except some rows".
  if (exitCode === EXIT_CANNOT_EVALUATE) {
    console.error(`reconcile: ${cannotProbeable} probeable surface(s) unreadable — control broken`)
    return EXIT_CANNOT_EVALUATE
  }
  if (exitCode === EXIT_FINDINGS) {
    console.log(
      `reconcile findings: ${overdue} overdue, ${stale} stale undated, ${cannotAttested} stale attestations, ${unwitnessed.rows.length} unwitnessed, ${gaps} capture gaps.`
    )
    return EXIT_FINDINGS
  }
  console.log('reconcile: converged.')
  return EXIT_CONVERGED
}

// `sqlLiteral` is re-exported for the CLI, which builds its own upsert without a
// D1 handle; keeping one escaper means one place to get quoting wrong.
export { sqlLiteral }

const invokedDirectly = process.argv[1]?.endsWith('ci-reconcile-obligations.ts')
if (invokedDirectly) {
  main()
    .then((code) => process.exit(code))
    .catch((err: unknown) => {
      console.error(`reconcile: unhandled failure — ${(err as Error)?.message ?? String(err)}`)
      process.exit(EXIT_CANNOT_EVALUATE)
    })
}
