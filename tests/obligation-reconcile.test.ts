/**
 * The obligation reconciler's controls (ADR 0088).
 *
 * The house pattern from tests/config-reconcile.test.ts: run the real script,
 * with its D1 and gh calls replaced by stubs on disk, and inject faults by env.
 * Testing extracted pure functions alone would prove the logic and miss the
 * thing that actually breaks — a script that exits 0 on a read it never made.
 *
 * Every control here gets a case that would FAIL if the control were removed.
 * The three that carry the design:
 *
 *   - An empty register exits 1 ONCE A PRIOR RUN SAW ROWS, and exits 0 before
 *     that. Both halves matter: the first catches a broken selector, the second
 *     stops the reconciler crying wolf from the day it ships, which is how the
 *     cadence engine trained everyone to ignore it.
 *   - A probeable surface that will not answer is a BROKEN CONTROL (exit 1),
 *     never a finding. "I could not look" and "it is not there" demand opposite
 *     responses, and conflating them is how an unprobeable class grows behind a
 *     green build.
 *   - A source class holding artifacts and producing zero obligations raises a
 *     capture gap. This is the only assertion in the suite that can catch the
 *     register's own silence.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { execFileSync } from 'child_process'

// Every case spawns `npx tsx scripts/ci-reconcile-obligations.ts`, a cold
// TypeScript compile per invocation that takes 3 to 7 seconds depending on
// machine load. vitest's default 5 s timeout is below that cost, so the suite
// went red on a loaded laptop (18 timeouts at 5.0 to 6.7 s in the 2026-09-25
// pre-push verify) while every assertion was true. The timeout is a property
// of the instrument, not of the code under test; size it to the instrument.
vi.setConfig({ testTimeout: 60_000 })
import { mkdtempSync, writeFileSync, rmSync, chmodSync, readFileSync, existsSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'

const SCRIPT = resolve(process.cwd(), 'scripts/ci-reconcile-obligations.ts')

// Every case boots the real script in a fresh subprocess through `npx tsx`
// (the repo's pinned devDependency since 2026-09-25). That is over a second
// idle and several seconds when the machine is busy, which the 5s unit-test
// default does not cover: on 2026-09-25 the file
// failed 12 of 37 cases on timeouts alone under a load average of 12, with
// the same tree passing when the machine was quiet. A subprocess-per-case
// integration file gets an integration budget.
vi.setConfig({ testTimeout: 30_000 })

let dir: string
let statePath: string

/**
 * A stand-in for `wrangler d1 execute`, backed by a JSON file the test mutates.
 * It answers the handful of SELECTs the reconciler issues and records every
 * write, so a test can assert what the run DID, not merely what it printed.
 */
const D1_STUB = `#!/usr/bin/env node
const fs = require('fs')
const state = JSON.parse(fs.readFileSync(process.env.STATE_PATH, 'utf8'))
const sql = process.argv[3] || ''

if (process.env.FAKE_D1_UNREADABLE === '1') { process.exit(3) }
if (process.env.FAKE_D1_GARBAGE === '1') { process.stdout.write('not json'); process.exit(0) }

function out(results) { process.stdout.write(JSON.stringify([{ results, success: true }])) }

if (/^INSERT|^UPDATE/i.test(sql.trim())) {
  state.writes.push(sql)
  // Apply state transitions, so a two-step walk (open -> delivered -> verified)
  // reads its own first step back the way D1 would.
  const moved = /^UPDATE client_obligations SET\\s+state = '(\\w+)'[\\s\\S]*WHERE obligation_id = '([^']+)'/i.exec(sql.trim())
  if (moved) for (const o of state.obligations) if (o.obligation_id === moved[2]) o.state = moved[1]
  fs.writeFileSync(process.env.STATE_PATH, JSON.stringify(state, null, 2))
  return out([])
}
if (/FROM customer_configs/i.test(sql)) return out(state.seats)
if (/FROM fleet_alert_state/i.test(sql) && /status = 'open'/i.test(sql)) return out(state.alert_state)
if (/FROM fleet_alert_state/i.test(sql)) return out(state.alert_probe ?? [])
if (/FROM operator_change_requests/i.test(sql)) {
  if (process.env.FAKE_D1_CR_UNREADABLE === '1') { process.exit(5) }
  return out(state.change_requests ?? [])
}
if (/AS total/i.test(sql)) return out([{ total: state.obligations.length, universe: state.obligations.filter(o => !['closed','cancelled','void'].includes(o.state)).length }])
if (/MAX\\(total_rows\\)/i.test(sql)) return out([{ hwm: state.high_water_mark ?? null }])
if (/LEFT JOIN reconcile_runs/i.test(sql)) return out(state.unwitnessed ?? [])
if (/GROUP BY origin_source/i.test(sql)) return out(state.census ?? [])
if (/FROM client_obligations/i.test(sql)) return out(state.obligations.filter(o => !['closed','cancelled','void'].includes(o.state)))
out([])
`

