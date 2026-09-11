/**
 * routine-grid.yaml structural validator (ADR 0075).
 *
 * The routine grid is the compiled traceability artifact that maps a client
 * commitment letter's per-routine autonomy tiers onto a seat's skills and
 * records how each tier is enforced in config. It lives next to customer.yaml
 * at operator/customers/<slug>/routine-grid.yaml and is the source for the
 * console "the work" data plane (docs/design/operator/04-console-structure.md
 * §6 / §7 step 3).
 *
 * This module consumes the parsed YAML as an `unknown` (the consumer chooses
 * its YAML parser — the same posture as the customer.yaml validator per ADR
 * 0012 §4) and returns a tagged-union result: `ok` carries a typed RoutineGrid,
 * the failure branch carries a flat list of path-named RoutineGridValidationError
 * entries. It never throws and never casts unvalidated input — every field is
 * checked before it is admitted (repo coding standard: parse, don't cast).
 *
 * The error shape mirrors src/lib/operator/customer-yaml/ (typed `code`,
 * JSONPath-ish `path`, human `message`) but the code set is scoped to this
 * artifact so the two validators stay independent.
 */

import {
  ACCEPTED_EXPOSURE_CEILINGS,
  EXPOSURE_ACTION_CLASSES,
  SEND_ACTION_CLASSES,
} from './customer-yaml/types'
import { isRecord } from '../api/helpers'

/**
 * Closed tier vocabulary. The letter's prose forms ("Runs on its own",
 * "Flag-only / prepare-and-route") are normalized to exactly these three in the
 * grid file itself; this validator only accepts the normalized set.
 */
export type RoutineTier = 'flag-only' | 'prepare-and-route' | 'auto-handle'

export const ROUTINE_TIERS: readonly RoutineTier[] = [
  'flag-only',
  'prepare-and-route',
  'auto-handle',
]

export interface RoutineGridEnforcement {
  initiation: string
  /**
   * Exposure action class -> live ceiling value (e.g. internal_write ->
   * autonomous). Keys are drawn from `EXPOSURE_ACTION_CLASSES` and values from
   * `ACCEPTED_EXPOSURE_CEILINGS` — NOT free-form (ss#2314). These strings index
   * the seat's runtime override store, so a key outside the vocabulary matches
   * nothing on the Machine and used to render as a silent `flag-only`.
   */
  exposure_keys: Record<string, string>
  content_floor: boolean
  banned_tools: string[]
  notes: string
}

export interface RoutineGridRow {
  routine: string
  letter_section: string
  skills: string[]
  start_tier: RoutineTier
  ceiling_tier: RoutineTier
  start_verbatim: string
  ceiling_verbatim: string
  enforcement: RoutineGridEnforcement
}

export interface RoutineGrid {
  adr: string
  seat: string
  persona: string
  source_letter: string
  rows: RoutineGridRow[]
}

export type RoutineGridErrorCode =
  | 'MissingField'
  | 'EmptyField'
  | 'EmptyList'
  | 'TypeMismatch'
  | 'EnumViolation'
  | 'InvalidActionClass'
  | 'InvalidActionCeiling'

export interface RoutineGridValidationError {
  code: RoutineGridErrorCode
  path: string
  message: string
}

export type RoutineGridValidationResult =
  { ok: true; value: RoutineGrid } | { ok: false; errors: RoutineGridValidationError[] }

type Errors = RoutineGridValidationError[]

/** Required non-empty string. Pushes an error and returns '' on any failure;
 *  the empty value is only ever surfaced when the whole grid validates clean. */
function reqString(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: Errors
): string {
  const v = rec[key]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return ''
  }
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a string` })
    return ''
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyField', path, message: `${path} must not be empty` })
    return ''
  }
  return v
}

function reqBool(rec: Record<string, unknown>, key: string, path: string, errors: Errors): boolean {
  const v = rec[key]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return false
  }
  if (typeof v !== 'boolean') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a boolean` })
    return false
  }
  return v
}

