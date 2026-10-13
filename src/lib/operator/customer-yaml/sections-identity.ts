/**
 * Identity + runtime + users section validators for customer.yaml.
 * Kept apart from validator.ts to honor the 500/75/15 ceiling.
 */

import {
  ACCEPTED_SCHEMA_VERSIONS,
  ACCEPTED_USER_ROLES,
  LANE_MEMORY_MB_BOUNDS,
  SLUG_PATTERN,
  type LaneMemoryMb,
  type MachineSpec,
  type User,
  type UserRole,
  type ValidationError,
} from './types'
import { isPlainObject } from './helpers'

/**
 * Per-user voice profile slug — validates against SLUG_PATTERN and
 * enforces uniqueness across users[]. Sharing a profile defeats the
 * per-user attribution model, so duplicates are an authoring error.
 *
 * Returns the slug on success, '' when the field is absent (legacy
 * users continue to work), or null when the field is present but
 * invalid (the matching ValidationError has been pushed).
 */
function checkVoiceProfileId(
  raw: unknown,
  i: number,
  seenVoiceProfileIds: Set<string>,
  errors: ValidationError[]
): string | null {
  if (raw === undefined || raw === null) return null
  if (typeof raw !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: `users[${i}].voice_profile_id`,
      message: 'users[].voice_profile_id must be a string when present',
    })
    return null
  }
  if (!SLUG_PATTERN.test(raw)) {
    errors.push({
      code: 'InvalidSlug',
      path: `users[${i}].voice_profile_id`,
      message: 'users[].voice_profile_id must match ^[a-z0-9][a-z0-9-]{0,31}$',
    })
    return null
  }
  if (seenVoiceProfileIds.has(raw)) {
    errors.push({
      code: 'DuplicateVoiceProfileId',
      path: `users[${i}].voice_profile_id`,
      message: `users[].voice_profile_id "${raw}" is duplicated; two users cannot share a voice profile`,
    })
    return raw
  }
  seenVoiceProfileIds.add(raw)
  return raw
}

export function checkSchemaVersion(
  root: Record<string, unknown>,
  errors: ValidationError[]
): number {
  const v = root['schema_version']
  if (v === undefined || v === null) {
    errors.push({
      code: 'MissingField',
      path: 'schema_version',
      message: 'schema_version is required (must be 1)',
    })
    return 0
  }
  if (typeof v !== 'number' || !Number.isInteger(v)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'schema_version',
      message: 'schema_version must be an integer',
    })
    return 0
  }
  if (!(ACCEPTED_SCHEMA_VERSIONS as readonly number[]).includes(v)) {
    errors.push({
      code: 'SchemaVersionUnsupported',
      path: 'schema_version',
      message: `schema_version ${v} is not in the accepted set [${ACCEPTED_SCHEMA_VERSIONS.join(', ')}]`,
    })
    return 0
  }
  return v
}

export function checkCustomerId(
  root: Record<string, unknown>,
  errors: ValidationError[]
): string | null {
  const id = root['customer_id']
  if (id === undefined || id === null) {
    errors.push({
      code: 'MissingField',
      path: 'customer_id',
      message: 'customer_id is required',
    })
    return null
  }
  if (typeof id !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: 'customer_id',
      message: 'customer_id must be a string',
    })
    return null
  }
  if (id.length === 0) {
    errors.push({
      code: 'EmptyField',
      path: 'customer_id',
      message: 'customer_id must not be empty',
    })
    return null
  }
  if (!SLUG_PATTERN.test(id)) {
    errors.push({
      code: 'InvalidSlug',
      path: 'customer_id',
      message: 'customer_id must match ^[a-z0-9][a-z0-9-]{0,31}$',
    })
    return null
  }
  return id
}

export function checkPracticeAreas(
  root: Record<string, unknown>,
  vertical: string | null,
  errors: ValidationError[]
): string[] {
  const raw = root['practice_areas']
  if (raw === undefined || raw === null) {
    if (vertical === 'law-firm') {
      errors.push({
        code: 'MissingField',
        path: 'practice_areas',
        message: 'practice_areas is required when vertical=law-firm',
      })
    }
    return []
  }
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'practice_areas',
      message: 'practice_areas must be a list of strings',
    })
    return []
  }
  const out: string[] = []
  for (let i = 0; i < raw.length; i++) {
    const item: unknown = raw[i]
    if (typeof item !== 'string' || item.length === 0) {
      errors.push({
        code: 'TypeMismatch',
        path: `practice_areas[${i}]`,
        message: 'practice_areas entries must be non-empty strings',
      })
      continue
    }
    out.push(item)
  }
  return out
}

export function checkMachine(
  root: Record<string, unknown>,
  errors: ValidationError[]
): MachineSpec | null {
  const m = root['machine']
  if (m === undefined || m === null) {
    errors.push({ code: 'MissingField', path: 'machine', message: 'machine is required' })
    return null
  }
  if (!isPlainObject(m)) {
    errors.push({ code: 'TypeMismatch', path: 'machine', message: 'machine must be an object' })
    return null
  }
  return checkMachineFields(m, errors)
}

