/**
 * Pipeline-settings DAL — admin-tunable thresholds and caps for the four
 * lead-gen workers (review_mining, job_monitor, new_business,
 * social_listening). Closes issue #595.
 *
 * Design intent
 * -------------
 *  - Workers call `getPipelineSettings(db, orgId, pipeline)` at the TOP of
 *    each invocation (cron or fetch). This means the next run picks up
 *    the latest admin-set values without a worker restart. Reads at
 *    module-load time would defeat the purpose.
 *  - When a setting row is absent, fall back to the hardcoded default in
 *    the registry below. Never throw on missing rows — admin may not have
 *    touched anything yet, and the worker must still run.
 *  - Defaults are pinned to the values that shipped before this migration,
 *    so the deploy is a no-op until ops explicitly tunes a value.
 *  - Validation lives in `SETTING_SPECS`. Each tunable declares a parser
 *    (string -> number / int / etc.), an optional range, and a default.
 *    `updatePipelineSettings` rejects out-of-range or unparseable values
 *    rather than silently coercing.
 *  - Every change writes a row to `pipeline_settings_audit`. This is the
 *    forensic trail for "who lowered the threshold to 3?"
 *
 * The shape of `PipelineSettings` is the canonical, fully-resolved view
 * — every tunable is present, with either the stored value or the default
 * filled in. Workers and the admin UI both consume this same shape.
 */

export type PipelineId = 'review_mining' | 'job_monitor' | 'new_business' | 'social_listening'

export const PIPELINE_IDS: readonly PipelineId[] = [
  'review_mining',
  'job_monitor',
  'new_business',
  'social_listening',
] as const

// ---------------------------------------------------------------------------
// Setting registry — the source of truth for tunables, defaults, ranges.
// ---------------------------------------------------------------------------

export interface NumericSettingSpec {
  type: 'int' | 'float'
  default: number
  min: number
  max: number
  label: string
  help: string
}

export type SettingSpec = NumericSettingSpec

/**
 * Registry of all admin-tunable settings, by pipeline.
 *
 * Defaults match the constants that shipped before this migration. Do NOT
 * change a default casually — it is the deployed behavior when a fresh org
 * has no settings rows. Range bounds (`min`/`max`) are validated on write
 * by `updatePipelineSettings`.
 */
export const SETTING_SPECS: Record<PipelineId, Record<string, SettingSpec>> = {
  review_mining: {
    pain_threshold: {
      type: 'int',
      default: 7,
      min: 1,
      max: 10,
      label: 'Pain threshold',
      help: 'Minimum Claude pain_score (1-10) for a business to qualify and be written to D1. Lower = more leads, lower signal quality.',
    },
    max_review_checks: {
      type: 'int',
      default: 200,
      min: 1,
      max: 5000,
      label: 'Max review checks per run',
      help: 'Cap on businesses sent to Outscraper per run. Each check is ~$0.003 of Outscraper spend.',
    },
    outscraper_budget_usd_per_run: {
      type: 'float',
      default: 1.0,
      min: 0.01,
      max: 100.0,
      label: 'Outscraper budget per run (USD)',
      help: 'Belt-and-suspenders cost guard. Stops the loop early if projected Outscraper spend would exceed this dollar value.',
    },
  },
  job_monitor: {
    pain_threshold: {
      type: 'int',
      default: 7,
      min: 1,
      max: 10,
      label: 'Pain threshold',
      help: 'Minimum derived pain score (1-10) for a job posting to qualify and be written to D1. Score is derived from Claude confidence + problem count. Lower = more leads, lower signal quality.',
    },
  },
  new_business: {
    pain_threshold: {
      type: 'int',
      default: 1,
      min: 0,
      max: 10,
      label: 'Pain threshold',
      help: 'Minimum derived score for a permit to qualify (0-10). Score is derived from Claude outreach_timing: immediate=10, wait_30_days=7, wait_60_days=5, not_recommended=0. Default 1 preserves prior behavior (skip only not_recommended).',
    },
  },
  social_listening: {},
}

// ---------------------------------------------------------------------------
// Resolved-settings shape per pipeline (every tunable, with default if unset)
// ---------------------------------------------------------------------------

export interface ReviewMiningSettings {
  pain_threshold: number
  max_review_checks: number
  outscraper_budget_usd_per_run: number
}

export interface JobMonitorSettings {
  pain_threshold: number
}

export interface NewBusinessSettings {
  pain_threshold: number
}

// Reserved — no tunables yet for social_listening.
export type SocialListeningSettings = Record<string, never>

export type PipelineSettings<P extends PipelineId> = P extends 'review_mining'
  ? ReviewMiningSettings
  : P extends 'job_monitor'
    ? JobMonitorSettings
    : P extends 'new_business'
      ? NewBusinessSettings
      : SocialListeningSettings

// ---------------------------------------------------------------------------
// D1 access
// ---------------------------------------------------------------------------

interface RawRow {
  pipeline: PipelineId
  key: string
  value: string
  updated_at: string
  updated_by: string | null
}

/**
 * Parse a stored TEXT value into the type declared by the registry.
 * On parse failure, return the registry default (graceful degradation —
 * workers still run with sensible values even if a row is corrupt).
 */
function parseValue(spec: SettingSpec, raw: string): number {
  if (spec.type === 'int') {
    const n = Number.parseInt(raw, 10)
    if (!Number.isFinite(n)) return spec.default
    return n
  }
  // float
  const n = Number.parseFloat(raw)
  if (!Number.isFinite(n)) return spec.default
  return n
}

/**
 * Read all tunables for one pipeline in one query, merge with defaults,
 * return the canonical resolved shape.
 *
 * Workers call this at the top of every invocation. Cheap query (4 rows
 * max for review_mining today), idempotent, no caching required.
 */