/** Required list of strings. `allowEmpty` distinguishes skills (>=1) from
 *  banned_tools (may be []). */
function reqStringList(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: Errors,
  allowEmpty: boolean
): string[] {
  const v = rec[key]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return []
  }
  if (!Array.isArray(v)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list of strings` })
    return []
  }
  // Build the typed list element-wise (parse, don't cast): one non-string
  // element makes the whole field a TypeMismatch.
  const out: string[] = []
  for (const x of v) {
    if (typeof x !== 'string') {
      errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list of strings` })
      return []
    }
    out.push(x)
  }
  if (!allowEmpty && out.length === 0) {
    errors.push({ code: 'EmptyList', path, message: `${path} must not be empty` })
    return []
  }
  return out
}

function reqTier(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: Errors
): RoutineTier {
  const v = rec[key]
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return 'flag-only'
  }
  if (typeof v !== 'string' || !(ROUTINE_TIERS as readonly string[]).includes(v)) {
    errors.push({
      code: 'EnumViolation',
      path,
      message: `${path} must be one of: ${ROUTINE_TIERS.join(', ')}`,
    })
    return 'flag-only'
  }
  return v as RoutineTier
}

/**
 * Mapping of exposure action class -> ceiling value. Empty map is admitted (a
 * row may gate nothing).
 *
 * KEYS ARE A CLOSED VOCABULARY (ss#2314). These strings are not documentation:
 * they index the seat's `exposure_override` store, both when the portal reads
 * the tier it DISPLAYS and when it writes the override the Machine enforces. A
 * key outside `EXPOSURE_ACTION_CLASSES` matches nothing on either side, and the
 * read path's miss is indistinguishable from the legitimate fail-closed
 * "unauthored" answer — so the portal rendered a safety posture nobody was
 * enforcing. Validating here is the offline half of the fix: the typo fails CI
 * rather than reaching a client-facing control. The runtime half is
 * `resolveLiveTier` / the `unknown_exposure_key` rejection in
 * entitlement-compiler.ts, which stays fail-closed for a key that gets past
 * this gate (a grid read from the D1 projection is not re-validated here).
 *
 * `confirm` is restricted to the send classes exactly as the customer.yaml
 * exposure validator restricts it (`sections-persona-skills.ts`
 * `checkExposureMap`) — enforce()'s confirm branch lives in the send branch,
 * so the value has no defined meaning elsewhere. The two validators are
 * deliberately parallel: the grid records what customer.yaml authors, so a
 * pair they disagree about could never be realized.
 */