function checkMachineFields(
  m: Record<string, unknown>,
  errors: ValidationError[]
): MachineSpec | null {
  const size = m['size']
  const mem = m['memory_mb']
  let ok = true
  if (typeof size !== 'string' || size.length === 0) {
    errors.push({
      code: 'MissingField',
      path: 'machine.size',
      message: 'machine.size is required',
    })
    ok = false
  }
  if (typeof mem !== 'number' || !Number.isInteger(mem)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'machine.memory_mb',
      message: 'machine.memory_mb must be an integer',
    })
    ok = false
  } else if (mem < 256 || mem > 8192) {
    errors.push({
      code: 'EnumViolation',
      path: 'machine.memory_mb',
      message: 'machine.memory_mb must be between 256 and 8192',
    })
    ok = false
  }
  const lanes = checkLaneMemory(m['lane_memory_mb'], errors)
  if (lanes === false || !ok) return null
  const spec: MachineSpec = { size: size as string, memory_mb: mem as number }
  if (lanes) spec.lane_memory_mb = lanes
  return spec
}

const LANE_MEMORY_KEYS = ['medchron', 'demand'] as const

function checkLaneMemoryValue(
  key: (typeof LANE_MEMORY_KEYS)[number],
  v: unknown,
  errors: ValidationError[]
): boolean {
  const path = `machine.lane_memory_mb.${key}`
  if (typeof v !== 'number' || !Number.isInteger(v)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be an integer (MiB)` })
    return false
  }
  if (v < LANE_MEMORY_MB_BOUNDS.min || v > LANE_MEMORY_MB_BOUNDS.max) {
    errors.push({
      code: 'EnumViolation',
      path,
      message: `${path} must be between ${LANE_MEMORY_MB_BOUNDS.min} and ${LANE_MEMORY_MB_BOUNDS.max}`,
    })
    return false
  }
  return true
}

/**
 * `machine.lane_memory_mb`: optional; each lane optional, an integer MiB in
 * bounds. Returns undefined when unauthored, false when invalid (errors pushed).
 */
function checkLaneMemory(
  raw: unknown,
  errors: ValidationError[]
): LaneMemoryMb | undefined | false {
  if (raw === undefined || raw === null) return undefined
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'machine.lane_memory_mb',
      message: 'machine.lane_memory_mb must be an object',
    })
    return false
  }
  let ok = true
  for (const key of Object.keys(raw)) {
    if (!(LANE_MEMORY_KEYS as readonly string[]).includes(key)) {
      errors.push({
        code: 'EnumViolation',
        path: `machine.lane_memory_mb.${key}`,
        message: `machine.lane_memory_mb.${key}: unknown lane (known: ${LANE_MEMORY_KEYS.join(', ')})`,
      })
      ok = false
    }
  }
  const out: LaneMemoryMb = {}
  for (const key of LANE_MEMORY_KEYS) {
    const v = raw[key]
    if (v === undefined) continue
    if (checkLaneMemoryValue(key, v, errors)) out[key] = v as number
    else ok = false
  }
  return ok ? out : false
}

export function checkUsers(root: Record<string, unknown>, errors: ValidationError[]): User[] {
  const raw = root['users']
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path: 'users', message: 'users is required' })
    return []
  }
  if (!Array.isArray(raw)) {
    errors.push({ code: 'TypeMismatch', path: 'users', message: 'users must be a list' })
    return []
  }
  if (raw.length === 0) {
    errors.push({
      code: 'EmptyList',
      path: 'users',
      message: 'users must contain at least one entry',
    })
    return []
  }
  const out: User[] = []
  const seenVoiceProfileIds = new Set<string>()
  for (let i = 0; i < raw.length; i++) {
    const u = checkOneUser(raw[i], i, seenVoiceProfileIds, errors)
    if (u !== null) out.push(u)
  }
  return out
}

function checkOneUser(
  u: unknown,
  i: number,
  seenVoiceProfileIds: Set<string>,
  errors: ValidationError[]
): User | null {
  if (!isPlainObject(u)) {
    errors.push({
      code: 'TypeMismatch',
      path: `users[${i}]`,
      message: 'users entries must be objects',
    })
    return null
  }
  const email = u['email']
  const role = u['role']
  const fullName = u['full_name']
  let valid = true
  if (typeof email !== 'string' || email.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `users[${i}].email`,
      message: 'users[].email is required',
    })
    valid = false
  }
  if (typeof role !== 'string' || !(ACCEPTED_USER_ROLES as readonly string[]).includes(role)) {
    errors.push({
      code: 'EnumViolation',
      path: `users[${i}].role`,
      message: `users[].role must be one of: ${ACCEPTED_USER_ROLES.join(', ')}`,
    })
    valid = false
  }
  if (typeof fullName !== 'string' || fullName.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `users[${i}].full_name`,
      message: 'users[].full_name is required',
    })
    valid = false
  }
  const voiceProfileId = checkVoiceProfileId(u['voice_profile_id'], i, seenVoiceProfileIds, errors)
  if (!valid) return null
  return {
    email: email as string,
    role: role as UserRole,
    full_name: fullName as string,
    voice_profile_id: voiceProfileId,
  }
}
