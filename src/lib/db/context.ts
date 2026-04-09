/**
 * Context data access layer.
 *
 * INVARIANT: APPEND-ONLY
 * The context table is an append-only log of everything we learn about an entity.
 * This module intentionally exports NO update or delete operations. Context entries
 * are immutable once written — corrections are modeled as new entries, not edits.
 *
 * D1 does not support triggers, so append-only enforcement lives at the
 * TypeScript layer: this module is the sole write path to the context table,
 * and it only exposes INSERT operations (appendContext, appendContextRaw).
 * Any future code review that adds UPDATE or DELETE exports to this file
 * should be rejected — it would violate the append-only contract.
 *
 * Signals, enrichment, notes, transcripts, extractions, outreach drafts,
 * engagement logs, follow-up results, feedback, parking lot items — all go here.
 *
 * LLMs read context at retrieval time to generate any artifact.
 *
 * All queries are parameterized to prevent SQL injection.
 */

import { recomputeDeterministicCache } from '../entities/recompute.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ContextEntry {
  id: string
  entity_id: string
  org_id: string
  type: ContextType
  content: string
  source: string
  source_ref: string | null
  content_size: number | null
  metadata: string | null
  engagement_id: string | null
  created_at: string
}

export type ContextType =
  | 'signal'
  | 'enrichment'
  | 'note'
  | 'transcript'
  | 'extraction'
  | 'outreach_draft'
  | 'engagement_log'
  | 'follow_up_result'
  | 'feedback'
  | 'parking_lot'
  | 'stage_change'
  | 'intake'
  | 'scorecard'

export const CONTEXT_TYPES: { value: ContextType; label: string }[] = [
  { value: 'signal', label: 'Pipeline Signal' },
  { value: 'enrichment', label: 'Enrichment' },
  { value: 'note', label: 'Note' },
  { value: 'transcript', label: 'Transcript' },
  { value: 'extraction', label: 'Extraction' },
  { value: 'outreach_draft', label: 'Outreach Draft' },
  { value: 'engagement_log', label: 'Engagement Log' },
  { value: 'follow_up_result', label: 'Follow-up Result' },
  { value: 'feedback', label: 'Feedback' },
  { value: 'parking_lot', label: 'Parking Lot' },
  { value: 'stage_change', label: 'Stage Change' },
  { value: 'intake', label: 'Intake' },
  { value: 'scorecard', label: 'Scorecard' },
]

export interface AppendContextData {
  entity_id: string
  type: ContextType
  content: string
  source: string
  source_ref?: string | null
  metadata?: Record<string, unknown> | null
  engagement_id?: string | null
}

export interface ContextFilters {
  type?: ContextType
  types?: ContextType[]
  engagement_id?: string
}

// ---------------------------------------------------------------------------
// Append
// ---------------------------------------------------------------------------

/**
 * Append a context entry to an entity. Triggers deterministic cache recompute.
 */
export async function appendContext(
  db: D1Database,
  orgId: string,
  data: AppendContextData
): Promise<ContextEntry> {
  const id = crypto.randomUUID()
  const contentSize = new TextEncoder().encode(data.content).length
  const metadataJson = data.metadata ? JSON.stringify(data.metadata) : null
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO context (
        id, entity_id, org_id, type, content, source, source_ref,
        content_size, metadata, engagement_id, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.entity_id,
      orgId,
      data.type,
      data.content,
      data.source,
      data.source_ref ?? null,
      contentSize,
      metadataJson,
      data.engagement_id ?? null,
      now
    )
    .run()

  // Recompute deterministic cache after every context append
  await recomputeDeterministicCache(db, orgId, data.entity_id)

  const entry = await getContextEntry(db, id)
  if (!entry) throw new Error('Failed to retrieve created context entry')
  return entry
}

/**
 * Append a context entry WITHOUT triggering cache recompute.
 * Used during bulk migration to avoid N recomputes.
 */
