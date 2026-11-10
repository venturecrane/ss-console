/**
 * Optional `sender_enrollment:` block validator (ADR 0106).
 *
 * Switches invite-code enrollment on: a person holding a code SMD issued
 * emails the seat from an unlisted address and is added to the people the
 * seat answers. Accepted only on a seat with `seat.role: guide` or
 * `seat.kind: demo` (SMD's demo seat, where sales representatives enroll the
 * same way; ADR 0106 amendment 2026-11-10). On any other seat it is refused,
 * because on a client seat who is listed is the client's own decision, made
 * in this file by a reviewed change. The keys are `enabled` (a boolean)
 * and `daily_limit` (how many emails a day one listed sender who is not an
 * administrator is answered); anything else is refused, so a misspelled
 * switch cannot read as off while the file says on. Mirrors the overlay's
 * `bootstrap/validate.py` `_validate_sender_enrollment`.
 */

import { isPlainObject } from './helpers'
import type { ValidationError } from './types'

export const SENDER_ENROLLMENT_ROLE = 'guide'
/** The other seat descriptor that may opt in: SMD's own demo seat (ADR 0107 purpose demo). */
export const SENDER_ENROLLMENT_KIND = 'demo'

/** Whether this seat's descriptor may switch enrollment on at all. */
export function enrollmentSeatAllowed(seat: unknown): boolean {
  if (!isPlainObject(seat)) return false
  return seat['role'] === SENDER_ENROLLMENT_ROLE || seat['kind'] === SENDER_ENROLLMENT_KIND
}

/** Bounds of `daily_limit`, the emails a day one listed, non-administrator sender is answered. */
const DAILY_LIMIT_MAX = 1000

/** The per-sender daily limit (the overlay's webhook router, daily_limit.py). */
function checkDailyLimit(raw: Record<string, unknown>, errors: ValidationError[]): void {
  if (!('daily_limit' in raw)) return
  const v = raw['daily_limit']
  if (typeof v !== 'number' || !Number.isInteger(v) || v < 1 || v > DAILY_LIMIT_MAX) {
    errors.push({
      code: 'TypeMismatch',
      path: 'sender_enrollment.daily_limit',
      message: `sender_enrollment.daily_limit must be a whole number from 1 to ${DAILY_LIMIT_MAX}`,
    })
  }
}

export function checkSenderEnrollment(
  root: Record<string, unknown>,
  errors: ValidationError[]
): void {
  const raw = root['sender_enrollment']
  if (raw === undefined || raw === null) return
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'sender_enrollment',
      message: 'sender_enrollment must be a mapping when present',
    })
    return
  }
  for (const key of Object.keys(raw)) {
    if (key !== 'enabled' && key !== 'daily_limit') {
      errors.push({
        code: 'EnumViolation',
        path: `sender_enrollment.${key}`,
        message: 'unknown key; sender_enrollment takes only enabled and daily_limit',
      })
    }
  }
  checkDailyLimit(raw, errors)
  if (typeof raw['enabled'] !== 'boolean') {
    errors.push({
      code: 'TypeMismatch',
      path: 'sender_enrollment.enabled',
      message: 'sender_enrollment.enabled must be true or false',
    })
  }
  if (!enrollmentSeatAllowed(root['seat'])) {
    errors.push({
      code: 'EnumViolation',
      path: 'sender_enrollment',
      message:
        'sender_enrollment is accepted only on a seat with seat.role: guide or seat.kind: demo; on any other ' +
        "seat who is listed is the client's own decision, made in scope.inbound_allow_from",
    })
  }
}