/**
 * A stand-in for `gh`. The two faults are separate on purpose: an unreachable
 * ISSUE LIST fails during import, an unreachable ISSUE VIEW fails during the
 * per-row probe. Collapsing them into one switch makes the probe test pass for
 * the wrong reason — it exits 1 at import and never reaches the probe at all,
 * which is exactly what the first draft of this stub did.
 */
const GH_STUB = `#!/usr/bin/env node
const fs = require('fs')
const state = JSON.parse(fs.readFileSync(process.env.STATE_PATH, 'utf8'))
const args = process.argv.slice(2)
if (args[1] === 'list') {
  if (process.env.FAKE_GH_LIST_UNREACHABLE === '1') { process.exit(4) }
  process.stdout.write(JSON.stringify(state.github_issues ?? [])); process.exit(0)
}
if (args[1] === 'view') {
  if (process.env.FAKE_GH_VIEW_UNREACHABLE === '1') { process.exit(4) }
  process.stdout.write(state.github_issue_state ?? 'OPEN'); process.exit(0)
}
process.stdout.write('')
`

interface State {
  seats: { customer_slug: string; entity_id: string }[]
  obligations: Record<string, unknown>[]
  alert_state: { customer_slug: string; condition: string }[]
  alert_probe?: { status: string }[]
  change_requests?: Record<string, unknown>[]
  github_issues?: { number: number; title: string }[]
  github_issue_state?: string
  census?: { customer_slug: string; origin_source: string; n: number }[]
  unwitnessed?: Record<string, unknown>[]
  high_water_mark?: number | null
  writes: string[]
}

function setState(state: Partial<State>): void {
  const full: State = {
    seats: [{ customer_slug: 'ashton-price', entity_id: 'e-ap' }],
    obligations: [],
    alert_state: [],
    writes: [],
    ...state,
  }
  writeFileSync(statePath, JSON.stringify(full, null, 2))
}

function readState(): State {
  return JSON.parse(readFileSync(statePath, 'utf8'))
}

function run(env: Record<string, string> = {}): { code: number; stdout: string } {
  try {
    const stdout = execFileSync('npx', ['tsx', SCRIPT], {
      encoding: 'utf8',
      env: {
        ...process.env,
        STATE_PATH: statePath,
        SS_RECONCILE_D1_CMD: join(dir, 'd1-stub.cjs'),
        SS_RECONCILE_GH_CMD: join(dir, 'gh-stub.cjs'),
        ...env,
      },
    })
    return { code: 0, stdout }
  } catch (err) {
    const e = err as { status: number; stdout: string; stderr: string }
    return { code: e.status, stdout: `${e.stdout ?? ''}${e.stderr ?? ''}` }
  }
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'obl-reconcile-'))
  statePath = join(dir, 'state.json')
  for (const [name, body] of [
    ['d1-stub.cjs', D1_STUB],
    ['gh-stub.cjs', GH_STUB],
  ]) {
    const path = join(dir, name)
    writeFileSync(path, body)
    chmodSync(path, 0o755)
  }
  setState({})
})

afterEach(() => rmSync(dir, { recursive: true, force: true }))

describe('the reconciler exists and is wired', () => {
  it('ships the script the workflow invokes', () => {
    expect(existsSync(SCRIPT)).toBe(true)
  })

  it('is actually scheduled, with the bash -e guard the house protocol needs', () => {
    // A reconciler nobody runs is a reconciler that cannot fail. This asserts
    // the workflow exists, has a cron, and keeps the `|| STATUS=$?` guard whose
    // removal silently skips the issue-opening step (ss#2307).
    const workflow = readFileSync(
      resolve(process.cwd(), '.github/workflows/obligation-reconcile.yml'),
      'utf8'
    )
    expect(workflow).toMatch(/schedule:/)
    expect(workflow).toMatch(/cron:/)
    expect(workflow).toMatch(/\|\| STATUS=\$\?/)
    expect(workflow).toMatch(/reconcile-series:/)
  })
})

