/**
 * `case_manager:` block validator (spec
 * docs/specs/operator/case-manager-deadline-work.md, schema doc
 * docs/specs/operator/customer-yaml-schema.md "case_manager").
 *
 * The block turns on the case-manager deadline jobs, each at the level the
 * firm chose. Absent is a real authored state, not an error: every job is off
 * and the deadline escalator behaves exactly as it did before the block
 * existed (ADR 0035, no imposed defaults). Its runtime readers are the skills'
 * own pre_run scripts, which read the seat's /var/lib/smd-config/customer.yaml
 * directly (task-list-keeper, date-prep-brief, deadline-miss-escalator).
 *
 * STRICT on purpose. An unknown key is refused rather than ignored, because a
 * misspelled sub-block (`date_perp:`) would otherwise read as "that job is
 * off" and nobody would learn why the firm's prep never arrived.
 */

import {
  ACCEPTED_CASE_MANAGER_LEVELS,
  ACCEPTED_DATE_PREP_STEPS,
  type CaseManager,
  type CaseManagerDatePrep,
  type CaseManagerLevel,
  type CaseManagerOwnTasks,
  type CaseManagerQuiet,
  type CaseManagerTaskCleanup,
  type DatePrepStep,
  type ValidationError,
} from './types'
import { isPlainObject } from './helpers'

const ROOT = 'case_manager'
const TOP_KEYS = ['own_tasks', 'task_cleanup', 'date_prep', 'quiet'] as const

/** Ceilings. Past these a value is a typo, not a posture. */
const MAX_WINDOW_DAYS = 90
const MAX_KEEP_QUIET_DAYS = 365
const MAX_LINES = 30
const MAX_LEGACY_TASK_IDS = 200

function fail(errors: ValidationError[], path: string, message: string): null {
  errors.push({ code: 'InvalidCaseManager', path, message })
  return null
}

function unknownKeys(
  raw: Record<string, unknown>,
  allowed: readonly string[],
  path: string,
  errors: ValidationError[]
): boolean {
  let clean = true
  for (const key of Object.keys(raw)) {
    if (!allowed.includes(key)) {
      fail(
        errors,
        `${path}.${key}`,
        `${path}.${key} is not a case_manager field (allowed: ${allowed.join(', ')})`
      )
      clean = false
    }
  }
  return clean
}

function level(raw: unknown, path: string, errors: ValidationError[]): CaseManagerLevel | null {
  if (
    typeof raw === 'string' &&
    (ACCEPTED_CASE_MANAGER_LEVELS as readonly string[]).includes(raw)
  ) {
    return raw as CaseManagerLevel
  }
  return fail(errors, path, `${path} must be one of: ${ACCEPTED_CASE_MANAGER_LEVELS.join(', ')}`)
}

function posInt(
  raw: unknown,
  max: number,
  path: string,
  errors: ValidationError[],
  required: boolean
): number | null {
  if (raw === undefined || raw === null) {
    return required ? fail(errors, path, `${path} is required`) : null
  }
  if (typeof raw !== 'number' || !Number.isInteger(raw) || raw < 1 || raw > max) {
    return fail(errors, path, `${path} must be a whole number from 1 to ${max}`)
  }
  return raw
}

/** The object at `root[key]`, null when absent, or an error when not a mapping. */
function subBlock(
  root: Record<string, unknown>,
  key: string,
  allowed: readonly string[],
  errors: ValidationError[]
): Record<string, unknown> | null {
  const raw = root[key]
  if (raw === undefined || raw === null) return null
  const path = `${ROOT}.${key}`
  if (!isPlainObject(raw)) return fail(errors, path, `${path} must be a mapping`)
  return unknownKeys(raw, allowed, path, errors) ? raw : null
}

