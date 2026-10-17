/**
 * Remaining section validators: escalation, memory, and the
 * optional voice_library / business_hours / logging / pause / observability
 * blocks. Scope validation lives in sections-scope.ts.
 */

import {
  ACCEPTED_LOG_LEVELS,
  ACCEPTED_LOG_SHIPS,
  AUDIT_LOG_DAYS_MAX,
  VERTICAL_AUDIT_LOG_DAYS_DEFAULTS,
  type BusinessHours,
  type Digest,
  type LogLevel,
  type LogShip,
  type Logging,
  type Memory,
  type MemoryRetention,
  type Pause,
  type ValidationError,
  type Vertical,
  type VoiceLibrary,
} from './types'
import { isPlainObject, optionalNonEmptyString } from './helpers'

export function checkMemory(
  root: Record<string, unknown>,
  customerId: string | null,
  vertical: Vertical | null,
  errors: ValidationError[]
): Memory | null {
  const raw = root['memory']
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path: 'memory', message: 'memory is required' })
    return null
  }
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path: 'memory', message: 'memory must be an object' })
    return null
  }
  const d1 = checkMemoryField(raw, 'd1_namespace', customerId, errors)
  const r2 = checkMemoryR2(raw['r2_vault_path'], customerId, errors)
  const vec = checkMemoryVectorize(raw['vectorize_index'], customerId, errors)
  const retention = checkMemoryRetention(raw['retention'], vertical, errors)
  // ADR 0022 Stream 1: known-optional R2 skill-body keys, populated at bootstrap (PR 2).
  const optSkillBody = (k: 'bucket' | 'prefix'): string | null =>
    optionalNonEmptyString(raw, `r2_skill_bodies_${k}`, `memory.r2_skill_bodies_${k}`, errors)
  const skillBodiesBucket = optSkillBody('bucket')
  const skillBodiesPrefix = optSkillBody('prefix')
  const switches = checkMemorySwitches(raw, errors)
  if (d1 === null || r2 === null || vec === null) return null
  return {
    d1_namespace: d1,
    r2_vault_path: r2,
    vectorize_index: vec,
    retention,
    r2_skill_bodies_bucket: skillBodiesBucket,
    r2_skill_bodies_prefix: skillBodiesPrefix,
    ...switches,
  }
}

/**
 * Hermes native memory switches (passthrough to the profile `config.memory`).
 * Optional booleans; a space-mode seat must author both false, which is a
 * cross-field rule in sections-people.ts (ADR 0091).
 */
function checkMemorySwitches(
  raw: Record<string, unknown>,
  errors: ValidationError[]
): Pick<Memory, 'memory_enabled' | 'user_profile_enabled'> {
  const out: Pick<Memory, 'memory_enabled' | 'user_profile_enabled'> = {}
  for (const key of ['memory_enabled', 'user_profile_enabled'] as const) {
    const v = raw[key]
    if (v === undefined || v === null) continue
    if (typeof v !== 'boolean') {
      errors.push({
        code: 'TypeMismatch',
        path: `memory.${key}`,
        message: `memory.${key} must be a boolean`,
      })
      continue
    }
    out[key] = v
  }
  return out
}

function checkMemoryField(
  raw: Record<string, unknown>,
  key: string,
  customerId: string | null,
  errors: ValidationError[]
): string | null {
  const v = raw[key]
  if (typeof v !== 'string' || v.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `memory.${key}`,
      message: `memory.${key} is required`,
    })
    return null
  }
  if (customerId !== null && v !== customerId) {
    errors.push({
      code: 'IsolationViolation',
      path: `memory.${key}`,
      message: `memory.${key} must equal customer_id (got "${v}", expected "${customerId}")`,
    })
  }
  return v
}

function checkMemoryR2(
  v: unknown,
  customerId: string | null,
  errors: ValidationError[]
): string | null {
  if (typeof v !== 'string' || v.length === 0) {
    errors.push({
      code: 'MissingField',
      path: 'memory.r2_vault_path',
      message: 'memory.r2_vault_path is required',
    })
    return null
  }
  if (customerId !== null && v !== `vaults/${customerId}/`) {
    errors.push({
      code: 'IsolationViolation',
      path: 'memory.r2_vault_path',
      message: `memory.r2_vault_path must equal "vaults/${customerId}/"`,
    })
  }
  return v
}

