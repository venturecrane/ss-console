/**
 * Which request cards are ledgered but not emailed (migration 0147).
 *
 * A card that says nothing went wrong, from a seat that is not a customer's:
 * our own rehearsals answering on time were half of the ops mail on 2026-11-02.
 * Everything else still emails on every seat, because a test seat that stops
 * answering is usually something every seat shares. A customer seat's request
 * cards always email (the Captain asked for one per request, 2026-10-27, #3555).
 *
 * A routine notice is quiet on every seat: a later reply with no request behind
 * it is the Operator telling someone something on a schedule (21 negotiation
 * notices in two weeks, subject "(no request on record)"). Nobody asked, nothing
 * went wrong, and nothing is owed, so it is not a message about a request.
 *
 * The seat's kind is its authored `seat.kind`, read from the D1 projection. An
 * unknown kind is NOT a test seat, the same rule as operator/bin/work-health.py:
 * a seat goes quiet only when its own config says it may.
 */

import type { RequestCard } from './request-card'

/** The card itself reports success: an answer given, or a job delivered. */
export function cardReportsSuccess(card: RequestCard): boolean {
  if (card.kind === 'replied' || card.kind === 'followup') return !card.couldNot
  return card.kind === 'job_done' && card.job?.state === 'delivered'
}

/** The seat's own marker for a later reply with no request behind it. */
export const NO_REQUEST_SUBJECT = '(no request on record)'

/** A delivered notice nobody asked for. One that declined still emails. */
export function cardIsRoutineNotice(card: RequestCard): boolean {
  return card.kind === 'followup' && card.subject === NO_REQUEST_SUBJECT && !card.couldNot
}

/** `seat.kind` from a projected seat_json, or null when it cannot be read. */
export function seatKindOf(seatJson: string | null | undefined): string | null {
  if (!seatJson) return null
  try {
    const seat: unknown = JSON.parse(seatJson)
    if (typeof seat !== 'object' || seat === null) return null
    const kind = (seat as Record<string, unknown>).kind
    return typeof kind === 'string' && kind.length > 0 ? kind : null
  } catch {
    return null
  }
}

/** True when this card is ledgered and not emailed. A failed read emails. */
export async function cardIsQuiet(
  db: D1Database,
  slug: string,
  card: RequestCard
): Promise<boolean> {
  if (cardIsRoutineNotice(card)) return true
  if (!cardReportsSuccess(card)) return false
  try {
    const row = await db
      .prepare('SELECT seat_json FROM customer_configs WHERE customer_slug = ?')
      .bind(slug)
      .first<{ seat_json: string | null }>()
    const kind = seatKindOf(row?.seat_json)
    return kind !== null && kind !== 'customer'
  } catch {
    return false
  }
}

export async function markCardQuiet(db: D1Database, slug: string, cardKey: string): Promise<void> {
  await db
    .prepare(
      'UPDATE operator_request_cards SET email_skipped = 1 WHERE customer_slug = ? AND card_key = ?'
    )
    .bind(slug, cardKey)
    .run()
}