function checkOwnTasks(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CaseManagerOwnTasks | null {
  const raw = subBlock(root, 'own_tasks', ['level', 'legacy_task_ids'], errors)
  if (raw === null) return null
  const path = `${ROOT}.own_tasks.legacy_task_ids`
  const lvl = level(raw['level'], `${ROOT}.own_tasks.level`, errors)
  const ids = raw['legacy_task_ids'] ?? []
  if (!Array.isArray(ids) || !ids.every((id) => typeof id === 'string' && id.trim() !== '')) {
    return fail(errors, path, `${path} must be a list of non-empty Smokeball task ids`)
  }
  if (ids.length > MAX_LEGACY_TASK_IDS) {
    return fail(errors, path, `${path} holds at most ${MAX_LEGACY_TASK_IDS} ids`)
  }
  if (new Set(ids).size !== ids.length) return fail(errors, path, `${path} lists an id twice`)
  return lvl === null ? null : { level: lvl, legacy_task_ids: ids as string[] }
}

function checkTaskCleanup(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CaseManagerTaskCleanup | null {
  const raw = subBlock(root, 'task_cleanup', ['level', 'keep_quiet_days', 'max_lines'], errors)
  if (raw === null) return null
  const base = `${ROOT}.task_cleanup`
  const lvl = level(raw['level'], `${base}.level`, errors)
  const before = errors.length
  const quiet = posInt(
    raw['keep_quiet_days'],
    MAX_KEEP_QUIET_DAYS,
    `${base}.keep_quiet_days`,
    errors,
    false
  )
  const lines = posInt(raw['max_lines'], MAX_LINES, `${base}.max_lines`, errors, false)
  if (lvl === null || errors.length > before) return null
  return { level: lvl, keep_quiet_days: quiet, max_lines: lines }
}

const RANK: Record<CaseManagerLevel, number> = { surfaces: 0, prepares: 1, handles: 2 }

function checkSteps(
  raw: unknown,
  jobLevel: CaseManagerLevel | null,
  errors: ValidationError[]
): CaseManagerDatePrep['steps'] | null {
  const path = `${ROOT}.date_prep.steps`
  if (raw === undefined || raw === null) return {}
  if (!isPlainObject(raw)) return fail(errors, path, `${path} must be a mapping of step to level`)
  if (!unknownKeys(raw, ACCEPTED_DATE_PREP_STEPS, path, errors)) return null
  const steps: Partial<Record<DatePrepStep, CaseManagerLevel>> = {}
  for (const [step, value] of Object.entries(raw)) {
    const lvl = level(value, `${path}.${step}`, errors)
    if (lvl === null) return null
    if (jobLevel !== null && RANK[lvl] > RANK[jobLevel]) {
      return fail(
        errors,
        `${path}.${step}`,
        `${path}.${step} (${lvl}) is above date_prep.level (${jobLevel})`
      )
    }
    steps[step as DatePrepStep] = lvl
  }
  return steps
}

function checkDatePrep(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CaseManagerDatePrep | null {
  const raw = subBlock(root, 'date_prep', ['level', 'window_days', 'steps'], errors)
  if (raw === null) return null
  const base = `${ROOT}.date_prep`
  const lvl = level(raw['level'], `${base}.level`, errors)
  const windowDays = posInt(
    raw['window_days'],
    MAX_WINDOW_DAYS,
    `${base}.window_days`,
    errors,
    true
  )
  const steps = checkSteps(raw['steps'], lvl, errors)
  if (lvl === null || windowDays === null || steps === null) return null
  return { level: lvl, window_days: windowDays, steps }
}

function checkQuiet(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CaseManagerQuiet | null {
  const raw = subBlock(root, 'quiet', ['level'], errors)
  if (raw === null) return null
  const lvl = level(raw['level'], `${ROOT}.quiet.level`, errors)
  return lvl === null ? null : { level: lvl }
}

export function checkCaseManager(
  root: Record<string, unknown>,
  errors: ValidationError[]
): CaseManager | null {
  const raw = root[ROOT]
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) return fail(errors, ROOT, `${ROOT} must be a mapping`)
  if (!unknownKeys(raw, TOP_KEYS, ROOT, errors)) return null
  return {
    own_tasks: checkOwnTasks(raw, errors),
    task_cleanup: checkTaskCleanup(raw, errors),
    date_prep: checkDatePrep(raw, errors),
    quiet: checkQuiet(raw, errors),
  }
}