export async function appendContextRaw(
  db: D1Database,
  orgId: string,
  data: AppendContextData & { id?: string; created_at?: string }
): Promise<string> {
  const id = data.id ?? crypto.randomUUID()
  const contentSize = new TextEncoder().encode(data.content).length
  const metadataJson = data.metadata ? JSON.stringify(data.metadata) : null

  await db
    .prepare(
      `INSERT INTO context (
        id, entity_id, org_id, type, content, source, source_ref,
        content_size, metadata, engagement_id, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.entity_id,
      orgId,
      data.type,
      data.content,
      data.source,
      data.source_ref ?? null,
      contentSize,
      metadataJson,
      data.engagement_id ?? null,
      data.created_at ?? new Date().toISOString()
    )
    .run()

  return id
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

export async function getContextEntry(
  db: D1Database,
  contextId: string
): Promise<ContextEntry | null> {
  return (
    (await db
      .prepare('SELECT * FROM context WHERE id = ?')
      .bind(contextId)
      .first<ContextEntry>()) ?? null
  )
}

/**
 * List all context entries for an entity, chronologically.
 */
export async function listContext(
  db: D1Database,
  entityId: string,
  filters?: ContextFilters
): Promise<ContextEntry[]> {
  const conditions: string[] = ['entity_id = ?']
  const params: (string | number)[] = [entityId]

  if (filters?.type) {
    conditions.push('type = ?')
    params.push(filters.type)
  }

  if (filters?.types && filters.types.length > 0) {
    const placeholders = filters.types.map(() => '?').join(', ')
    conditions.push(`type IN (${placeholders})`)
    params.push(...filters.types)
  }

  if (filters?.engagement_id) {
    conditions.push('engagement_id = ?')
    params.push(filters.engagement_id)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM context WHERE ${where} ORDER BY created_at ASC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<ContextEntry>()
  return result.results
}

/**
 * Count context entries for an entity.
 */
export async function countContext(db: D1Database, entityId: string): Promise<number> {
  const result = await db
    .prepare('SELECT COUNT(*) as count FROM context WHERE entity_id = ?')
    .bind(entityId)
    .first<{ count: number }>()
  return result?.count ?? 0
}

/**
 * Get total content size for an entity's context (for budget estimation).
 */
export async function getContextSize(db: D1Database, entityId: string): Promise<number> {
  const result = await db
    .prepare('SELECT SUM(content_size) as total FROM context WHERE entity_id = ?')
    .bind(entityId)
    .first<{ total: number | null }>()
  return result?.total ?? 0
}

// ---------------------------------------------------------------------------
// Assembly for LLM operations
// ---------------------------------------------------------------------------

interface AssembleOptions {
  /** Maximum approximate size in bytes. Default 32KB (~8000 tokens). */
  maxBytes?: number
  /** Include full transcripts from R2? Default false (uses summaries). */
  includeTranscripts?: boolean
  /** Filter to specific context types. */
  typeFilter?: ContextType[]
}

/**
 * Assemble entity context into a formatted markdown string for LLM consumption.
 *
 * Respects size budgets to avoid blowing up token counts.
 * Transcripts are included as summaries unless `includeTranscripts` is true.
 */
export async function assembleEntityContext(
  db: D1Database,
  entityId: string,
  opts?: AssembleOptions
): Promise<string> {
  const maxBytes = opts?.maxBytes ?? 32_000
  const filters: ContextFilters = {}
  if (opts?.typeFilter) {
    filters.types = opts.typeFilter
  }

  const entries = await listContext(db, entityId, filters)
  if (entries.length === 0) return ''

  const parts: string[] = []
  let currentSize = 0

  for (const entry of entries) {
    const date = entry.created_at.split('T')[0]
    const header = `### [${entry.type}] ${entry.source} — ${date}`
    const entryText = `${header}\n${entry.content}\n`
    const entrySize = new TextEncoder().encode(entryText).length

    if (currentSize + entrySize > maxBytes) {
      parts.push(
        `\n_... ${entries.length - parts.length} additional entries truncated (size budget)_`
      )
      break
    }

    parts.push(entryText)
    currentSize += entrySize
  }

  return parts.join('\n')
}
