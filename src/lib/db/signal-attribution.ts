/**
 * Signal-attribution helpers for lifecycle artifacts (#589).
 *
 * "Originating signal" is the context-row id (type='signal') that an
 * admin attributes a meeting/quote/engagement to. Without this link we
 * can count signals per pipeline and revenue per engagement, but never
 * cross the two — i.e. ROI per pipeline is unknowable.
 *
 * Signals are stored as `context` rows (see migration 0008 + context DAL)
 * — not their own table — so attribution FKs point at `context.id`.
 *
 * All queries are parameterized and scoped by `org_id` per the P0 #399
 * cross-tenant isolation rule. NULL `originating_signal_id` is valid and
 * means "unattributed" (inbound referral, pre-migration row, or admin
 * intentionally cleared the attribution).
 */

import type { ContextEntry } from './context'

export interface SignalSummary {
  /** Context row id of a signal — the value stored in `originating_signal_id`. */
  id: string
  /** When the signal was logged (context.created_at). */
  created_at: string
  /** The pipeline that produced the signal (context.source). */
  source_pipeline: string
  /** The signal body (context.content) — truncated by callers if needed. */
  content: string
}

/**
 * Return the signals on file for an entity, most recent first.
 *
 * "Signal" here means a context row of type='signal'. The most recent
 * signal is the default attribution choice for new artifacts created
 * against this entity. Empty array means the entity has no signals —
 * artifact creation must leave `originating_signal_id` NULL.
 *
 * Org-scoped to enforce tenant isolation (#399).
 */
export async function listSignalsForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<SignalSummary[]> {
  const result = await db
    .prepare(
      `SELECT id, created_at, source AS source_pipeline, content
       FROM context
       WHERE org_id = ? AND entity_id = ? AND type = 'signal'
       ORDER BY created_at DESC`
    )
    .bind(orgId, entityId)
    .all<SignalSummary>()
  return result.results ?? []
}

/**
 * Return the most recent qualified signal for an entity, or null if the
 * entity has no signals. "Qualified" today means any context row of
 * type='signal' — the ingest endpoint already enforces a closed set of
 * pipelines (review_mining, job_monitor, new_business, social_listening).
 * If a future qualifier (e.g. pain_score >= N) is needed it goes in this
 * one query, not the call sites.
 *
 * Used as the default attribution at artifact-creation time when the
 * caller doesn't supply an explicit `originating_signal_id`.
 */
export async function getDefaultOriginatingSignalId(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<string | null> {
  const row = await db
    .prepare(
      `SELECT id FROM context
       WHERE org_id = ? AND entity_id = ? AND type = 'signal'
       ORDER BY created_at DESC
       LIMIT 1`
    )
    .bind(orgId, entityId)
    .first<{ id: string }>()
  return row?.id ?? null
}

/**
 * Resolve a context-row id back to its signal record, scoped to the org.
 * Returns null if the id doesn't exist, isn't a signal, or belongs to a
 * different org. Callers should reject mismatches before persisting an
 * attribution choice — D1 would silently store a dangling FK otherwise
 * (D1's FK enforcement is on by default but the column is nullable, and
 * we want the "wrong org" path to be a clean validation error rather
 * than an opaque constraint failure).
 */
export async function getSignalById(
  db: D1Database,
  orgId: string,
  signalId: string
): Promise<ContextEntry | null> {
  const row = await db
    .prepare(`SELECT * FROM context WHERE id = ? AND org_id = ? AND type = 'signal'`)
    .bind(signalId, orgId)
    .first<ContextEntry>()
  return row ?? null
}

export interface SignalPipelineRevenueRow {
  /** Pipeline value from context.source (review_mining / job_monitor / ...). */
  source_pipeline: string
  /** Engagements attributed to a signal from this pipeline. */
  engagement_count: number
  /** Sum of engagements.estimated_hours across the bucket (NULL hours skipped). */
  total_estimated_hours: number
  /** Sum of engagements.actual_hours across the bucket. */
  total_actual_hours: number
}

/**
 * Group engagements by the source pipeline of their originating signal.
 * Read-only attribution roll-up powering the (forthcoming) ROI dashboard.
 *
 * Engagements with NULL `originating_signal_id` are excluded — they can't
 * be attributed by definition. The dashboard surface is responsible for
 * showing the unattributed count separately if it wants to.
 *
 * Pipeline names come from `context.source` rather than a hardcoded enum
 * because the pipeline list lives in the ingest endpoint's `ALLOWED_PIPELINES`
 * constant; binding the SQL to that enum here would couple the DAL to the
 * API contract and force a code change every time we add a pipeline.
 */
export async function getEngagementsBySourcePipeline(
  db: D1Database,
  orgId: string
): Promise<SignalPipelineRevenueRow[]> {
  const result = await db
    .prepare(
      `SELECT
         c.source AS source_pipeline,
         COUNT(e.id) AS engagement_count,
         COALESCE(SUM(e.estimated_hours), 0) AS total_estimated_hours,
         COALESCE(SUM(e.actual_hours), 0) AS total_actual_hours
       FROM engagements e
       JOIN context c
         ON c.id = e.originating_signal_id
        AND c.type = 'signal'
        AND c.org_id = e.org_id
       WHERE e.org_id = ?
       GROUP BY c.source
       ORDER BY engagement_count DESC, c.source ASC`
    )
    .bind(orgId)
    .all<SignalPipelineRevenueRow>()
  return result.results ?? []
}
