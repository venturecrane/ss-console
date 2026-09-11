/**
 * Per-persona skill bundles + cron schedule validators (ADR 0021 Streams B + D).
 *
 * Lives in its own module so sections-personas.ts stays under the 500-line
 * ceiling. Both validators take the persona's already-validated skills list
 * so they can verify bundle/cron entries reference real skills.
 *
 * Bundles (Stream D):
 *   - One slash command (e.g. `/pi-intake`) loads multiple skills together.
 *   - The hermes-smd bootstrap CLI translates these into per-profile
 *     `~/.hermes/skill-bundles/<slug>.yaml` files at Machine boot.
 *
 * Cron (Stream B):
 *   - Per-skill schedule with optional no-agent pre-run script.
 *   - When `pre_run` is set the script's stdout JSON drives wakeAgent;
 *     skill authors MUST emit an audit_action="suppressed_wake" row before
 *     printing wakeAgent:false (the mirror-don't-gate principle from
 *     ADR 0016 extended to the cron-skip path).
 */

import {
  ACCEPTED_WAKE_POLICIES,
  SLUG_PATTERN,
  isAcceptedCronSchedule,
  type PersonaBundle,
  type PersonaCron,
  type PersonaSkill,
  type ValidationError,
  type WakePolicy,
} from './types'
import { isPlainObject } from './helpers'

const BUNDLE_DESCRIPTION_MAX = 200

export function checkBundles(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  errors: ValidationError[]
): PersonaBundle[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list when present` })
    return []
  }
  const out: PersonaBundle[] = []
  const seenSlugs = new Set<string>()
  for (let i = 0; i < raw.length; i++) {
    const bundle = checkOneBundle(raw[i], `${path}[${i}]`, skills, seenSlugs, errors)
    if (bundle !== null) out.push(bundle)
  }
  return out
}

function checkOneBundle(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  seenSlugs: Set<string>,
  errors: ValidationError[]
): PersonaBundle | null {
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: 'bundle entries must be objects' })
    return null
  }
  const slug = checkBundleSlug(raw['slug'], path, seenSlugs, errors)
  const description = checkBundleDescription(raw['description'], path, errors)
  const bundleSkills = checkBundleSkills(raw['skills'], path, skills, errors)
  const instruction = checkBundleInstruction(raw['instruction'], path, errors)
  if (slug === null || description === null || bundleSkills === null) return null
  return { slug, description, skills: bundleSkills, instruction }
}

function checkBundleSlug(
  raw: unknown,
  path: string,
  seenSlugs: Set<string>,
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.length === 0) {
    errors.push({ code: 'MissingField', path: `${path}.slug`, message: 'bundle.slug is required' })
    return null
  }
  if (!SLUG_PATTERN.test(raw)) {
    errors.push({
      code: 'InvalidSlug',
      path: `${path}.slug`,
      message: 'bundle.slug must match ^[a-z0-9][a-z0-9-]{0,31}$',
    })
    return null
  }
  if (seenSlugs.has(raw)) {
    errors.push({
      code: 'DuplicateBundleSlug',
      path: `${path}.slug`,
      message: `bundle.slug "${raw}" is duplicated within this persona`,
    })
    return null
  }
  seenSlugs.add(raw)
  return raw
}

function checkBundleDescription(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.description`,
      message: 'bundle.description is required',
    })
    return null
  }
  if (raw.length > BUNDLE_DESCRIPTION_MAX) {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.description`,
      message: `bundle.description must be ${BUNDLE_DESCRIPTION_MAX} chars or fewer`,
    })
    return null
  }
  return raw
}

function checkBundleSkills(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  errors: ValidationError[]
): string[] | null {
  const skillsPath = `${path}.skills`
  if (!Array.isArray(raw) || raw.length === 0) {
    errors.push({
      code: 'MissingField',
      path: skillsPath,
      message: 'bundle.skills must be a non-empty list',
    })
    return null
  }
  const enabledNames = new Set(skills.filter((s) => s.enabled).map((s) => s.name))
  const out: string[] = []
  let ok = true
  // Array.isArray narrows `unknown` to `any[]`; name the elements unknown so
  // each one is proven a string below rather than assumed.
  const entries: unknown[] = raw
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]
    if (typeof entry !== 'string' || entry.length === 0) {
      errors.push({
        code: 'TypeMismatch',
        path: `${skillsPath}[${i}]`,
        message: 'bundle.skills[] entries must be non-empty skill slugs',
      })
      ok = false
      continue
    }
    if (!enabledNames.has(entry)) {
      errors.push({
        code: 'UnknownBundleSkill',
        path: `${skillsPath}[${i}]`,
        message: `bundle references skill "${entry}" but no enabled skill with that name exists on this persona`,
      })
      ok = false
      continue
    }
    out.push(entry)
  }
  return ok ? out : null
}

function checkBundleInstruction(
  raw: unknown,
  path: string,
  errors: ValidationError[]
): string | null {
  if (raw === undefined || raw === null) return null
  if (typeof raw !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.instruction`,
      message: 'bundle.instruction must be a string when present',
    })
    return null
  }
  return raw
}

