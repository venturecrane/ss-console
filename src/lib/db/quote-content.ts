/**
 * Authored quote content, read back from its JSON columns: line items, the
 * schedule, the deliverables, and the send-readiness check over them. Every
 * parser validates shape and returns an empty array on anything malformed;
 * none throws, none casts.
 *
 * Split out of quotes.ts on 2026-09-11 (review 2026-09-10, Architecture 3):
 * eight importers read these parsers without touching the data layer, and
 * the data layer at 498 logical lines was one addition from the ceiling.
 */

import type { DeliverableRow, LineItem, Quote, ScheduleRow } from './quotes'

/**
 * Type guard for a single persisted line item. Validates the full LineItem
 * shape so malformed or partially-shaped elements are rejected rather than
 * cast through. Mirrors the per-element predicates in parseSchedule /
 * parseDeliverables.
 */
function isLineItem(row: unknown): row is LineItem {
  return (
    row != null &&
    typeof row === 'object' &&
    typeof (row as Record<string, unknown>).problem === 'string' &&
    typeof (row as Record<string, unknown>).description === 'string' &&
    typeof (row as Record<string, unknown>).estimated_hours === 'number'
  )
}

/**
 * Parse a persisted JSON line-items string into validated rows. Returns an
 * empty array when the input is null, missing, malformed, or not an array —
 * never throws, never casts. Each element is validated against the LineItem
 * shape (see isLineItem), so corrupt or shape-changed JSON yields a clean
 * (possibly empty) result instead of an unhandled exception downstream.
 *
 * Use this anywhere a stored `line_items` column is read back. Mirrors
 * parseSchedule / parseDeliverables.
 */
export function parseLineItems(raw: string | null): LineItem[] {
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const rows: unknown[] = parsed
    return rows.filter(isLineItem)
  } catch {
    return []
  }
}

function isRow(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/**
 * Parse the persisted JSON schedule into typed rows. Returns an empty array
 * when the column is null, missing, or malformed — callers should treat
 * "empty" the same as "not authored yet" and render nothing.
 */
export function parseSchedule(quote: Pick<Quote, 'schedule'>): ScheduleRow[] {
  if (!quote.schedule) return []
  try {
    const parsed: unknown = JSON.parse(quote.schedule)
    if (!Array.isArray(parsed)) return []
    const rows: unknown[] = parsed
    return rows.filter(
      (row): row is ScheduleRow =>
        isRow(row) && typeof row.label === 'string' && typeof row.body === 'string'
    )
  } catch {
    return []
  }
}

/**
 * Parse the persisted JSON deliverables into typed rows. Returns an empty
 * array when the column is null, missing, or malformed — see parseSchedule.
 */
export function parseDeliverables(quote: Pick<Quote, 'deliverables'>): DeliverableRow[] {
  if (!quote.deliverables) return []
  try {
    const parsed: unknown = JSON.parse(quote.deliverables)
    if (!Array.isArray(parsed)) return []
    const rows: unknown[] = parsed
    return rows.filter(
      (row): row is DeliverableRow =>
        isRow(row) && typeof row.title === 'string' && typeof row.body === 'string'
    )
  } catch {
    return []
  }
}

/**
 * Validate authored client-facing content before a draft quote can be sent.
 * Returns the list of missing fields; empty array means the quote is ready
 * to send. See #377: a quote without authored schedule + deliverables would
 * be re-rendered with synthesized commitments downstream.
 */
export function getMissingAuthoredContent(quote: Quote): string[] {
  const missing: string[] = []
  if (parseSchedule(quote).length === 0) {
    missing.push('schedule')
  }
  if (parseDeliverables(quote).length === 0) {
    missing.push('deliverables')
  }
  return missing
}
