/**
 * Lead signal data access layer.
 *
 * Lead signals are pre-client intelligence surfaced by automated pipelines.
 * They sit in a triage inbox until the admin promotes them to client records
 * or dismisses them.
 *
 * All queries are parameterized to prevent SQL injection.
 * Dedup is enforced atomically via UNIQUE(org_id, dedup_key, source_pipeline).
 */

// Re-export types from lead-gen schemas for convenience
export type { PipelineId } from '../../lead-gen/schemas/lead-scoring-schema.js'

export interface LeadSignal {
  id: string
  org_id: string
  client_id: string | null
  business_name: string
  phone: string | null
  website: string | null
  category: string | null
  area: string | null
  source_pipeline: string
  pain_score: number | null
  top_problems: string | null
  evidence_summary: string | null
  outreach_angle: string | null
  source_metadata: string | null
  triage_status: string
  triage_notes: string | null
  triaged_at: string | null
  dedup_key: string
  date_found: string
  created_at: string
}

export type TriageStatus = 'new' | 'reviewed' | 'promoted' | 'dismissed'

export const TRIAGE_STATUSES: { value: TriageStatus; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'reviewed', label: 'Reviewed' },
  { value: 'promoted', label: 'Promoted' },
  { value: 'dismissed', label: 'Dismissed' },
]

const ALLOWED_PIPELINES = ['review_mining', 'job_monitor', 'new_business', 'social_listening']

export interface CreateLeadSignalData {
  business_name: string
  phone?: string | null
  website?: string | null
  category?: string | null
  area?: string | null
  source_pipeline: string
  pain_score?: number | null
  top_problems?: string[] | null
  evidence_summary?: string | null
  outreach_angle?: string | null
  source_metadata?: Record<string, unknown> | null
  date_found: string
}

export interface LeadSignalFilters {
  triage_status?: string
  source_pipeline?: string
}

export type CreateResult =
  | { status: 'created'; id: string; cross_match: boolean }
  | { status: 'duplicate'; existing_id: string }

// ---------------------------------------------------------------------------
// Dedup key computation
// ---------------------------------------------------------------------------

/**
 * Compute a normalized dedup key from business identity fields.
 * Lowercase + whitespace collapse only — no suffix stripping to avoid false positives.
 */
export function computeDedupKey(
  businessName: string,
  category: string | null,
  area: string | null
): string {
  const name = businessName.toLowerCase().replace(/\s+/g, ' ').trim()
  const cat = (category || '').toLowerCase().trim()
  const loc = (area || '').toLowerCase().trim()
  return `${name}|${cat}|${loc}`
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/**
 * Insert a lead signal with atomic dedup.
 *
 * Uses INSERT ... ON CONFLICT DO NOTHING to avoid TOCTOU races.
 * If the same business was found by a different pipeline and is already linked
 * to a client, the new signal is auto-linked to the same client.
 */
export async function createLeadSignal(
  db: D1Database,
  orgId: string,
  data: CreateLeadSignalData
): Promise<CreateResult> {
  if (!ALLOWED_PIPELINES.includes(data.source_pipeline)) {
    throw new Error(`Invalid source_pipeline: ${data.source_pipeline}`)
  }

  const id = crypto.randomUUID()
  const dedupKey = computeDedupKey(data.business_name, data.category ?? null, data.area ?? null)

  // Check for cross-pipeline match (same dedup key, different pipeline)
  const crossMatch = await db
    .prepare(
      'SELECT id, client_id FROM lead_signals WHERE org_id = ? AND dedup_key = ? AND source_pipeline != ?'
    )
    .bind(orgId, dedupKey, data.source_pipeline)
    .first<{ id: string; client_id: string | null }>()

  const clientId = crossMatch?.client_id ?? null

  // Atomic insert with conflict handling
  const result = await db
    .prepare(
      `INSERT INTO lead_signals (
        id, org_id, client_id, business_name, phone, website, category, area,
        source_pipeline, pain_score, top_problems, evidence_summary, outreach_angle,
        source_metadata, triage_status, dedup_key, date_found
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
      ON CONFLICT(org_id, dedup_key, source_pipeline) DO NOTHING`
    )
    .bind(
      id,
      orgId,
      clientId,
      data.business_name,
      data.phone ?? null,
      data.website ?? null,
      data.category ?? null,
      data.area ?? null,
      data.source_pipeline,
      data.pain_score ?? null,
      data.top_problems ? JSON.stringify(data.top_problems) : null,
      data.evidence_summary ?? null,
      data.outreach_angle ?? null,
      data.source_metadata ? JSON.stringify(data.source_metadata) : null,
      dedupKey,
      data.date_found
    )
    .run()

  // If no rows changed, it was a conflict (duplicate)
  if (!result.meta.changed_db) {
    const existing = await db
      .prepare(
        'SELECT id FROM lead_signals WHERE org_id = ? AND dedup_key = ? AND source_pipeline = ?'
      )
      .bind(orgId, dedupKey, data.source_pipeline)
      .first<{ id: string }>()

    return { status: 'duplicate', existing_id: existing?.id ?? '' }
  }

  return { status: 'created', id, cross_match: !!crossMatch }
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

/**
 * List lead signals for an organization with optional filters.
 */
export async function listLeadSignals(
  db: D1Database,
  orgId: string,
  filters?: LeadSignalFilters
): Promise<LeadSignal[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.triage_status) {
    conditions.push('triage_status = ?')
    params.push(filters.triage_status)
  }

  if (filters?.source_pipeline) {
    conditions.push('source_pipeline = ?')
    params.push(filters.source_pipeline)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM lead_signals WHERE ${where} ORDER BY pain_score DESC, date_found DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<LeadSignal>()
  return result.results
}

/**
 * Get a single lead signal by ID, scoped to an organization.
 */
export async function getLeadSignal(
  db: D1Database,
  orgId: string,
  signalId: string
): Promise<LeadSignal | null> {
  const result = await db
    .prepare('SELECT * FROM lead_signals WHERE id = ? AND org_id = ?')
    .bind(signalId, orgId)
    .first<LeadSignal>()

  return result ?? null
}

/**
 * Count signals with triage_status = 'new' for dashboard badge.
 */
export async function countNewSignals(db: D1Database, orgId: string): Promise<number> {
  const result = await db
    .prepare(
      "SELECT COUNT(*) as count FROM lead_signals WHERE org_id = ? AND triage_status = 'new'"
    )
    .bind(orgId)
    .first<{ count: number }>()

  return result?.count ?? 0
}

// ---------------------------------------------------------------------------
// Triage actions
// ---------------------------------------------------------------------------

/**
 * Update the triage status of a signal (dismiss, mark reviewed, etc.).
 */
export async function updateTriageStatus(
  db: D1Database,
  orgId: string,
  signalId: string,
  status: TriageStatus,
  notes?: string | null
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE lead_signals
       SET triage_status = ?, triage_notes = ?, triaged_at = datetime('now')
       WHERE id = ? AND org_id = ?`
    )
    .bind(status, notes ?? null, signalId, orgId)
    .run()

  return (result.meta.changed_db ?? false) as boolean
}

/**
 * Link a signal to an existing client and mark as promoted.
 */
export async function linkToClient(
  db: D1Database,
  orgId: string,
  signalId: string,
  clientId: string
): Promise<boolean> {
  const result = await db
    .prepare(
      `UPDATE lead_signals
       SET client_id = ?, triage_status = 'promoted', triaged_at = datetime('now')
       WHERE id = ? AND org_id = ?`
    )
    .bind(clientId, signalId, orgId)
    .run()

  return (result.meta.changed_db ?? false) as boolean
}
