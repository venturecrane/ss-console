/**
 * Optional `logic_in_code:` block validator (ADR 0104).
 *
 * The per-seat switches for the mechanisms that can refuse an act or change what
 * a firm sees. Absent means `observe`; a value outside the two is refused here
 * because the readers (`logic_in_code.py` in the connector, the overlay and the
 * broker) would enforce on it. An unknown key is refused because a misspelled
 * switch would read as `observe` while the file said `enforce`. Mirrors the
 * overlay's `bootstrap/validate.py` `_validate_logic_in_code`.
 */

import { isPlainObject } from './helpers'
import type { LogicInCode, LogicInCodeMode, ValidationError } from './types'

export const LOGIC_IN_CODE_SWITCHES = [
  'printed_source_check',
  'post_record_derivation',
  'conflict_fence',
  'fixed_wording',
  'wording_rules',
  'library_setup',
  'library_edit_notice',
] as const

export const LOGIC_IN_CODE_MODES = ['observe', 'enforce'] as const

function isSwitch(key: string): key is (typeof LOGIC_IN_CODE_SWITCHES)[number] {
  return (LOGIC_IN_CODE_SWITCHES as readonly string[]).includes(key)
}

function isMode(value: unknown): value is LogicInCodeMode {
  return typeof value === 'string' && (LOGIC_IN_CODE_MODES as readonly string[]).includes(value)
}

/** The authored switches, or null when the block is absent or malformed. */
export function checkLogicInCode(
  root: Record<string, unknown>,
  errors: ValidationError[]
): LogicInCode | null {
  const raw = root['logic_in_code']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'logic_in_code',
      message: 'logic_in_code must be a mapping when present',
    })
    return null
  }
  const out: LogicInCode = {}
  for (const [key, value] of Object.entries(raw)) {
    const path = `logic_in_code.${key}`
    if (!isSwitch(key)) {
      errors.push({
        code: 'EnumViolation',
        path,
        message: `unknown switch; expected one of ${LOGIC_IN_CODE_SWITCHES.join(', ')}`,
      })
    } else if (isMode(value)) {
      out[key] = value
    } else if (value !== undefined && value !== null) {
      errors.push({
        code: 'EnumViolation',
        path,
        message: `${path} must be one of ${LOGIC_IN_CODE_MODES.join(', ')}`,
      })
    }
  }
  return out
}
