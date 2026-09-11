/**
 * Per-skill entry + entitlement validation for customer.yaml personas[] —
 * split from sections-personas.ts to keep both under the 500-line file
 * ceiling. Covers skill name/version/enabled/initiation, legacy-field
 * rejection, authored scalar settings (ADR 0075), cost estimates, skill
 * assembly, and the persona entitlements exposure map (ADR 0056).
 */

import {
  ACCEPTED_ACTION_CLASSES,
  ACCEPTED_EXPOSURE_CEILINGS,
  SEND_ACTION_CLASSES,
  type AuthoredExposureActionClass,
  type CostEstimate,
  type ExposureCeiling,
  type PersonaEntitlements,
  type PersonaSkill,
  type SkillInitiation,
  type ValidationError,
} from './types'
import { isPlainObject, optionalStringList } from './helpers'

export function checkPersonaSkills(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): PersonaSkill[] {
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required` })
    return []
  }
  if (!Array.isArray(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list` })
    return []
  }
  const out: PersonaSkill[] = []
  for (let i = 0; i < raw.length; i++) {
    const skill = checkOneSkill(raw[i], `${path}[${i}]`, errors)
    if (skill !== null) out.push(skill)
  }
  return out
}

function checkOneSkill(raw: unknown, path: string, errors: ValidationError[]): PersonaSkill | null {
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: 'skill entries must be objects' })
    return null
  }
  checkSkillName(raw['name'], path, errors)
  rejectLegacyField(raw, 'trust_ceiling', path, errors)
  rejectLegacyField(raw, 'action_ceilings', path, errors)
  checkSkillVersion(raw['version'], path, errors)
  checkSkillEnabled(raw['enabled'], path, errors)
  const initiation = checkSkillInitiation(raw['initiation'], `${path}.initiation`, errors)
  const cost = checkCostEstimate(raw['cost_estimate'], `${path}.cost_estimate`, errors)
  const scope = optionalStringList(raw, 'scope', `${path}.scope`, errors)
  const settings = checkSkillSettings(raw['settings'], `${path}.settings`, errors)
  return assembleSkill(raw, initiation, cost, scope, settings)
}

/**
 * Authored per-skill settings (ADR 0075): a flat map of scalar knobs the
 * runtime reads verbatim. Mirrors the overlay's `_skill_settings_block`
 * semantics with one deliberate difference: the overlay silently DROPS
 * non-scalar values, so this side REJECTS them — otherwise a nested value
 * would validate here and vanish at translate, authoring a setting the
 * runtime never sees (the projection would lie about the seat).
 */
function checkSkillSettings(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): Record<string, string | number | boolean> | undefined {
  if (raw === undefined || raw === null) return undefined
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a map of scalar values` })
    return undefined
  }
  const out: Record<string, string | number | boolean> = {}
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      out[key] = value
    } else {
      errors.push({
        code: 'TypeMismatch',
        path: `${path}.${key}`,
        message: `${path}.${key} must be a scalar (string/number/boolean) — the overlay drops anything else`,
      })
    }
  }
  return Object.keys(out).length > 0 ? out : undefined
}

function checkSkillName(name: unknown, path: string, errors: ValidationError[]): void {
  if (typeof name !== 'string' || name.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.name`,
      message: 'skill.name is required',
    })
  }
}

function rejectLegacyField(
  raw: Record<string, unknown>,
  field: string,
  path: string,
  errors: ValidationError[]
): void {
  if (raw[field] === undefined) return
  errors.push({
    code: 'LegacyEntitlementField',
    path: `${path}.${field}`,
    message: `${field} is retired; use personas[].entitlements.exposure and skills[].initiation`,
  })
}