/**
 * Validate the optional `memory.retention.*` block. Every `*_days` field is
 * optional; missing fields stay `null` and the runtime retention runner
 * (Python `MemoryRetentionPolicy.from_customer_yaml`) falls back to its own
 * defaults. `audit_log_days` has additional rules per audit-retention.md:
 * override-up-only relative to the vertical default, capped at AUDIT_LOG_DAYS_MAX.
 */
function checkMemoryRetention(
  raw: unknown,
  vertical: Vertical | null,
  errors: ValidationError[]
): MemoryRetention | null {
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'memory.retention',
      message: 'memory.retention must be an object when present',
    })
    return null
  }
  return {
    matters_days: checkRetentionInt(raw['matters_days'], 'memory.retention.matters_days', errors),
    documents_days: checkRetentionInt(
      raw['documents_days'],
      'memory.retention.documents_days',
      errors
    ),
    recipients_days: checkRetentionInt(
      raw['recipients_days'],
      'memory.retention.recipients_days',
      errors
    ),
    voice_samples_days: checkRetentionInt(
      raw['voice_samples_days'],
      'memory.retention.voice_samples_days',
      errors
    ),
    audit_log_days: checkAuditLogDays(raw['audit_log_days'], vertical, errors),
    drafts_days: checkRetentionInt(raw['drafts_days'], 'memory.retention.drafts_days', errors),
  }
}

function checkRetentionInt(v: unknown, path: string, errors: ValidationError[]): number | null {
  if (v === undefined || v === null) return null
  if (typeof v !== 'number' || !Number.isInteger(v) || v <= 0) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a positive integer when present`,
    })
    return null
  }
  return v
}

function checkAuditLogDays(
  v: unknown,
  vertical: Vertical | null,
  errors: ValidationError[]
): number | null {
  if (v === undefined || v === null) return null
  const path = 'memory.retention.audit_log_days'
  if (typeof v !== 'number' || !Number.isInteger(v) || v <= 0) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a positive integer when present`,
    })
    return null
  }
  if (v > AUDIT_LOG_DAYS_MAX) {
    errors.push({
      code: 'RetentionOverrideUnreasonable',
      path,
      message: `${path}=${v} exceeds sanity cap of ${AUDIT_LOG_DAYS_MAX} days; likely a day-vs-year typo`,
    })
    return v
  }
  if (vertical !== null) {
    const min = VERTICAL_AUDIT_LOG_DAYS_DEFAULTS[vertical]
    if (v < min) {
      errors.push({
        code: 'RetentionOverrideBelowDefault',
        path,
        message: `${path}=${v} is below the ${vertical} default of ${min} days; override-up-only`,
      })
    }
  }
  return v
}

function checkMemoryVectorize(
  v: unknown,
  customerId: string | null,
  errors: ValidationError[]
): string | null {
  if (typeof v !== 'string' || v.length === 0) {
    errors.push({
      code: 'MissingField',
      path: 'memory.vectorize_index',
      message: 'memory.vectorize_index is required',
    })
    return null
  }
  if (customerId !== null && v !== `hermes-${customerId}-vault`) {
    errors.push({
      code: 'IsolationViolation',
      path: 'memory.vectorize_index',
      message: `memory.vectorize_index must equal "hermes-${customerId}-vault"`,
    })
  }
  return v
}

export function checkVoiceLibrary(
  root: Record<string, unknown>,
  errors: ValidationError[]
): VoiceLibrary | null {
  const raw = root['voice_library']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'voice_library',
      message: 'voice_library must be an object when present',
    })
    return null
  }
  const samples = raw['samples_path']
  if (samples !== undefined && samples !== null && typeof samples !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: 'voice_library.samples_path',
      message: 'samples_path must be a string when present',
    })
    return { samples_path: null }
  }
  return { samples_path: typeof samples === 'string' ? samples : null }
}

export function checkBusinessHours(
  root: Record<string, unknown>,
  errors: ValidationError[]
): BusinessHours | null {
  const raw = root['business_hours']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'business_hours',
      message: 'business_hours must be an object when present',
    })
    return null
  }
  return validateBusinessHoursFields(raw, errors)
}

