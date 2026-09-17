/**
 * View derivations for the obligation register's admin surfaces (ADR 0088).
 *
 * Same division of labour as fleet-alerts.ts: the reader and the writers are
 * the frozen seam, and this module owns only the pure transformations — status
 * tone, urgency, grouping, filtering, counts. Pages do no arithmetic.
 *
 * NOTHING HERE INVENTS TEXT. Every string a page renders is either a column
 * from the row or a label for a state the row is actually in. Where a field is
 * absent the caller renders nothing, per docs/style/empty-state-pattern.md.
 * This register is internal, but the rule is absolute in this repo and an
 * internal surface that fabricates trains the same habit as one that does not.
 */

import type { Obligation, ObligationState } from '../db/obligations'

export type Urgency = 'overdue' | 'due-soon' | 'scheduled' | 'undated'

/** Display labels for each state. Internal vocabulary, stated once. */
export const STATE_LABELS: Record<ObligationState, string> = {
  open: 'Open',
  active: 'In progress',
  awaiting_external: 'Waiting on them',
  delivered: 'Delivered, unproven',
  verified: 'Verified',
  closed: 'Closed',
  parked: 'Parked',
  cancelled: 'Cancelled',
  void: 'Void',
}

/**
 * `delivered` reads as a caution rather than a success on purpose: it means we
 * believe the work is done and no probe has confirmed it. That gap is exactly
 * where the medchron job sat while its ledger said `failed`, and a green pill
 * would paper over the one state most worth looking at.
 */
export const STATE_TONE: Record<ObligationState, 'neutral' | 'good' | 'warn' | 'bad'> = {
  open: 'neutral',
  active: 'neutral',
  awaiting_external: 'warn',
  delivered: 'warn',
  verified: 'good',
  closed: 'good',
  parked: 'bad',
  cancelled: 'neutral',
  void: 'neutral',
}

export interface ObligationRow extends Obligation {
  urgency: Urgency
  daysOverdue: number | null
  stateLabel: string
  tone: 'neutral' | 'good' | 'warn' | 'bad'
}

/**
 * Urgency from the due date alone.
 *
 * Undated is its own bucket, never folded into "scheduled": an undated
 * obligation is real work with no clock, and showing it as on-track would be a
 * claim the row does not support.
 */
export function urgencyOf(dueAt: string | null, now: Date): Urgency {
  if (!dueAt) return 'undated'
  const due = new Date(dueAt)
  if (Number.isNaN(due.getTime())) return 'undated'
  const days = Math.floor((due.getTime() - now.getTime()) / 86400000)
  if (days < 0) return 'overdue'
  if (days <= 7) return 'due-soon'
  return 'scheduled'
}

export function daysOverdue(dueAt: string | null, now: Date): number | null {
  if (!dueAt) return null
  const due = new Date(dueAt)
  if (Number.isNaN(due.getTime())) return null
  const days = Math.floor((now.getTime() - due.getTime()) / 86400000)
  return days > 0 ? days : null
}

export function decorate(rows: readonly Obligation[], now: Date): ObligationRow[] {
  return rows.map((row) => ({
    ...row,
    urgency: urgencyOf(row.due_at, now),
    daysOverdue: daysOverdue(row.due_at, now),
    stateLabel: STATE_LABELS[row.state] ?? row.state,
    tone: STATE_TONE[row.state] ?? 'neutral',
  }))
}

const URGENCY_RANK: Record<Urgency, number> = {
  overdue: 3,
  'due-soon': 2,
  scheduled: 1,
  undated: 0,
}

/** Worst first, then soonest. The eye lands on what is already late. */
export function sortObligations(rows: readonly ObligationRow[]): ObligationRow[] {
  return [...rows].sort((a, b) => {
    const rank = URGENCY_RANK[b.urgency] - URGENCY_RANK[a.urgency]
    if (rank !== 0) return rank
    if (a.due_at && b.due_at) return a.due_at.localeCompare(b.due_at)
    return a.customer_slug.localeCompare(b.customer_slug)
  })
}

export interface ObligationFilters {
  customer?: string | null
  kind?: string | null
  urgency?: string | null
}

/** An empty or 'all' filter value matches everything — the fleet-alerts idiom. */
function normalize(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const trimmed = value.trim()
  return trimmed === '' || trimmed === 'all' ? null : trimmed
}

export function filterObligations(
  rows: readonly ObligationRow[],
  filters: ObligationFilters
): ObligationRow[] {
  const customer = normalize(filters.customer)
  const kind = normalize(filters.kind)
  const urgency = normalize(filters.urgency)
  return rows.filter((row) => {
    if (customer && row.customer_slug !== customer) return false
    if (kind && row.kind !== kind) return false
    if (urgency && row.urgency !== urgency) return false
    return true
  })
}

export interface ObligationCounts {
  overdue: number
  dueSoon: number
  scheduled: number
  undated: number
  total: number
}

export function countByUrgency(rows: readonly ObligationRow[]): ObligationCounts {
  const counts: ObligationCounts = {
    overdue: 0,
    dueSoon: 0,
    scheduled: 0,
    undated: 0,
    total: rows.length,
  }
  for (const row of rows) {
    if (row.urgency === 'overdue') counts.overdue += 1
    else if (row.urgency === 'due-soon') counts.dueSoon += 1
    else if (row.urgency === 'scheduled') counts.scheduled += 1
    else counts.undated += 1
  }
  return counts
}

export function distinctCustomers(rows: readonly ObligationRow[]): string[] {
  return [...new Set(rows.map((r) => r.customer_slug))].sort()
}

/**
 * What would close this obligation, in the Captain's words rather than the
 * schema's.
 *
 * Returns null when the row carries no evidence pointer — the page then renders
 * nothing rather than a confident-sounding placeholder. "Unknown" printed in a
 * column labelled "closes when" is a claim, and it is the wrong one.
 */
export function closesWhen(row: Obligation): string | null {
  if (!row.evidence_surface) return null
  const attested = row.evidence_class === 'attested'
  switch (row.evidence_surface) {
    case 'github':
      return `the issue closes and the client can do it again (${row.evidence_locator})`
    case 'fleet_alert_state':
      return 'the seat condition clears'
    case 'd1':
      return 'the request is resolved'
    case 'smokeball':
      return attested
        ? 'the seat confirms the filing (attested — CI cannot see Smokeball)'
        : 'the filing is observed'
    case 'r2':
      return `the artifact is present (${row.evidence_locator})`
    case 'mailbox':
      return 'the reply is observed in the mailbox'
    default:
      return attested ? `the seat confirms it (${row.evidence_surface})` : row.evidence_surface
  }
}

/** Group a fleet-wide list by client for the "what's on our plate" view. */
export function groupByCustomer(rows: readonly ObligationRow[]): Map<string, ObligationRow[]> {
  const grouped = new Map<string, ObligationRow[]>()
  for (const row of rows) {
    const list = grouped.get(row.customer_slug)
    if (list) list.push(row)
    else grouped.set(row.customer_slug, [row])
  }
  return grouped
}
