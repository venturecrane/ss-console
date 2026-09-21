/**
 * Optional `firm_identity:` block validator — the firm's letterhead.
 *
 * Authored once per firm, verbatim from the firm's own letterhead or pleading
 * header. The Smokeball connector's renderer prints it, in tool code, as the
 * first-page letterhead of `letter` / `demand_letter` documents rendered on the
 * starter base (operator/connectors/smokeball/smokeball_connector/letterhead.py).
 * The model never types these values, which is why they never meet the template
 * content gate.
 *
 * Validate-only (pushes errors); the connector reads the block off the seat's
 * live customer.yaml. `name` is required when the block is present; every other
 * field is optional and an absent one is an omitted line. Unknown keys are
 * refused: a misspelled `city_state_zip` would otherwise silently drop a line
 * from every letter.
 */

import type { ValidationError } from './types'
import { isPlainObject } from './helpers'

const FIELDS = ['name', 'street', 'city_state_zip', 'phone', 'fax', 'website'] as const
const MAX_LEN = 200

export function checkFirmIdentity(root: Record<string, unknown>, errors: ValidationError[]): void {
  const raw = root['firm_identity']
  if (raw === undefined || raw === null) return // optional block
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'firm_identity',
      message: 'firm_identity must be a mapping when present',
    })
    return
  }
  for (const key of Object.keys(raw)) {
    if (!(FIELDS as readonly string[]).includes(key)) {
      errors.push({
        code: 'InvalidFormat',
        path: `firm_identity.${key}`,
        message: `firm_identity.${key} is not a known field (known: ${FIELDS.join(', ')})`,
      })
    }
  }
  if (raw['name'] === undefined || raw['name'] === null) {
    errors.push({
      code: 'MissingField',
      path: 'firm_identity.name',
      message: 'firm_identity.name is required when firm_identity is authored',
    })
  }
  for (const key of FIELDS) {
    const value = raw[key]
    if (value === undefined || value === null) continue
    const path = `firm_identity.${key}`
    if (typeof value !== 'string') {
      errors.push({ code: 'TypeMismatch', path, message: `${path} must be a string (quote it)` })
    } else if (value.trim() === '') {
      errors.push({
        code: 'EmptyField',
        path,
        message: `${path} must not be empty; omit it instead`,
      })
    } else if (value.length > MAX_LEN) {
      errors.push({ code: 'InvalidFormat', path, message: `${path} exceeds ${MAX_LEN} characters` })
    }
  }
}
