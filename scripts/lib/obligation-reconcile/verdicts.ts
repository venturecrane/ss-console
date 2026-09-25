/**
 * The obligation reconciler's pure logic: what a probe result means, how severe
 * a finding is, when silence is a failure, and the alert row a finding becomes.
 *
 * Split out of scripts/ci-reconcile-obligations.ts (2026-09-25) when lint
 * reached scripts/ and found the reconciler at 598 logical lines with a `main`
 * of 249 lines and complexity 50. No I/O lives here, so every rule is testable
 * without a database, a network, or a subprocess.
 */

import { ageInDays } from '../sqlite-time.mjs'
import type { Obligation } from '../../../src/lib/db/obligations'

export type Verdict =
  | { verdict: 'verified' }
  | { verdict: 'withdrawn' }
  | { verdict: 'still_open' }
  | { verdict: 'overdue'; days: number }
  | { verdict: 'stale'; days: number }
  | { verdict: 'cannot_evaluate'; class: 'probeable' | 'attested' }
  | { verdict: 'unverifiable' }

/**
 * `withdrawn` is the source saying nothing is owed any more (a declined change
 * request), as distinct from `present` (the work was done). The two end in
 * different states -- `cancelled` versus `verified` -- because recording a
 * refusal as a delivery would put work in the ledger that nobody did.
 */
export type ProbeResult = { status: 'present' | 'withdrawn' | 'absent' | 'unreachable' }

/** The age at which an undated, still-open captured obligation becomes a finding. */
export const UNDATED_STALE_DAYS = 30

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
  if (probe.status === 'withdrawn') return { verdict: 'withdrawn' }
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

export interface AlertInsert {
  entity_id: string
  customer_slug: string
  alert_date: string
  driver: string
  summary: string
  details_json: string
}

type AlertingVerdict = Extract<Verdict, { verdict: 'overdue' | 'stale' | 'unverifiable' }>

const ALERT_CONDITION: Record<AlertingVerdict['verdict'], string> = {
  overdue: 'obligation_overdue',
  stale: 'obligation_stale',
  unverifiable: 'obligation_unverifiable',
}

function isAlerting(verdict: Verdict): verdict is AlertingVerdict {
  return verdict.verdict in ALERT_CONDITION
}

function alertSummary(row: Obligation, verdict: AlertingVerdict): string {
  if (verdict.verdict === 'overdue') return `Overdue ${verdict.days}d: ${row.what}`
  if (verdict.verdict === 'stale') return `Open ${verdict.days}d with no due date: ${row.what}`
  return `Certification not witnessed by a CI run: ${row.what}`
}

function alertSeverity(verdict: AlertingVerdict): 'warning' | 'critical' {
  if (verdict.verdict === 'overdue') return overdueSeverity(verdict.days)
  if (verdict.verdict === 'stale') return undatedAgeSeverity(verdict.days)
  return 'critical'
}

export function alertFor(row: Obligation, verdict: Verdict, today: string): AlertInsert | null {
  if (!isAlerting(verdict)) return null
  const condition = ALERT_CONDITION[verdict.verdict]
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
    summary: alertSummary(row, verdict),
    details_json: JSON.stringify({
      obligation_id: row.obligation_id,
      kind: row.kind,
      due_at: row.due_at,
      severity: alertSeverity(verdict),
      source_ref: row.source_ref,
    }),
  }
}