function validateBusinessHoursFields(
  raw: Record<string, unknown>,
  errors: ValidationError[]
): BusinessHours | null {
  const tz = raw['timezone']
  const days = raw['days']
  const start = raw['start']
  const end = raw['end']
  let ok = true
  if (typeof tz !== 'string' || tz.length === 0) {
    errors.push({
      code: 'MissingField',
      path: 'business_hours.timezone',
      message: 'timezone is required when business_hours is present',
    })
    ok = false
  }
  if (!Array.isArray(days) || !days.every((d) => typeof d === 'string')) {
    errors.push({
      code: 'TypeMismatch',
      path: 'business_hours.days',
      message: 'days must be a list of strings',
    })
    ok = false
  }
  if (typeof start !== 'string' || !/^\d{2}:\d{2}$/.test(start)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'business_hours.start',
      message: 'start must be HH:MM',
    })
    ok = false
  }
  if (typeof end !== 'string' || !/^\d{2}:\d{2}$/.test(end)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'business_hours.end',
      message: 'end must be HH:MM',
    })
    ok = false
  }
  if (!ok) return null
  return {
    timezone: tz as string,
    days: days as string[],
    start: start as string,
    end: end as string,
  }
}

const GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function checkDigest(
  root: Record<string, unknown>,
  errors: ValidationError[]
): Digest | null {
  const raw = root['digest']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'digest',
      message: 'digest must be an object when present',
    })
    return null
  }
  const home = raw['home_matter_id']
  if (typeof home !== 'string' || !GUID_RE.test(home)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'digest.home_matter_id',
      message:
        'home_matter_id must be a Smokeball matter GUID (the designated internal operations matter)',
    })
    return null
  }
  return { home_matter_id: home }
}

export function checkLogging(
  root: Record<string, unknown>,
  errors: ValidationError[]
): Logging | null {
  const raw = root['logging']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'logging',
      message: 'logging must be an object when present',
    })
    return null
  }
  const level = raw['level']
  const shipTo = raw['ship_to']
  let ok = true
  if (typeof level !== 'string' || !(ACCEPTED_LOG_LEVELS as readonly string[]).includes(level)) {
    errors.push({
      code: 'EnumViolation',
      path: 'logging.level',
      message: `level must be one of: ${ACCEPTED_LOG_LEVELS.join(', ')}`,
    })
    ok = false
  }
  if (
    !Array.isArray(shipTo) ||
    !shipTo.every(
      (s) => typeof s === 'string' && (ACCEPTED_LOG_SHIPS as readonly string[]).includes(s)
    )
  ) {
    errors.push({
      code: 'EnumViolation',
      path: 'logging.ship_to',
      message: `ship_to entries must be one of: ${ACCEPTED_LOG_SHIPS.join(', ')}`,
    })
    ok = false
  }
  return ok ? { level: level as LogLevel, ship_to: shipTo as LogShip[] } : null
}

export function checkPause(root: Record<string, unknown>, errors: ValidationError[]): Pause | null {
  const raw = root['pause']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'pause',
      message: 'pause must be an object when present',
    })
    return null
  }
  const active = raw['active']
  const reason = raw['reason']
  if (typeof active !== 'boolean') {
    errors.push({
      code: 'MissingField',
      path: 'pause.active',
      message: 'pause.active must be a boolean',
    })
    return null
  }
  if (active && (typeof reason !== 'string' || reason.length === 0)) {
    errors.push({
      code: 'MissingField',
      path: 'pause.reason',
      message: 'pause.reason is required when pause.active is true',
    })
    return { active, reason: null }
  }
  return { active, reason: typeof reason === 'string' ? reason : null }
}

/**
 * `compliance_enabled` is an optional top-level boolean. When omitted or
 * explicitly false, the dedicated Compliance dashboard view does NOT
 * render even for users who hold the `compliance` product_role — the
 * firm has not opted in to the separation-of-duties posture this view
 * represents. When true, the Compliance dashboard surfaces audit log
 * entry, evidence packet generation, and retention controls.
 *
 * The field is deliberately a boolean rather than an enum so future
 * tiers (e.g. read-only-counsel vs. ethics-officer) live in their own
 * keys; this one stays focused on "is the dashboard view on".
 */
export function checkComplianceEnabled(
  root: Record<string, unknown>,
  errors: ValidationError[]
): boolean {
  const raw = root['compliance_enabled']
  if (raw === undefined || raw === null) return false
  if (typeof raw !== 'boolean') {
    errors.push({
      code: 'TypeMismatch',
      path: 'compliance_enabled',
      message: 'compliance_enabled must be a boolean when present',
    })
    return false
  }
  return raw
}
