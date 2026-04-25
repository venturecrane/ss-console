/**
 * Enrichment runs DAL.
 *
 * INVARIANT: APPEND-ONLY for completed rows. The only mutation is
 * `running → terminal status` via `completeRun`; once a row has a non-null
 * `completed_at`, it is immutable. Future history is added as new rows.
 *
 * Concurrency: `startRun` checks for an existing in-flight `running` row
 * for the same (entity_id, module) within the last 5 minutes and returns
 * `null` instead of inserting. Callers treat that as `skipped, reason:
 * 'in_progress'`. This prevents double-click money-burn on the admin
 * Retry / Run-full buttons and races between concurrent triggers
 * (e.g. cron worker fires while admin clicks Retry).
 *
 * The 5-minute ceiling on the lock means a crashed run leaves a stale
 * `running` row but the next attempt after the timeout proceeds. We do
 * not actively reap stale rows — the cost is one extra row per crash
 * and an inaccurate "still running" display in the admin UI for up to
 * 5 minutes. Worth it for not having to introduce a sweeper.
 */

import type { ModuleId } from '../enrichment/modules'

export type RunStatus = 'running' | 'succeeded' | 'no_data' | 'skipped' | 'failed'
export type RunMode = 'full' | 'reviews-and-news' | 'single'

export interface EnrichmentRun {
  id: string
  org_id: string
  entity_id: string
  module: ModuleId
  status: RunStatus
  reason: string | null
  error_message: string | null
  input_fingerprint: string | null
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  triggered_by: string
  mode: RunMode
  context_entry_id: string | null
}

export interface StartRunInput {
  org_id: string
  entity_id: string
  module: ModuleId
  mode: RunMode
  triggered_by: string
  input_fingerprint?: string | null
}

export interface CompleteRunInput {
  status: Exclude<RunStatus, 'running'>
  reason?: string | null
  error_message?: string | null
  context_entry_id?: string | null
}

const LOCK_WINDOW_MS = 5 * 60 * 1000

/**
 * Insert a `running` row, unless an in-flight row exists for the same
 * (entity_id, module) within the lock window. Returns the new run id, or
 * `null` if locked out.
 */
export async function startRun(db: D1Database, input: StartRunInput): Promise<string | null> {
  const now = new Date()
  const cutoff = new Date(now.getTime() - LOCK_WINDOW_MS).toISOString()

  const existing = await db
    .prepare(
      `SELECT id FROM enrichment_runs
       WHERE entity_id = ? AND module = ? AND status = 'running'
         AND started_at > ?
       LIMIT 1`
    )
    .bind(input.entity_id, input.module, cutoff)
    .first<{ id: string }>()

  if (existing) return null

  const id = crypto.randomUUID()
  await db
    .prepare(
      `INSERT INTO enrichment_runs (
        id, org_id, entity_id, module, status, reason, error_message,
        input_fingerprint, started_at, completed_at, duration_ms,
        triggered_by, mode, context_entry_id
      ) VALUES (?, ?, ?, ?, 'running', NULL, NULL, ?, ?, NULL, NULL, ?, ?, NULL)`
    )
    .bind(
      id,
      input.org_id,
      input.entity_id,
      input.module,
      input.input_fingerprint ?? null,
      now.toISOString(),
      input.triggered_by,
      input.mode
    )
    .run()

  return id
}

/**
 * Transition a run from `running` to a terminal status. No-op if the row
 * is already completed (i.e. completed_at is non-null) — protects against
 * double-completion races.
 */
export async function completeRun(
  db: D1Database,
  runId: string,
  input: CompleteRunInput
): Promise<void> {
  const row = await db
    .prepare('SELECT started_at, completed_at FROM enrichment_runs WHERE id = ?')
    .bind(runId)
    .first<{ started_at: string; completed_at: string | null }>()

  if (!row || row.completed_at) return

  const now = new Date()
  const startedAt = new Date(row.started_at)
  const duration = Math.max(0, now.getTime() - startedAt.getTime())

  const truncatedError = input.error_message ? input.error_message.slice(0, 512) : null

  await db
    .prepare(
      `UPDATE enrichment_runs
       SET status = ?, reason = ?, error_message = ?,
           completed_at = ?, duration_ms = ?, context_entry_id = ?
       WHERE id = ?`
    )
    .bind(
      input.status,
      input.reason ?? null,
      truncatedError,
      now.toISOString(),
      duration,
      input.context_entry_id ?? null,
      runId
    )
    .run()
}

/**
 * Return the most-recent run row per module for a given entity. Used by
 * the admin Enrichment Status panel.
 */
export async function latestRunByModule(
  db: D1Database,
  entityId: string
): Promise<Map<ModuleId, EnrichmentRun>> {
  const rows = await db
    .prepare(
      `SELECT * FROM enrichment_runs
       WHERE entity_id = ?
       ORDER BY module ASC, started_at DESC`
    )
    .bind(entityId)
    .all<EnrichmentRun>()

  const out = new Map<ModuleId, EnrichmentRun>()
  for (const row of rows.results ?? []) {
    if (!out.has(row.module)) {
      out.set(row.module, row)
    }
  }
  return out
}

/**
 * Return the most-recent SUCCESSFUL run for a single module, or null. Used
 * for narrow checks like "did review_synthesis ever succeed for this
 * entity" without loading every row.
 */
export async function lastSuccessfulRun(
  db: D1Database,
  entityId: string,
  module: ModuleId
): Promise<EnrichmentRun | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM enrichment_runs
         WHERE entity_id = ? AND module = ? AND status = 'succeeded'
         ORDER BY started_at DESC
         LIMIT 1`
      )
      .bind(entityId, module)
      .first<EnrichmentRun>()) ?? null
  )
}