describe('denominators and the empty register', () => {
  it('exits 0 on an empty register before any run has seen rows', () => {
    setState({ high_water_mark: null })
    const result = run()
    expect(result.code).toBe(0)
    expect(result.stdout).toMatch(/obligations total:\s+0/)
  })

  it('exits 1 on an empty register once a prior run saw rows', () => {
    // The broken-selector case. Without the high-water mark this is
    // indistinguishable from a quiet week.
    setState({ high_water_mark: 42 })
    const result = run()
    expect(result.code).toBe(1)
    expect(result.stdout).toMatch(/prior run saw 42 rows/)
  })

  it('prints both denominators, not one', () => {
    setState({
      obligations: [
        {
          obligation_id: 'o1',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'request',
          what: 'x',
          state: 'open',
          evidence_surface: null,
          evidence_locator: null,
        },
      ],
    })
    const result = run()
    expect(result.stdout).toMatch(/obligations total:\s+1/)
    expect(result.stdout).toMatch(/universe \(non-terminal\):\s+1/)
  })

  it('exits 1 when D1 cannot be read at all', () => {
    const result = run({ FAKE_D1_UNREADABLE: '1' })
    expect(result.code).toBe(1)
    expect(result.stdout).toMatch(/cannot evaluate/)
  })

  it('exits 1 rather than 0 when D1 returns garbage', () => {
    // Falsifier: an unparseable response must not read as an empty result set,
    // which would look exactly like a converged run.
    const result = run({ FAKE_D1_GARBAGE: '1' })
    expect(result.code).toBe(1)
  })

  it('exits 1 when there are no seats, instead of reporting converged', () => {
    setState({ seats: [] })
    const result = run()
    expect(result.code).toBe(1)
  })
})

describe('findings', () => {
  const overdueRow = {
    obligation_id: 'o-late',
    customer_slug: 'ashton-price',
    entity_id: 'e-ap',
    kind: 'deliverable',
    what: 'Send the signature copies.',
    state: 'active',
    due_at: '2026-01-01',
    evidence_surface: null,
    evidence_locator: null,
    evidence_class: 'probeable',
  }

  it('exits 2 and names the overdue row', () => {
    setState({ obligations: [overdueRow] })
    const result = run()
    expect(result.code).toBe(2)
    expect(result.stdout).toMatch(/overdue/)
    expect(result.stdout).toMatch(/o-late/)
  })

  it('writes an alert row keyed per obligation, not per client', () => {
    // Two overdue obligations for one client on one day must stay two rows:
    // the composite key would otherwise collapse them and the second would
    // vanish.
    setState({
      obligations: [
        overdueRow,
        { ...overdueRow, obligation_id: 'o-late-2', what: 'Second thing.' },
      ],
    })
    run()
    const alertWrites = readState().writes.filter((w) => w.includes('cost_anomaly_alerts'))
    expect(alertWrites).toHaveLength(2)
    expect(alertWrites[0]).toContain("'obligation'")
    expect(alertWrites[0]).toContain('obligation:o-late:obligation_overdue')
    expect(alertWrites[1]).toContain('obligation:o-late-2:obligation_overdue')
  })

  it('does not alarm on an undated obligation', () => {
    // Quote-grounding protects the citation, not the interpretation, so a row
    // with no grounded date must never page anyone.
    setState({ obligations: [{ ...overdueRow, due_at: null }] })
    const result = run()
    expect(result.code).toBe(0)
    expect(readState().writes.filter((w) => w.includes('cost_anomaly_alerts'))).toHaveLength(0)
  })

  it('flags a certification no CI workflow stands behind', () => {
    setState({
      unwitnessed: [
        {
          obligation_id: 'o-forged',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'deliverable',
          what: 'Closed by hand.',
          due_at: null,
          source_ref: 'x',
        },
      ],
    })
    const result = run()
    expect(result.code).toBe(2)
    expect(result.stdout).toMatch(/no CI workflow stands behind/)
  })
})