export function checkPersonaEntitlements(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): PersonaEntitlements {
  if (raw === undefined || raw === null) return { exposure: {} }
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be an object when present`,
    })
    return { exposure: {} }
  }
  rejectLegacyField(raw, 'trust_ceiling', path, errors)
  rejectLegacyField(raw, 'action_ceilings', path, errors)
  const exposure = checkExposureMap(raw['exposure'], `${path}.exposure`, errors, {
    allowCommitmentConfirm: true,
  })
  if (raw['exposure_ceiling'] === undefined || raw['exposure_ceiling'] === null) {
    return { exposure }
  }
  // The runtime entitlement dial's letter-commitment bound (ss#2003 Q7): same
  // per-entry vocabulary as exposure, plus coherence — an authored exposure
  // above its own ceiling is incoherent, not sparse.
  const ceiling = checkExposureMap(raw['exposure_ceiling'], `${path}.exposure_ceiling`, errors)
  for (const [key, bound] of Object.entries(ceiling)) {
    const authored = exposure[key as AuthoredExposureActionClass]
    if (authored !== undefined && restrictiveness(authored) < restrictiveness(bound)) {
      errors.push({
        code: 'InvalidActionCeiling',
        path: `${path}.exposure.${key}`,
        message: `authored value '${authored}' exceeds its own exposure_ceiling '${bound}' — raise the ceiling or lower the exposure`,
      })
    }
  }
  return { exposure, exposure_ceiling: ceiling }
}

// Restrictiveness ordering for the exposure / exposure_ceiling coherence
// check (higher == more restrictive). Local mirror of the frozen governance
// table (src/lib/portal/operator/config-governance.ts) — the validator layer
// stays free of portal imports.
const CEILING_RESTRICTIVENESS: Record<ExposureCeiling, number> = {
  autonomous: 0,
  confirm: 1,
  draft_for_review: 2,
  refused: 3,
}

function restrictiveness(c: ExposureCeiling): number {
  return CEILING_RESTRICTIVENESS[c]
}

function checkExposureMap(
  raw: unknown,
  path: string,
  errors: ValidationError[],
  options: { allowCommitmentConfirm?: boolean } = {}
): Partial<Record<AuthoredExposureActionClass, ExposureCeiling>> {
  if (raw === undefined || raw === null) return {}
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be an object when present` })
    return {}
  }
  const out: Partial<Record<AuthoredExposureActionClass, ExposureCeiling>> = {}
  for (const [key, value] of Object.entries(raw)) {
    if (!(ACCEPTED_ACTION_CLASSES as readonly string[]).includes(key)) {
      errors.push({
        code: 'InvalidActionClass',
        path: `${path}.${key}`,
        message: `exposure key must be one of: ${ACCEPTED_ACTION_CLASSES.join(', ')}`,
      })
      continue
    }
    if (key === 'read') {
      errors.push({
        code: 'InvalidActionClass',
        path: `${path}.read`,
        message: 'read is always allowed by enforcement and must not be authored as exposure',
      })
      continue
    }
    // `confirm` (act after an explicit in-turn approval, ADR 0071) is valid for
    // the send classes (external_send / external_send_internal /
    // external_send_client / external_send_vendor), and, since ss-console#2536,
    // for `commitment` on the AUTHORED EXPOSURE only.
    //
    // Why commitment and not destructive: a commitment is the firm's own record
    // gaining something (the Operator's internal matter), and the firm's
    // administrators can be shown exactly what it will be and can answer. A
    // destructive act removes something, the read-back cannot show what would be
    // lost, and it stays where it is until somebody argues otherwise.
    //
    // Why exposure and not exposure_ceiling: the ceiling is the entitlement
    // dial's Machine-side clamp, derived from the routine grid's send tiers
    // (tests/customer-commitments.test.ts gate (i) checks that derivation
    // equals the authored map), and commitment has no send tier to derive from.
    // Leaving it out of the ceiling map means the dial can never raise
    // commitment at all, which is the fail-closed direction.
    const confirmAllowed =
      (SEND_ACTION_CLASSES as readonly string[]).includes(key) ||
      (key === 'commitment' && options.allowCommitmentConfirm === true)
    const allowedCeilings = confirmAllowed
      ? ACCEPTED_EXPOSURE_CEILINGS
      : ACCEPTED_EXPOSURE_CEILINGS.filter((c) => c !== 'confirm')
    if (typeof value !== 'string' || !(allowedCeilings as readonly string[]).includes(value)) {
      errors.push({
        code: 'InvalidActionCeiling',
        path: `${path}.${key}`,
        message: `exposure.${key} must be one of: ${allowedCeilings.join(', ')}`,
      })
      continue
    }
    out[key as AuthoredExposureActionClass] = value as ExposureCeiling
  }
  return out
}

function checkSkillInitiation(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): SkillInitiation {
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path, message: `${path} is required for enabled skills` })
    return { manual: false, scheduled: false, webhook: false }
  }
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be an object` })
    return { manual: false, scheduled: false, webhook: false }
  }
  return {
    manual: checkInitiationFlag(raw['manual'], `${path}.manual`, errors),
    scheduled: checkInitiationFlag(raw['scheduled'], `${path}.scheduled`, errors),
    webhook: checkInitiationFlag(raw['webhook'], `${path}.webhook`, errors),
  }
}

function checkInitiationFlag(raw: unknown, path: string, errors: ValidationError[]): boolean {
  if (typeof raw !== 'boolean') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a boolean` })
    return false
  }
  return raw
}

function checkSkillVersion(version: unknown, path: string, errors: ValidationError[]): void {
  if (version !== undefined && version !== null && typeof version !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.version`,
      message: 'skill.version must be a string when present',
    })
  }
}

function checkSkillEnabled(enabled: unknown, path: string, errors: ValidationError[]): void {
  if (enabled !== undefined && enabled !== null && typeof enabled !== 'boolean') {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.enabled`,
      message: 'skill.enabled must be a boolean when present',
    })
  }
}

function assembleSkill(
  raw: Record<string, unknown>,
  initiation: SkillInitiation,
  cost: CostEstimate | null,
  scope: string[],
  settings: Record<string, string | number | boolean> | undefined
): PersonaSkill {
  const name = raw['name']
  const version = raw['version']
  const enabled = raw['enabled']
  const skill: PersonaSkill = {
    name: typeof name === 'string' ? name : '',
    version: typeof version === 'string' ? version : 'pending',
    initiation,
    enabled: typeof enabled === 'boolean' ? enabled : true,
    cost_estimate: cost,
    scope,
  }
  if (settings !== undefined) skill.settings = settings
  return skill
}

function checkCostEstimate(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): CostEstimate | null {
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be an object` })
    return null
  }
  const fields: Array<keyof CostEstimate> = [
    'tokens_in_per_run',
    'tokens_out_per_run',
    'tool_calls_per_run',
    'runs_per_day_typical',
  ]
  const out: Partial<CostEstimate> = {}
  let ok = true
  for (const f of fields) {
    const v = raw[f]
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
      errors.push({
        code: 'TypeMismatch',
        path: `${path}.${f}`,
        message: `${f} must be a non-negative integer`,
      })
      ok = false
    } else {
      out[f] = v
    }
  }
  return ok ? (out as CostEstimate) : null
}
