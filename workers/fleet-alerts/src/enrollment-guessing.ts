/**
 * enrollment_guessing: someone is guessing a guide seat's invite codes
 * (ADR 0106, migrations 0151/0152).
 *
 * THE GAP. A guide seat that switches invite-code enrollment on adds whoever
 * emails it a valid code to the people it answers. The broker locks a sender
 * out after five failures in a day and the whole seat after twenty in an hour,
 * and audits every attempt, but a lockout nobody hears about is a guessing run
 * nobody stops. The seat now ships `sender_enrollment` on every beat (stored in
 * sender_enrollment_json): the enrolled count, the failed attempts in the
 * trailing day, and whether the seat is locked.
 *
 * THE DECISION, a plain tri-state:
 *   sender_enrollment_json NULL   push nothing (hold: a seat with enrollment
 *                                 off, an image without the field, or a seat
 *                                 that could not read its ledger).
 *   failed_24h >= threshold, or locked
 *                                 push ACTIVE on this first reading. Ten
 *                                 failures in a day is two senders' worth of
 *                                 lockouts, more than a person mistyping a
 *                                 code; a locked seat is refusing everyone,
 *                                 the real people included.
 *   otherwise                     push inactive (resolves). The day's count
 *                                 falls as the window moves, so a run that
 *                                 stopped resolves on its own.
 */

import type { ConditionState, FleetStatusRow } from './index'

export const ENROLLMENT_GUESSING_CONDITION = 'enrollment_guessing'

/** Failed attempts in the trailing day that page. */
export const ENROLLMENT_FAILED_24H_THRESHOLD = 10

export interface SenderEnrollmentFacts {
  enrolled: number
  failed_24h: number
  locked: boolean
}

function nonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/**
 * Parse a row's sender_enrollment_json defensively (the Worker's own trust
 * boundary: one corrupt row degrades to null, a hold, and never aborts the
 * fleet loop).
 */
export function parseSenderEnrollment(
  json: string | null | undefined
): SenderEnrollmentFacts | null {
  if (json === null || json === undefined) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const enrolled = nonNegInt(r.enrolled)
  const failed = nonNegInt(r.failed_24h)
  if (enrolled === null || failed === null || typeof r.locked !== 'boolean') return null
  return { enrolled, failed_24h: failed, locked: r.locked }
}

function pages(f: SenderEnrollmentFacts): boolean {
  return f.locked || f.failed_24h >= ENROLLMENT_FAILED_24H_THRESHOLD
}

function detail(slug: string, f: SenderEnrollmentFacts): string {
  const counts =
    `${f.failed_24h} failed invite-code attempt(s) in the last 24 hours ` +
    `(page at ${ENROLLMENT_FAILED_24H_THRESHOLD}), ${f.enrolled} person(s) enrolled`
  if (!pages(f)) return `${counts}, seat not locked`
  const locked = f.locked
    ? 'The seat is LOCKED: it refuses every invite code, real ones included, until the hour passes. '
    : ''
  return (
    `${counts}. ${locked}` +
    'Each attempt is a SENDER_ENROLLMENT_ATTEMPT row in the seat audit ledger (outcome, sender key, code id): ' +
    `fly ssh console -a hermes-${slug}. Revoke a leaked code there with sender_enrollment_cli. ` +
    'Auto-resolves when the day holds fewer failures and the seat is not locked.'
  )
}

/** The enrollment_guessing state for one row, or none (hold). */
export function enrollmentGuessingConditions(row: FleetStatusRow): ConditionState[] {
  const f = parseSenderEnrollment(row.sender_enrollment_json)
  if (f === null) return []
  return [
    {
      customer_slug: row.customer_slug,
      condition: ENROLLMENT_GUESSING_CONDITION,
      active: pages(f),
      detail: detail(row.customer_slug, f),
    },
  ]
}