describe('cannot-evaluate splits by evidence class', () => {
  it('treats an unreadable probeable surface as a broken control, exit 1', () => {
    setState({
      obligations: [
        {
          obligation_id: 'o-probe',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'product_defect',
          what: 'Blocked by a defect.',
          state: 'active',
          due_at: null,
          evidence_class: 'probeable',
          evidence_surface: 'github',
          evidence_locator: 'venturecrane/ss-console#999',
        },
      ],
    })
    // The LIST call must still succeed, or this exits during import and never
    // reaches the probe it is meant to test.
    const result = run({ FAKE_GH_VIEW_UNREACHABLE: '1' })
    expect(result.code).toBe(1)
    expect(result.stdout).toMatch(/control broken/)
    expect(result.stdout).toMatch(/attested: 0, probeable: 1/)
  })

  it('treats a missing attestation as a finding, exit 2', () => {
    // The Smokeball class: CI holds no credential for it, so a stale receipt is
    // something to report, not a broken control.
    setState({
      obligations: [
        {
          obligation_id: 'o-attest',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'deliverable',
          what: 'Filed into Smokeball.',
          state: 'delivered',
          due_at: null,
          evidence_class: 'attested',
          evidence_surface: 'smokeball',
          evidence_locator: 'matter/m-0001/doc/7',
          evidence_last_verified_at: null,
        },
      ],
    })
    const result = run()
    expect(result.code).toBe(2)
    expect(result.stdout).toMatch(/attested: 1, probeable: 0/)
  })
})

describe('import', () => {
  it('derives obligations from client-labelled GitHub issues', () => {
    setState({ github_issues: [{ number: 2794, title: 'Chronology gate rejects a scanned PDF' }] })
    run()
    const writes = readState().writes.filter((w) => w.includes('client_obligations'))
    expect(writes.some((w) => w.includes('gh-2794'))).toBe(true)
    expect(writes.some((w) => w.includes('product_defect'))).toBe(true)
  })

  it('derives a renewal from an expiring connector token', () => {
    setState({
      alert_state: [
        { customer_slug: 'ashton-price', condition: 'connector_token_expiring:smokeball' },
      ],
    })
    run()
    const writes = readState().writes.filter((w) => w.includes('client_obligations'))
    expect(writes.some((w) => w.includes("'renewal'"))).toBe(true)
    expect(writes.some((w) => w.includes('smokeball'))).toBe(true)
  })

  it('derives an incident from any other open seat condition', () => {
    setState({ alert_state: [{ customer_slug: 'ashton-price', condition: 'scheduler_error' }] })
    run()
    const writes = readState().writes.filter((w) => w.includes('client_obligations'))
    expect(writes.some((w) => w.includes("'incident'"))).toBe(true)
  })

  it('re-import upserts instead of duplicating', () => {
    setState({ github_issues: [{ number: 2794, title: 'Same issue' }] })
    run()
    const writes = readState().writes.filter((w) => w.includes('client_obligations'))
    expect(writes[0]).toContain('ON CONFLICT(customer_slug, kind, stable_key) DO UPDATE SET')
    // State is deliberately absent from the update list: re-importing proves an
    // obligation is still stated, and must never reopen one somebody closed.
    expect(writes[0]).not.toContain('state =')
  })

  it('exits 1 when GitHub cannot be read, rather than importing nothing quietly', () => {
    const result = run({ FAKE_GH_LIST_UNREACHABLE: '1' })
    expect(result.code).toBe(1)
  })
})

describe('the coverage census', () => {
  it('raises a gap when a source holds artifacts and produced no obligations', () => {
    // The register's own silence, made visible. Without this, capture could
    // stop entirely and every other control would still report healthy.
    setState({
      github_issues: [
        { number: 1, title: 'a' },
        { number: 2, title: 'b' },
      ],
      census: [],
    })
    const result = run()
    expect(result.stdout).toMatch(/GAP/)
    expect(result.stdout).toMatch(/capture gap: github holds 2 artifacts/)
    expect(result.code).toBe(2)
  })

  it('does not raise a gap when the source is represented', () => {
    // Falsifier for the census: if it flagged regardless of counts, the test
    // above would pass while the control was meaningless.
    setState({
      github_issues: [{ number: 1, title: 'a' }],
      census: [{ customer_slug: 'ashton-price', origin_source: 'github', n: 1 }],
    })
    const result = run()
    expect(result.stdout).not.toMatch(/GAP/)
  })

  it('prints the census with both sides of every comparison', () => {
    setState({
      github_issues: [{ number: 1, title: 'a' }],
      census: [{ customer_slug: 'ashton-price', origin_source: 'github', n: 1 }],
    })
    const result = run()
    expect(result.stdout).toMatch(/coverage census:/)
    expect(result.stdout).toMatch(/github\s+artifacts\s+1\s+obligations\s+1/)
  })
})

