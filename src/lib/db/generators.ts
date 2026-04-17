/**
 * Generator config DAL.
 *
 * Read path: getGeneratorConfig / listGeneratorConfigs always return a
 * usable config (merged with defaults) plus a parseError array. If the
 * JSON blob is corrupt or violates invariants, errors are surfaced —
 * we NEVER silently revert to defaults without visible indication.
 *
 * Write path: upsertGeneratorConfig validates first, then writes the
 * canonical JSON string it produced. Malformed input is rejected with
 * errors — the form shows them to the admin rather than persisting.
 *
 * Run path: recordGeneratorRun is called by each worker at the end of
 * its invocation to populate last_run_at / last_run_signals_count /
 * last_run_error. This is the authoritative liveness indicator.
 */

import { DEFAULTS, type PipelineId } from '../generators/types.js'
import { validateByPipeline } from '../generators/validate.js'

export interface GeneratorConfigRow {
  id: string
  pipeline: PipelineId
  enabled: boolean
  config: unknown
  parseError: string[]
  last_run_at: string | null
  last_run_signals_count: number | null
  last_run_error: string | null
  created_at: string
  updated_at: string
}

interface RawRow {
  id: string
  pipeline: PipelineId
  enabled: number
  config_json: string
  last_run_at: string | null
  last_run_signals_count: number | null
  last_run_error: string | null
  created_at: string
  updated_at: string
}

function hydrate(row: RawRow): GeneratorConfigRow {
  let parsed: unknown = null
  let parseError: string[] = []
  try {
    parsed = JSON.parse(row.config_json)
  } catch (e) {
    parseError.push(`Corrupt JSON in stored config: ${e instanceof Error ? e.message : String(e)}`)
    parsed = {}
  }
  const result = validateByPipeline(row.pipeline, parsed)
  if (result.errors.length > 0) {
    parseError = [...parseError, ...result.errors]
  }
  return {
    id: row.id,
    pipeline: row.pipeline,
    enabled: row.enabled === 1,
    config: result.value,
    parseError,
    last_run_at: row.last_run_at,
    last_run_signals_count: row.last_run_signals_count,
    last_run_error: row.last_run_error,
    created_at: row.created_at,
    updated_at: row.updated_at,
  }
}

function defaultRow(pipeline: PipelineId): GeneratorConfigRow {
  return {
    id: '',
    pipeline,
    enabled: true,
    config: DEFAULTS[pipeline],
    parseError: [],
    last_run_at: null,
    last_run_signals_count: null,
    last_run_error: null,
    created_at: '',
    updated_at: '',
  }
}

export async function getGeneratorConfig(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId
): Promise<GeneratorConfigRow> {
  const row = await db
    .prepare(
      `SELECT id, pipeline, enabled, config_json,
              last_run_at, last_run_signals_count, last_run_error,
              created_at, updated_at
       FROM generator_config
       WHERE org_id = ? AND pipeline = ?`
    )
    .bind(orgId, pipeline)
    .first<RawRow>()

  if (!row) return defaultRow(pipeline)
  return hydrate(row)
}

export async function listGeneratorConfigs(
  db: D1Database,
  orgId: string
): Promise<GeneratorConfigRow[]> {
  const rows = await db
    .prepare(
      `SELECT id, pipeline, enabled, config_json,
              last_run_at, last_run_signals_count, last_run_error,
              created_at, updated_at
       FROM generator_config
       WHERE org_id = ?`
    )
    .bind(orgId)
    .all<RawRow>()

  const byPipeline = new Map<PipelineId, GeneratorConfigRow>()
  for (const row of rows.results ?? []) {
    byPipeline.set(row.pipeline, hydrate(row))
  }

  const out: GeneratorConfigRow[] = []
  for (const p of ['new_business', 'job_monitor', 'review_mining', 'social_listening'] as const) {
    out.push(byPipeline.get(p) ?? defaultRow(p))
  }
  return out
}

export interface UpsertResult {
  ok: boolean
  errors: string[]
}

export async function upsertGeneratorConfig(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId,
  config: unknown,
  enabled: boolean
): Promise<UpsertResult> {
  const result = validateByPipeline(pipeline, config)
  if (result.errors.length > 0) {
    return { ok: false, errors: result.errors }
  }
  const canonical = JSON.stringify(result.value)
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO generator_config (
         id, org_id, pipeline, enabled, config_json, created_at, updated_at
       )
       VALUES (?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(org_id, pipeline) DO UPDATE SET
         enabled = excluded.enabled,
         config_json = excluded.config_json,
         updated_at = excluded.updated_at`
    )
    .bind(id, orgId, pipeline, enabled ? 1 : 0, canonical, now, now)
    .run()

  return { ok: true, errors: [] }
}

export async function recordGeneratorRun(
  db: D1Database,
  orgId: string,
  pipeline: PipelineId,
  result: { signalsCount: number; error: string | null }
): Promise<void> {
  const now = new Date().toISOString()
  // Create row on first run if config hasn't been touched yet — so Run-now
  // records liveness even when the admin hasn't saved custom config.
  const id = crypto.randomUUID()
  const defaultJson = JSON.stringify(DEFAULTS[pipeline])
  await db
    .prepare(
      `INSERT INTO generator_config (
         id, org_id, pipeline, enabled, config_json,
         last_run_at, last_run_signals_count, last_run_error,
         created_at, updated_at
       )
       VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(org_id, pipeline) DO UPDATE SET
         last_run_at = excluded.last_run_at,
         last_run_signals_count = excluded.last_run_signals_count,
         last_run_error = excluded.last_run_error,
         updated_at = excluded.updated_at`
    )
    .bind(id, orgId, pipeline, defaultJson, now, result.signalsCount, result.error, now, now)
    .run()
}