function reqExposureKeys(
  rec: Record<string, unknown>,
  path: string,
  errors: Errors
): Record<string, string> {
  const v = rec['exposure_keys']
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return {}
  }
  if (!isRecord(v)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a mapping of exposure action class to value`,
    })
    return {}
  }
  const out: Record<string, string> = {}
  for (const [k, val] of Object.entries(v)) {
    if (!(EXPOSURE_ACTION_CLASSES as readonly string[]).includes(k)) {
      errors.push({
        code: 'InvalidActionClass',
        path: `${path}.${k}`,
        message:
          `${path}.${k} is not an action class the Operator can honor; ` +
          `must be one of: ${EXPOSURE_ACTION_CLASSES.join(', ')}`,
      })
      continue
    }
    const allowed = (SEND_ACTION_CLASSES as readonly string[]).includes(k)
      ? ACCEPTED_EXPOSURE_CEILINGS
      : ACCEPTED_EXPOSURE_CEILINGS.filter((c) => c !== 'confirm')
    if (typeof val !== 'string' || !(allowed as readonly string[]).includes(val)) {
      errors.push({
        code: 'InvalidActionCeiling',
        path: `${path}.${k}`,
        message: `${path}.${k} must be one of: ${allowed.join(', ')}`,
      })
      continue
    }
    out[k] = val
  }
  return out
}

function checkEnforcement(
  rec: Record<string, unknown>,
  path: string,
  errors: Errors
): RoutineGridEnforcement {
  const v = rec['enforcement']
  const ePath = `${path}.enforcement`
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path: ePath, message: `${ePath} is required` })
    return { initiation: '', exposure_keys: {}, content_floor: false, banned_tools: [], notes: '' }
  }
  if (!isRecord(v)) {
    errors.push({ code: 'TypeMismatch', path: ePath, message: `${ePath} must be an object` })
    return { initiation: '', exposure_keys: {}, content_floor: false, banned_tools: [], notes: '' }
  }
  return {
    initiation: reqString(v, 'initiation', `${ePath}.initiation`, errors),
    exposure_keys: reqExposureKeys(v, `${ePath}.exposure_keys`, errors),
    content_floor: reqBool(v, 'content_floor', `${ePath}.content_floor`, errors),
    banned_tools: reqStringList(v, 'banned_tools', `${ePath}.banned_tools`, errors, true),
    notes: reqString(v, 'notes', `${ePath}.notes`, errors),
  }
}

function checkRow(raw: unknown, path: string, errors: Errors): RoutineGridRow {
  if (!isRecord(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be an object` })
    return {
      routine: '',
      letter_section: '',
      skills: [],
      start_tier: 'flag-only',
      ceiling_tier: 'flag-only',
      start_verbatim: '',
      ceiling_verbatim: '',
      enforcement: {
        initiation: '',
        exposure_keys: {},
        content_floor: false,
        banned_tools: [],
        notes: '',
      },
    }
  }
  return {
    routine: reqString(raw, 'routine', `${path}.routine`, errors),
    letter_section: reqString(raw, 'letter_section', `${path}.letter_section`, errors),
    skills: reqStringList(raw, 'skills', `${path}.skills`, errors, false),
    start_tier: reqTier(raw, 'start_tier', `${path}.start_tier`, errors),
    ceiling_tier: reqTier(raw, 'ceiling_tier', `${path}.ceiling_tier`, errors),
    start_verbatim: reqString(raw, 'start_verbatim', `${path}.start_verbatim`, errors),
    ceiling_verbatim: reqString(raw, 'ceiling_verbatim', `${path}.ceiling_verbatim`, errors),
    enforcement: checkEnforcement(raw, path, errors),
  }
}

function checkRows(root: Record<string, unknown>, errors: Errors): RoutineGridRow[] {
  const v = root['rows']
  if (v === undefined || v === null) {
    errors.push({ code: 'MissingField', path: 'rows', message: 'rows is required' })
    return []
  }
  if (!Array.isArray(v)) {
    errors.push({ code: 'TypeMismatch', path: 'rows', message: 'rows must be a list' })
    return []
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyList', path: 'rows', message: 'rows must not be empty' })
    return []
  }
  return v.map((row, i) => checkRow(row, `rows[${i}]`, errors))
}

/**
 * Validate a parsed routine-grid.yaml object. Never throws; collects every
 * structural violation and returns them in one pass so an author sees all
 * errors at once (mirrors the customer.yaml validator).
 */
export function validateRoutineGrid(input: unknown): RoutineGridValidationResult {
  const errors: Errors = []
  if (!isRecord(input)) {
    errors.push({
      code: 'TypeMismatch',
      path: '$',
      message: 'routine-grid.yaml must parse to an object at the root',
    })
    return { ok: false, errors }
  }
  const adr = reqString(input, 'adr', 'adr', errors)
  const seat = reqString(input, 'seat', 'seat', errors)
  const persona = reqString(input, 'persona', 'persona', errors)
  const source_letter = reqString(input, 'source_letter', 'source_letter', errors)
  const rows = checkRows(input, errors)
  if (errors.length > 0) return { ok: false, errors }
  return { ok: true, value: { adr, seat, persona, source_letter, rows } }
}
