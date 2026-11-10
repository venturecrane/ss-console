/**
 * Optional `sender_enrollment:` block validator (ADR 0106).
 *
 * Switches invite-code enrollment on: a person holding a code SMD issued
 * emails the seat from an unlisted address and is added to the people the
 * seat answers. Accepted only on a seat with `seat.role: guide`. On any other
 * seat it is refused, because on a client seat who is listed is the client's
 * own decision, made in this file by a reviewed change. The one key is
 * `enabled` (a boolean); anything else is refused, so a misspelled switch
 * cannot read as off while the file says on. Mirrors the overlay's
 * `bootstrap/validate.py` `_validate_sender_enrollment`.
 */

import { isPlainObject } from './helpers'
import type { ValidationError } from './types'

export const SENDER_ENROLLMENT_ROLE = 'guide'

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
    if (key !== 'enabled') {
      errors.push({
        code: 'EnumViolation',
        path: `sender_enrollment.${key}`,
        message: 'unknown key; sender_enrollment takes only enabled',
      })
    }
  }
  if (typeof raw['enabled'] !== 'boolean') {
    errors.push({
      code: 'TypeMismatch',
      path: 'sender_enrollment.enabled',
      message: 'sender_enrollment.enabled must be true or false',
    })
  }
  const seat = root['seat']
  const role = isPlainObject(seat) ? seat['role'] : undefined
  if (role !== SENDER_ENROLLMENT_ROLE) {
    errors.push({
      code: 'EnumViolation',
      path: 'sender_enrollment',
      message:
        'sender_enrollment is accepted only on a seat with seat.role: guide; on any other ' +
        "seat who is listed is the client's own decision, made in scope.inbound_allow_from",
    })
  }
}