export async function getPipelineSettings<P extends PipelineId>(
  db: D1Database,
  orgId: string,
  pipeline: P
): Promise<PipelineSettings<P>> {
  const specs = SETTING_SPECS[pipeline]
  const out: Record<string, number> = {}
  for (const [key, spec] of Object.entries(specs)) {
    out[key] = spec.default
  }

  const rows = await db
    .prepare(
      `SELECT pipeline, key, value, updated_at, updated_by
       FROM pipeline_settings
       WHERE org_id = ? AND pipeline = ?`
    )
    .bind(orgId, pipeline)
    .all<RawRow>()

  for (const row of rows.results ?? []) {
    const spec = specs[row.key]
    if (!spec) continue // unknown key — ignore
    out[row.key] = parseValue(spec, row.value)
  }

  return out as unknown as PipelineSettings<P>
}

/**
 * Read the raw rows (with metadata: updated_at, updated_by) for the admin
 * UI — distinguishes "stored value" from "default fallback" so the form
 * can show provenance.
 */
export interface RawSetting {
  key: string
  value: string
  updated_at: string
  updated_by: string | null
}

export async function getPipelineSettingsRaw(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId
): Promise<RawSetting[]> {
  const rows = await db
    .prepare(
      `SELECT key, value, updated_at, updated_by
       FROM pipeline_settings
       WHERE org_id = ? AND pipeline = ?
       ORDER BY key`
    )
    .bind(orgId, pipeline)
    .all<RawSetting>()
  return rows.results ?? []
}

// ---------------------------------------------------------------------------
// Write path
// ---------------------------------------------------------------------------

export interface UpdateInput {
  key: string
  value: number
}

export interface UpdateResult {
  ok: boolean
  errors: string[]
  changed: number
}

export interface Actor {
  userId: string | null
  email: string | null
}

/**
 * Validate and persist a batch of setting changes for one pipeline.
 * Each change is checked against the registry (key must be known, value
 * must be in range). On any error the entire batch is rejected and
 * `errors[]` is populated — partial saves would let the admin walk away
 * thinking everything saved.
 *
 * Audit rows are written for every actually-changed key. No-op writes
 * (value identical to stored) skip the audit row. Auth/role enforcement
 * is the caller's responsibility (the admin page is behind middleware).
 */
export async function updatePipelineSettings(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId,
  inputs: UpdateInput[],
  actor: Actor
): Promise<UpdateResult> {
  const specs = SETTING_SPECS[pipeline]
  const errors: string[] = []

  for (const input of inputs) {
    const spec = specs[input.key]
    if (!spec) {
      errors.push(`Unknown setting "${input.key}" for pipeline ${pipeline}`)
      continue
    }
    if (!Number.isFinite(input.value)) {
      errors.push(`${spec.label}: value must be a number`)
      continue
    }
    if (spec.type === 'int' && !Number.isInteger(input.value)) {
      errors.push(`${spec.label}: must be a whole number`)
      continue
    }
    if (input.value < spec.min || input.value > spec.max) {
      errors.push(`${spec.label}: must be between ${spec.min} and ${spec.max}`)
    }
  }

  if (errors.length > 0) {
    return { ok: false, errors, changed: 0 }
  }

  // Read existing values so we can populate audit `old_value` and skip
  // no-op writes. One round-trip up front is cheaper than per-key reads.
  const existingRows = await db
    .prepare(`SELECT key, value FROM pipeline_settings WHERE org_id = ? AND pipeline = ?`)
    .bind(orgId, pipeline)
    .all<{ key: string; value: string }>()
  const existingByKey = new Map<string, string>()
  for (const r of existingRows.results ?? []) existingByKey.set(r.key, r.value)

  const now = new Date().toISOString()
  let changed = 0

  for (const input of inputs) {
    const newValueStr = String(input.value)
    const oldValue = existingByKey.get(input.key) ?? null
    if (oldValue === newValueStr) continue

    const id = crypto.randomUUID()
    await db
      .prepare(
        `INSERT INTO pipeline_settings (id, org_id, pipeline, key, value, updated_at, updated_by)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(org_id, pipeline, key) DO UPDATE SET
           value = excluded.value,
           updated_at = excluded.updated_at,
           updated_by = excluded.updated_by`
      )
      .bind(id, orgId, pipeline, input.key, newValueStr, now, actor.email ?? actor.userId)
      .run()

    const auditId = crypto.randomUUID()
    await db
      .prepare(
        `INSERT INTO pipeline_settings_audit (
           id, org_id, pipeline, key, old_value, new_value,
           actor_user_id, actor_email, changed_at
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        auditId,
        orgId,
        pipeline,
        input.key,
        oldValue,
        newValueStr,
        actor.userId,
        actor.email,
        now
      )
      .run()

    changed++
  }

  return { ok: true, errors: [], changed }
}

// ---------------------------------------------------------------------------
// Audit log read
// ---------------------------------------------------------------------------

export interface AuditRow {
  id: string
  pipeline: PipelineId
  key: string
  old_value: string | null
  new_value: string
  actor_user_id: string | null
  actor_email: string | null
  changed_at: string
}

export async function listPipelineSettingsAudit(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId,
  limit = 25
): Promise<AuditRow[]> {
  const rows = await db
    .prepare(
      `SELECT id, pipeline, key, old_value, new_value,
              actor_user_id, actor_email, changed_at
       FROM pipeline_settings_audit
       WHERE org_id = ? AND pipeline = ?
       ORDER BY changed_at DESC
       LIMIT ?`
    )
    .bind(orgId, pipeline, limit)
    .all<AuditRow>()
  return rows.results ?? []
}