describe('dry run', () => {
  it('classifies and reports without writing anything', () => {
    setState({
      obligations: [
        {
          obligation_id: 'o1',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'deliverable',
          what: 'x',
          state: 'active',
          due_at: '2026-01-01',
          evidence_class: 'probeable',
          evidence_surface: null,
          evidence_locator: null,
        },
      ],
      github_issues: [{ number: 7, title: 'y' }],
    })
    const result = run({ SS_RECONCILE_DRY_RUN: '1' })
    expect(result.code).toBe(2)
    expect(readState().writes).toHaveLength(0)
  })
})

describe('regressions found in review', () => {
  it('records a converged run as converged, not as findings', () => {
    // The run row was stamped before the convergence check, so a healthy pass
    // was written to reconcile_runs as exit_code 2 forever. A ledger that
    // disagrees with the process is the failure this register exists to end;
    // it does not get an exemption in the register's own history table.
    setState({
      census: [{ customer_slug: 'ashton-price', origin_source: 'github', n: 1 }],
      github_issues: [{ number: 1, title: 'a' }],
    })
    const result = run()
    expect(result.code).toBe(0)

    const stamp = readState().writes.find((w) => w.includes('UPDATE reconcile_runs'))
    expect(stamp).toBeTruthy()
    expect(stamp).toMatch(/exit_code\s*=\s*0/)
  })

  it('records a findings run as findings', () => {
    // Falsifier for the assertion above: if exit_code were hardcoded either
    // way, one of these two tests would fail.
    setState({
      obligations: [
        {
          obligation_id: 'o-late',
          customer_slug: 'ashton-price',
          entity_id: 'e-ap',
          kind: 'deliverable',
          what: 'late thing',
          state: 'active',
          due_at: '2026-01-01',
          evidence_class: 'probeable',
          evidence_surface: null,
          evidence_locator: null,
        },
      ],
    })
    const result = run()
    expect(result.code).toBe(2)
    const stamp = readState().writes.find((w) => w.includes('UPDATE reconcile_runs'))
    expect(stamp).toMatch(/exit_code\s*=\s*2/)
  })

  it('raises a capture gap as an ALERT, not only as a log line', () => {
    // The handbook and the migration header both say a capture gap reaches the
    // Captain. It previously only reached the Actions log, which would have made
    // the one control that catches this design's own silence the one control he
    // never sees.
    setState({ github_issues: [{ number: 1, title: 'a' }], census: [] })
    const result = run()
    expect(result.code).toBe(2)

    const alertWrites = readState().writes.filter((w) => w.includes('cost_anomaly_alerts'))
    expect(alertWrites.some((w) => w.includes('obligation_capture_gap'))).toBe(true)
  })

  it('treats an unreadable change-request source as a control failure', () => {
    // Its two sibling import reads escalate; this one used to swallow the
    // error, which would let the census read "this source produced nothing"
    // when the truth was "we never looked".
    setState({})
    const result = run({ FAKE_D1_CR_UNREADABLE: '1' })
    expect(result.code).toBe(1)
    expect(result.stdout).toMatch(/operator_change_requests unreadable/)
  })
})

/** A SQLite-shaped UTC timestamp N days back, matching CURRENT_TIMESTAMP. */
function daysAgo(n: number): string {
  return new Date(Date.now() - n * 86400000).toISOString().replace('T', ' ').slice(0, 19)
}

/**
 * The stale ladder and the confidentiality split (2026-09-17).
 *
 * Both exist because of a defect the register shipped with: an undated row that
 * stayed open classified `still_open`, `still_open` was counted in no total, and
 * the nightly run therefore exited CONVERGED over eleven rows nothing was
 * moving. That is the predecessor cadence engine's failure -- 7 of 16 items
 * overdue, one by 134 days, reporting itself healthy -- rebuilt inside its own
 * replacement.
 *
 * The pairing in the first test is the point. A verdict that raises an alert but
 * is missing from `findingCount` produces a run that pages the Captain AND
 * records itself converged, so asserting the alert alone would pass against the
 * exact bug being fixed.
 */
