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
  | 'alert'

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

export type ContextAuthority = 'authoritative' | 'non_authoritative'

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

async function getContextEntry(db: D1Database, contextId: string): Promise<ContextEntry | null> {
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
 * For a batch of entity ids, return a Map keyed by entity_id whose value
 * is the latest `outreach_draft` context entry for that entity. Used by
 * the entity list to gate "Log reply" (existence check via Map.has) and
 * to build a "Send outreach" mailto (content from Map.get) without an
 * N+1.
 *
 * Outreach-drafted is the practical proxy for "outreach exists" because
 * we don't yet record send events explicitly (the mailto: button kicks
 * the user into their mail client and we can't observe what happens
 * next). A drafted but never-sent outreach overcounts slightly; that's
 * the conservative direction — we'd rather show a Log reply button on
 * a row that's *almost* ready than hide it on a row where outreach
 * actually happened.
 *
 * Empty input returns an empty Map without touching the DB.
 */
export async function getLatestOutreachDraftForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, ContextEntry>> {
  const result = new Map<string, ContextEntry>()
  if (entityIds.length === 0) return result

  const placeholders = entityIds.map(() => '?').join(', ')
  // Order entity_id, created_at DESC so the first row per entity is the
  // most recent draft; later rows we ignore. Cheaper than a window
  // function on D1 for this volume.
  const rows = await db
    .prepare(
      `SELECT * FROM context
       WHERE org_id = ? AND type = 'outreach_draft'
         AND entity_id IN (${placeholders})
       ORDER BY entity_id ASC, created_at DESC`
    )
    .bind(orgId, ...entityIds)
    .all<ContextEntry>()

  for (const row of rows.results ?? []) {
    if (!result.has(row.entity_id)) {
      result.set(row.entity_id, row)
    }
  }
  return result
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
  /**
   * Include model-authored, non-authoritative summaries. Default false.
   * Downstream prompt assembly should read extractive facts by default.
   */
  includeNonAuthoritative?: boolean
}

const LEGACY_NON_AUTHORITATIVE_SOURCES = new Set([
  'intelligence_brief',
  'review_analysis',
  'review_synthesis',
])

function parseMetadataJson(metadata: string | null): Record<string, unknown> | null {
  if (!metadata) return null
  try {
    const parsed = JSON.parse(metadata)
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function getContextAuthority(entry: ContextEntry): ContextAuthority {
  const metadata = parseMetadataJson(entry.metadata)
  const explicit = metadata?.context_authority
  if (explicit === 'authoritative' || explicit === 'non_authoritative') {
    return explicit
  }
  return LEGACY_NON_AUTHORITATIVE_SOURCES.has(entry.source) ? 'non_authoritative' : 'authoritative'
}

/**
 * Assemble entity context into a formatted markdown string for LLM consumption.
 *
 * Respects size budgets to avoid blowing up token counts.
 * Transcripts are included as summaries unless `includeTranscripts` is true.
 *
 * @public Consumed by src/lib/claude/assessment-to-quote.ts, which knip reports unused (no route
 * calls it). Retiring that path is a product decision, not this gate's.
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

  const listed = await listContext(db, entityId, filters)
  const entries = opts?.includeNonAuthoritative
    ? listed
    : listed.filter((entry) => getContextAuthority(entry) === 'authoritative')
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
