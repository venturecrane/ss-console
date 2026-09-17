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

/** Tier vocabulary low to high — the order the start/ceiling pair is checked in. */
const TIER_ORDER: readonly RoutineTier[] = ROUTINE_TIERS

/**
 * The agreement's plain names for the three levels. A `start_verbatim` equal to
 * one of these (case-insensitively) needs no normalization note; anything else
 * is prose that a human mapped onto the tier vocabulary.
 */
const PLAIN_TIER_NAMES: readonly string[] = ['flag-only', 'prepare-and-route', 'auto-handle']

function isPlainTierName(verbatim: string): boolean {
  return PLAIN_TIER_NAMES.includes(verbatim.trim().toLowerCase())
}

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
  /**
   * Required when `start_verbatim` is not a plain tier name: the agreement
   * phrase the mapping onto `start_tier` rests on, quoted, plus the reading.
   * `start_tier` is what a client page renders as the routine's level, so the
   * judgment behind it is recorded beside the row and pinned by the engagements
   * parity gate. Absent on a row whose starting setting is a plain tier name.
   */
  start_tier_note?: string
  enforcement: RoutineGridEnforcement
}

export interface RoutineGrid {
  adr: string
  seat: string
  persona: string
  source_letter: string
  /**
   * The signed (or signature-draft) agreement whose Schedule A-1 this grid
   * mirrors, as a path inside the private engagements repo. Once a grid
   * names one, the agreement is the definition of record: every row's
   * `routine`, `start_verbatim`, and `ceiling_verbatim` must equal the
   * agreement's row verbatim, enforced from the engagements side
   * (tests/routine-grid-parity.test.ts there reads this public repo), because
   * this repo's CI cannot read the private one. Absent on a pre-agreement
   * grid, where `source_letter` is the source. See
   * docs/runbooks/operator/routine-lifecycle.md.
   */
  source_agreement?: string
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
  | 'StartAboveCeiling'

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

/** Optional non-empty string: absent is fine, present-but-wrong is an error. */
function optString(
  rec: Record<string, unknown>,
  key: string,
  path: string,
  errors: Errors
): string | undefined {
  const v = rec[key]
  if (v === undefined || v === null) return undefined
  if (typeof v !== 'string') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a string` })
    return undefined
  }
  if (v.length === 0) {
    errors.push({ code: 'EmptyField', path, message: `${path} must not be empty` })
    return undefined
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
  const start_tier = reqTier(raw, 'start_tier', `${path}.start_tier`, errors)
  const ceiling_tier = reqTier(raw, 'ceiling_tier', `${path}.ceiling_tier`, errors)
  // The two tiers were parsed independently, so a grid could author a start
  // ABOVE the committed ceiling and the portal's level control would offer it.
  // The pair is a contract, so it is checked as one.
  if (TIER_ORDER.indexOf(start_tier) > TIER_ORDER.indexOf(ceiling_tier)) {
    errors.push({
      code: 'StartAboveCeiling',
      path: `${path}.start_tier`,
      message: `${path}.start_tier (${start_tier}) is above ${path}.ceiling_tier (${ceiling_tier}); a starting setting may never exceed the committed ceiling`,
    })
  }
  const start_verbatim = reqString(raw, 'start_verbatim', `${path}.start_verbatim`, errors)
  // A starting setting that is not one of the three plain tier names is a
  // NORMALIZATION: prose the agreement wrote, mapped by a human onto the closed
  // tier vocabulary. That judgment has to be written down next to the row it
  // governs, because `start_tier` is what the client's page renders as the
  // routine's level. The engagements parity gate asserts the quoted phrase is
  // still in the agreement.
  const normalized = start_verbatim !== '' && !isPlainTierName(start_verbatim)
  const start_tier_note = optString(raw, 'start_tier_note', `${path}.start_tier_note`, errors)
  if (normalized && start_tier_note === undefined) {
    errors.push({
      code: 'MissingField',
      path: `${path}.start_tier_note`,
      message: `${path}.start_tier_note is required: start_verbatim is not a plain tier name, so the mapping onto ${start_tier} is a judgment that must quote the agreement phrase it rests on`,
    })
  }
  return {
    routine: reqString(raw, 'routine', `${path}.routine`, errors),
    letter_section: reqString(raw, 'letter_section', `${path}.letter_section`, errors),
    skills: reqStringList(raw, 'skills', `${path}.skills`, errors, false),
    start_tier,
    ceiling_tier,
    start_verbatim,
    ceiling_verbatim: reqString(raw, 'ceiling_verbatim', `${path}.ceiling_verbatim`, errors),
    ...(start_tier_note === undefined ? {} : { start_tier_note }),
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
  const source_agreement = optString(input, 'source_agreement', 'source_agreement', errors)
  const rows = checkRows(input, errors)
  if (errors.length > 0) return { ok: false, errors }
  return {
    ok: true,
    value: {
      adr,
      seat,
      persona,
      source_letter,
      ...(source_agreement === undefined ? {} : { source_agreement }),
      rows,
    },
  }
}
