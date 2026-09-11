/**
 * Seat-descriptor section validator for customer.yaml.
 *
 * WHY THIS EXISTS. Nothing in this repo said what a seat IS. `fly status`
 * reports `started`, the only `status:` in any customer.yaml is on the persona
 * and reads `active` on all eight seats including `_template`, and the one
 * place recording "ashton-price has no Machine" was a prose sentence in an
 * upgrade runbook that had gone stale. A live client-designated seat was
 * mislabelled as serving traffic on exactly that evidence.
 *
 * KIND IS AUTHORED, STATE IS DERIVED. This block carries only the two facts
 * that do not change over a seat's life. Lifecycle state — provisioned,
 * connected, serving — is deliberately ABSENT: a state field is a claim an
 * agent can write, and a claim an agent can write is a claim that rots. State
 * belongs to probes of the running system, not to this file.
 *
 * OPTIONAL HERE, REQUIRED IN CI. The block validates as optional so that the
 * portal's `reconstructFromProjection` — which builds a synthetic root from
 * lossy D1 columns and hands it to this validator — keeps resolving for every
 * customer. Making it required would reproduce #1965 exactly: a field the
 * projection cannot carry turns the editor into a config error for everyone.
 * Completeness is enforced instead by a repo test asserting every directory
 * under operator/customers/ authors it.
 */

import { isPlainObject } from './helpers'
import {
  SEAT_KINDS,
  SEAT_PRODUCTS,
  type Seat,
  type SeatKind,
  type SeatProduct,
  type ValidationError,
} from './types'

/**
 * Validate the optional `seat:` block.
 *
 * Returns null on absence (a legitimate state for a projection-reconstructed
 * root) and on unrecoverable error, having pushed the error.
 */
export function checkSeat(root: Record<string, unknown>, errors: ValidationError[]): Seat | null {
  const raw = root['seat']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'seat',
      message: 'seat must be an object when present',
    })
    return null
  }
  const kind = checkEnumField(raw['kind'], 'seat.kind', SEAT_KINDS, errors)
  const product = checkEnumField(raw['product'], 'seat.product', SEAT_PRODUCTS, errors)
  if (kind === null || product === null) return null
  return { kind: kind as SeatKind, product: product as SeatProduct }
}

function checkEnumField(
  raw: unknown,
  path: string,
  allowed: readonly string[],
  errors: ValidationError[]
): string | null {
  if (raw === undefined || raw === null) {
    errors.push({
      code: 'MissingField',
      path,
      message: `${path} is required when seat is present`,
    })
    return null
  }
  if (typeof raw !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a string`,
    })
    return null
  }
  if (!allowed.includes(raw)) {
    errors.push({
      code: 'EnumViolation',
      path,
      message: `${path} must be one of: ${allowed.join(', ')}`,
    })
    return null
  }
  return raw
}