describe('the stale ladder for undated obligations', () => {
  const captured = (over: Record<string, unknown> = {}) => ({
    obligation_id: 'o-stale',
    customer_slug: 'ashton-price',
    entity_id: 'e-ap',
    kind: 'deliverable',
    what: 'a promise made in a letter',
    state: 'open',
    origin: 'captured',
    due_at: null,
    created_at: daysAgo(45),
    evidence_surface: null,
    evidence_locator: null,
    ...over,
  })

  it('a 45-day undated captured row alerts AND counts as a finding', () => {
    setState({ obligations: [captured()] })
    const result = run()
    // Both halves, deliberately. Removing `stale` from the findingCount sum
    // leaves the alert assertion passing and fails only the exit code.
    expect(result.stdout).toMatch(/open 45d with no due date/)
    expect(result.code).toBe(2)
    expect(readState().writes.some((w) => w.includes('obligation_stale'))).toBe(true)
  })

  it('a 5-day undated captured row is not a finding', () => {
    // Falsifier for the ladder: a threshold pinned at 0 days would pass every
    // assertion above and this is the only case that would catch it.
    setState({ obligations: [captured({ obligation_id: 'o-new', created_at: daysAgo(5) })] })
    const result = run()
    expect(result.code).toBe(0)
    expect(result.stdout).not.toMatch(/with no due date/)
  })

  it('an IMPORTED undated row never goes stale, however old', () => {
    // A derived GitHub row probes `absent` for as long as its issue is merely
    // open, so an unscoped ladder pages on ordinary backlog -- 51 open issues
    // at time of writing. Dropping the origin scope fails this.
    setState({
      obligations: [
        captured({ obligation_id: 'o-imp', origin: 'imported', created_at: daysAgo(400) }),
      ],
    })
    const result = run()
    expect(result.code).toBe(0)
    expect(result.stdout).not.toMatch(/with no due date/)
  })
})

describe('findings never carry client text', () => {
  // Findings become reconcile.txt, which obligation-reconcile.yml cats into the
  // public Actions log AND into a `gh issue create` body in
  // venturecrane/ss-console -- a PUBLIC repo. `what` names a real firm's real
  // backlog. The ALERT summary keeps it, because that path is Resend to
  // team@smd.services and the admin console. The findings list must not.
  // Interpolating row.what into either finding line fails these.
  const secret = 'Reconcile the synthetic widget ledger for the fixture firm'

  const row = (over: Record<string, unknown>) => ({
    obligation_id: 'o-x',
    customer_slug: 'ashton-price',
    entity_id: 'e-ap',
    kind: 'deliverable',
    what: secret,
    state: 'open',
    origin: 'captured',
    due_at: null,
    created_at: daysAgo(40),
    evidence_surface: null,
    evidence_locator: null,
    ...over,
  })

  it('an overdue finding names the row but not what it says', () => {
    setState({ obligations: [row({ due_at: daysAgo(9).slice(0, 10) })] })
    const result = run()
    expect(result.code).toBe(2)
    expect(result.stdout).toMatch(/overdue 9d/)
    expect(result.stdout).not.toContain(secret)
  })

  it('a stale finding names the row but not what it says', () => {
    setState({ obligations: [row({ created_at: daysAgo(90) })] })
    const result = run()
    expect(result.code).toBe(2)
    expect(result.stdout).not.toContain(secret)
  })
})