export function checkCron(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  errors: ValidationError[]
): PersonaCron[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a list when present` })
    return []
  }
  const out: PersonaCron[] = []
  for (let i = 0; i < raw.length; i++) {
    const cron = checkOneCron(raw[i], `${path}[${i}]`, skills, errors)
    if (cron !== null) out.push(cron)
  }
  return out
}

function checkOneCron(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  errors: ValidationError[]
): PersonaCron | null {
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: 'cron entries must be objects' })
    return null
  }
  const skill = checkCronSkill(raw['skill'], path, skills, errors)
  const schedule = checkCronSchedule(raw['schedule'], path, errors)
  const wakePolicy = checkWakePolicy(raw['wake_policy'], path, errors)
  const preRun = checkCronPreRun(raw['pre_run'], wakePolicy, path, errors)
  if (skill === null || schedule === null || wakePolicy === null || preRun === undefined) {
    return null
  }
  return { skill, schedule, pre_run: preRun, wake_policy: wakePolicy }
}

function checkCronSkill(
  raw: unknown,
  path: string,
  skills: PersonaSkill[],
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.skill`,
      message: 'cron.skill is required',
    })
    return null
  }
  const skill = skills.find((s) => s.enabled && s.name === raw)
  if (skill === undefined) {
    errors.push({
      code: 'UnknownCronSkill',
      path: `${path}.skill`,
      message: `cron references skill "${raw}" but no enabled skill with that name exists on this persona`,
    })
    return null
  }
  if (!skill.initiation.scheduled) {
    errors.push({
      code: 'MissingField',
      path: `${path}.skill`,
      message: `cron references skill "${raw}" but that skill does not grant initiation.scheduled`,
    })
    return null
  }
  return raw
}

function checkCronSchedule(raw: unknown, path: string, errors: ValidationError[]): string | null {
  if (typeof raw !== 'string' || raw.length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.schedule`,
      message: 'cron.schedule is required',
    })
    return null
  }
  if (!isAcceptedCronSchedule(raw)) {
    errors.push({
      code: 'InvalidCronSchedule',
      path: `${path}.schedule`,
      message:
        'cron.schedule must be a cron expression (5 fields), interval ("every 30m"), ' +
        'relative delay ("30m"), or ISO 8601 timestamp',
    })
    return null
  }
  return raw
}

function checkWakePolicy(raw: unknown, path: string, errors: ValidationError[]): WakePolicy | null {
  if (raw === undefined || raw === null) {
    errors.push({
      code: 'MissingField',
      path: `${path}.wake_policy`,
      message: `cron.wake_policy is required (one of: ${ACCEPTED_WAKE_POLICIES.join(', ')})`,
    })
    return null
  }
  if (typeof raw !== 'string' || !(ACCEPTED_WAKE_POLICIES as readonly string[]).includes(raw)) {
    errors.push({
      code: 'InvalidCronWakePolicy',
      path: `${path}.wake_policy`,
      message: `cron.wake_policy must be one of: ${ACCEPTED_WAKE_POLICIES.join(', ')}`,
    })
    return null
  }
  return raw as WakePolicy
}

function checkCronPreRun(
  raw: unknown,
  wakePolicy: WakePolicy | null,
  path: string,
  errors: ValidationError[]
): string | null | undefined {
  if (raw === undefined || raw === null) {
    if (wakePolicy === 'pre_run_decides') {
      errors.push({
        code: 'MissingField',
        path: `${path}.pre_run`,
        message: 'cron.pre_run is required when wake_policy is "pre_run_decides"',
      })
      return undefined
    }
    return null
  }
  if (typeof raw !== 'string' || raw.length === 0) {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.pre_run`,
      message: 'cron.pre_run must be a non-empty string path when present',
    })
    return undefined
  }
  if (wakePolicy === 'always') {
    errors.push({
      code: 'InvalidCronWakePolicy',
      path: `${path}.pre_run`,
      message: 'cron.pre_run must NOT be set when wake_policy is "always"',
    })
    return undefined
  }
  return raw
}
