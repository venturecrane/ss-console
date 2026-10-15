/**
 * Billing view composition (ADR 0046).
 *
 * The bi-modal money surface. One-time totals are fully derived from
 * invoices. The recurring side is honest about the schema gap: there is
 * no recurring-price column yet (Operator pricing is doctrinally
 * deferred), so this module exposes a real *count* of active operator
 * subscriptions and never fabricates an MRR dollar figure.
 */

import type { Invoice } from '../db/invoices'
import { computeBillingRollup } from './client-hub'

/** Worklist ordering: money owed first, settled last. */
const STATUS_ORDER: Record<string, number> = {
  overdue: 0,
  sent: 1,
  draft: 2,
  paid: 3,
  void: 4,
}

export function sortInvoicesForBilling(invoices: Invoice[]): Invoice[] {
  return [...invoices].sort(
    (a, b) =>
      (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) ||
      b.created_at.localeCompare(a.created_at)
  )
}

export interface OneTimeTotals {
  invoiced: number
  paid: number
  outstanding: number
  overdueCount: number
  overdueAmount: number
}

export interface OperatorRecurring {
  /** Real MRR: each priced commercial line reached from the roster, counted once. */
  mrr: number
  /** Authoritative count of active operator seats (from the roster, never the spine). */
  activeCount: number
  /** Roster seats with no price authored yet — surfaced, never summed as 0. */
  unpricedCount: number
}

/** The commercial line one seat is priced by: its service id and price. */
export interface SeatLine {
  serviceId: string
  price: number | null
}

/**
 * Seat (customer_slug) → the ACTIVE operator service it is priced by
 * (migration 0126). Seats with no service, or a non-active one, are absent,
 * so they read as unpriced. Keyed by seat, never by entity: a client's two
 * seats can carry two prices.
 */
export function seatLines(
  seats: ReadonlyArray<{
    instance_slug: string | null
    service_id: string | null
    recurring_price: number | null
    service_status: string | null
  }>
): Map<string, SeatLine> {
  const map = new Map<string, SeatLine>()
  for (const s of seats) {
    if (!s.instance_slug || !s.service_id || s.service_status !== 'active') continue
    map.set(s.instance_slug, { serviceId: s.service_id, price: s.recurring_price })
  }
  return map
}

/**
 * Recurring revenue, computed the safe way (ADR 0046; migration 0126): iterate
 * the AUTHORITATIVE operator roster (every live seat), and price each seat from
 * the spine via `lineBySeat`. A roster seat with no priced line is counted
 * `unpriced` — never dropped, never silently summed as 0. MRR sums each
 * DISTINCT service once: seats sharing a line (SMD's own seats) do not
 * multiply its price.
 */
export function operatorRecurring(
  rosterSeatSlugs: string[],
  lineBySeat: Map<string, SeatLine>
): OperatorRecurring {
  const counted = new Set<string>()
  let mrr = 0
  let unpricedCount = 0
  for (const slug of rosterSeatSlugs) {
    const line = lineBySeat.get(slug)
    if (!line || line.price == null) {
      unpricedCount += 1
      continue
    }
    if (counted.has(line.serviceId)) continue
    counted.add(line.serviceId)
    mrr += line.price
  }
  return { mrr, activeCount: rosterSeatSlugs.length, unpricedCount }
}

export function oneTimeTotals(invoices: Invoice[]): OneTimeTotals {
  const base = computeBillingRollup(invoices)
  let overdueCount = 0
  let overdueAmount = 0
  for (const inv of invoices) {
    if (inv.status === 'overdue') {
      overdueCount += 1
      overdueAmount += inv.amount
    }
  }
  return {
    invoiced: base.invoiced,
    paid: base.paid,
    outstanding: base.outstanding,
    overdueCount,
    overdueAmount,
  }
}
