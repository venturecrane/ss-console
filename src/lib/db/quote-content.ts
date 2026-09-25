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
  if (!isRow(row)) return false
  const hours = row.estimated_hours
  return (
    typeof row.problem === 'string' &&
    typeof row.description === 'string' &&
    typeof hours === 'number' &&
    Number.isFinite(hours) &&
    hours >= 0
  )
}

/** Decode the stored column into its raw rows, or say why it cannot be. */
function decodeLineItemRows(raw: string | null): unknown[] | LineItemsRefusal {
  if (!raw) return { ok: false, reason: 'missing' }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, reason: 'malformed_json' }
  }
  return Array.isArray(parsed) ? parsed : { ok: false, reason: 'not_an_array' }
}

/**
 * Parse a persisted JSON line-items string into validated rows. Returns an
 * empty array when the input is null, missing, malformed, or not an array;
 * never throws, never casts. Each element is validated against the LineItem
 * shape (see isLineItem), so corrupt or shape-changed JSON yields a clean
 * (possibly empty) result instead of an unhandled exception downstream.
 *
 * Use this anywhere a stored `line_items` column is read back for display.
 * Mirrors parseSchedule / parseDeliverables. A caller that turns line items
 * into money or into one milestone per row uses readLineItemsExact instead,
 * because dropping a row there shifts every row after it.
 */
export function parseLineItems(raw: string | null): LineItem[] {
  const rows = decodeLineItemRows(raw)
  return Array.isArray(rows) ? rows.filter(isLineItem) : []
}

export interface LineItemsRefusal {
  ok: false
  reason: 'missing' | 'malformed_json' | 'not_an_array' | 'invalid_row'
  /** The first row that failed the LineItem shape, when reason is invalid_row. */
  index?: number
}

export type LineItemsRead = { ok: true; items: LineItem[] } | LineItemsRefusal

/**
 * The all-or-nothing reading of the same column, for the money path. Every
 * row must pass the LineItem shape (a finite, non-negative estimated_hours
 * included) or the whole read is refused with the reason and the first bad
 * row. Milestone invoicing multiplies a row's hours by the rate, and SOW
 * finalization makes one milestone per row, so a silently dropped or
 * NaN-hours row would bill or schedule the wrong thing (review 2026-09-25,
 * Code Quality 2).
 */
export function readLineItemsExact(raw: string | null): LineItemsRead {
  const rows = decodeLineItemRows(raw)
  if (!Array.isArray(rows)) return rows
  const index = rows.findIndex((row) => !isLineItem(row))
  if (index >= 0) return { ok: false, reason: 'invalid_row', index }
  return { ok: true, items: rows.filter(isLineItem) }
}

/** One line for a log, an error message, or a Sentry event. */
export function describeLineItemsRefusal(refusal: LineItemsRefusal): string {
  return refusal.index === undefined ? refusal.reason : `${refusal.reason} at row ${refusal.index}`
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