describe('closing: a row whose evidence reads true actually moves', () => {
  // Until 2026-09-19 nothing ever moved a row out of `open`. The run counted
  // imported rows whose source had cleared as "verified this run" and left them
  // open, and captured rows had no evidence pointer at all. Every assertion here
  // reads the WRITES, because the printed count is exactly what lied before.
  const obligationWrites = () =>
    readState().writes.filter((w) => /^UPDATE client_obligations/i.test(w.trim()))
  const statesWritten = () => obligationWrites().map((w) => /state = '(\w+)'/.exec(w)?.[1])

  const row = (over: Record<string, unknown>) => ({
    obligation_id: 'o1',
    customer_slug: 'ashton-price',
    entity_id: 'e-ap',
    kind: 'incident',
    what: 'synthetic',
    due_at: null,
    created_at: '2026-09-18 00:00:00',
    ...over,
  })

  const clearedAlertRow = (state: string) =>
    row({
      origin: 'imported',
      state,
      evidence_class: 'probeable',
      evidence_surface: 'fleet_alert_state',
      evidence_locator: 'ashton-price:scheduler_error',
    })

  it('walks an imported row whose source cleared through delivered to verified', () => {
    setState({ obligations: [clearedAlertRow('open')], alert_probe: [] })
    run()
    expect(statesWritten()).toEqual(['delivered', 'verified'])
    expect(readState().obligations[0].state).toBe('verified')
  })

  it('leaves an imported row open while its source still stands', () => {
    // The mirror: a closer that closed everything would pass the test above.
    setState({ obligations: [clearedAlertRow('open')], alert_probe: [{ status: 'open' }] })
    run()
    expect(obligationWrites()).toHaveLength(0)
    expect(readState().obligations[0].state).toBe('open')
  })

  it('certifies a delivered letter row from the receipt register deliver leaves', () => {
    setState({
      obligations: [
        row({
          origin: 'captured',
          kind: 'deliverable',
          state: 'delivered',
          evidence_class: 'attested',
          evidence_surface: 'engagements',
          evidence_locator: 'venturecrane/engagements@0123456789ab:operator/x.md',
          evidence_last_verified_at: '2026-09-19 12:00:00',
        }),
      ],
    })
    run()
    expect(statesWritten()).toEqual(['verified'])
  })

  it('never walks a captured row: only register deliver moves it', () => {
    // A captured row with no evidence probes absent and stays open, however
    // complete the work is. That is the gap `register deliver` fills; the
    // reconciler must not paper over it by promoting on its own.
    setState({
      obligations: [
        row({ origin: 'captured', kind: 'deliverable', state: 'open', evidence_locator: null }),
      ],
    })
    run()
    expect(obligationWrites()).toHaveLength(0)
  })

  const changeRequestRow = (over: Record<string, unknown> = {}) =>
    row({
      origin: 'imported',
      kind: 'external_dependency',
      state: 'open',
      source_ref: 'operator_change_requests:3',
      evidence_class: 'probeable',
      evidence_surface: 'd1',
      evidence_locator: 'operator_change_requests:3',
      ...over,
    })

  it('cancels, not verifies, an imported row whose change request was declined', () => {
    // A declined request is settled but nothing was delivered. Walking it to
    // verified would record work nobody did; leaving it open kept it owed
    // forever, which is what cr-3 did for 72 days.
    setState({ obligations: [changeRequestRow()], change_requests: [{ status: 'declined' }] })
    const result = run()
    expect(statesWritten()).toEqual(['cancelled'])
    expect(readState().obligations[0].state).toBe('cancelled')
    expect(obligationWrites()[0]).toMatch(/source withdrew the ask: operator_change_requests:3/)
    expect(result.stdout).toMatch(/withdrawn this run:\s+1/)
  })

  it('still verifies an imported row whose change request was resolved', () => {
    setState({ obligations: [changeRequestRow()], change_requests: [{ status: 'resolved' }] })
    run()
    expect(statesWritten()).toEqual(['delivered', 'verified'])
  })

  it('leaves an imported row open while its change request is open', () => {
    setState({ obligations: [changeRequestRow()], change_requests: [{ status: 'open' }] })
    run()
    expect(obligationWrites()).toHaveLength(0)
  })

  it('never cancels a captured row on a withdrawn probe', () => {
    setState({
      obligations: [changeRequestRow({ origin: 'captured' })],
      change_requests: [{ status: 'declined' }],
    })
    const result = run()
    expect(obligationWrites()).toHaveLength(0)
    expect(result.stdout).toMatch(/not cancelled: a captured row probed withdrawn/)
  })

  it('does not recount a row that is already verified', () => {
    const verified = { ...clearedAlertRow('verified'), reconcile_run_id: 'r0' }
    setState({ obligations: [verified], alert_probe: [] })
    const result = run()
    expect(obligationWrites()).toHaveLength(0)
    expect(result.stdout).toMatch(/verified this run:\s+0/)
  })
})
