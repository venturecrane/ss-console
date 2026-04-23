/**
 * Entity data access layer.
 *
 * An entity is a single business tracked across its full lifecycle —
 * from pipeline signal through engagement delivery and repeat business.
 * Replaces the separate clients and lead_signals tables.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID().
 * Dedup enforced via UNIQUE(org_id, slug).
 */

import { computeSlug } from '../entities/slug.js'
import { recomputeDeterministicCache } from '../entities/recompute.js'
import { appendContext } from './context.js'
import { isLostReasonCode, type LostReasonCode } from './lost-reasons.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Entity {
  id: string
  org_id: string
  name: string
  slug: string
  phone: string | null
  website: string | null
  stage: EntityStage
  stage_changed_at: string
  pain_score: number | null
  vertical: string | null
  area: string | null
  employee_count: number | null
  tier: EntityTier | null
  summary: string | null
  next_action: string | null
  next_action_at: string | null
  source_pipeline: string | null
  created_at: string
  updated_at: string
}

export type EntityStage =
  | 'signal'
  | 'prospect'
  | 'meetings'
  | 'proposing'
  | 'engaged'
  | 'delivered'
  | 'ongoing'
  | 'lost'

export type EntityTier = 'hot' | 'warm' | 'cool' | 'cold'

export type EntityVertical =
  | 'home_services'
  | 'professional_services'
  | 'contractor_trades'
  | 'retail_salon'
  | 'restaurant_food'
  | 'other'

export const ENTITY_STAGES: { value: EntityStage; label: string }[] = [
  { value: 'signal', label: 'Signal' },
  { value: 'prospect', label: 'Prospect' },
  { value: 'meetings', label: 'Meetings' },
  { value: 'proposing', label: 'Proposing' },
  { value: 'engaged', label: 'Engaged' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'ongoing', label: 'Ongoing' },
  { value: 'lost', label: 'Lost' },
]

export const ENTITY_TIERS: { value: EntityTier; label: string }[] = [
  { value: 'hot', label: 'Hot' },
  { value: 'warm', label: 'Warm' },
  { value: 'cool', label: 'Cool' },
  { value: 'cold', label: 'Cold' },
]

export const ENTITY_VERTICALS: { value: EntityVertical; label: string }[] = [
  { value: 'home_services', label: 'Home Services' },
  { value: 'professional_services', label: 'Professional Services' },
  { value: 'contractor_trades', label: 'Contractor / Trades' },
  { value: 'retail_salon', label: 'Retail / Salon / Spa' },
  { value: 'restaurant_food', label: 'Restaurant / Food Service' },
  { value: 'other', label: 'Other' },
]

/**
 * Valid stage transitions. Key = current stage, value = allowed next stages.
 * `lost` is non-terminal: can re-engage back to `prospect`.
 */
const VALID_TRANSITIONS: Record<EntityStage, EntityStage[]> = {
  signal: ['prospect', 'lost'],
  prospect: ['meetings', 'lost'],
  // From `meetings` the admin picks the next step explicitly (#470). Direct
  // transitions to `engaged`/`delivered`/`ongoing` still require going
  // through `proposing` first — the `proposing→engaged` accepted-quote
  // invariant protects the engagement model. Backing out to `prospect` is
  // allowed so a discovery/follow-up meeting that didn't qualify doesn't
  // force an entity into `lost`.
  meetings: ['proposing', 'prospect', 'lost'],
  proposing: ['engaged', 'lost'],
  engaged: ['delivered', 'lost'],
  delivered: ['ongoing', 'prospect', 'lost'],
  ongoing: ['prospect', 'lost'],
  lost: ['prospect'],
}

export interface EntityFilters {
  stage?: EntityStage
  stages?: EntityStage[]
  vertical?: string
  tier?: EntityTier
  source_pipeline?: string
}

export interface CreateEntityData {
  name: string
  area?: string | null
  phone?: string | null
  website?: string | null
  stage?: EntityStage
  source_pipeline?: string | null
}

export interface UpdateEntityData {
  name?: string
  phone?: string | null
  website?: string | null
  next_action?: string | null
  next_action_at?: string | null
  tier?: EntityTier | null
  summary?: string | null
}

export type FindOrCreateResult =
  | { status: 'created'; entity: Entity }
  | { status: 'found'; entity: Entity }

export interface TransitionStageOptions {
  /** Override reason — bypasses pre-condition checks where documented. */
  force?: string
  /**
   * Structured metadata for `lost` transitions. Captured on the
   * `stage_change` context entry's JSON metadata so the Lost tab can
   * filter and future reporting can roll up "why we lost" without
   * parsing free text.
   *
   * Required when `newStage === 'lost'`. Enforced at the DAL layer
   * rather than the API so every caller (admin UI, scripts, future
   * background jobs) is held to the same contract.
   */
  lostReason?: {
    code: LostReasonCode
    /** Optional operator note. Trimmed. Empty → stored as null. */
    detail?: string | null
  }
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

export async function listEntities(
  db: D1Database,
  orgId: string,
  filters?: EntityFilters
): Promise<Entity[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.stage) {
    conditions.push('stage = ?')
    params.push(filters.stage)
  }

  if (filters?.stages && filters.stages.length > 0) {
    const placeholders = filters.stages.map(() => '?').join(', ')
    conditions.push(`stage IN (${placeholders})`)
    params.push(...filters.stages)
  }

  if (filters?.vertical) {
    conditions.push('vertical = ?')
    params.push(filters.vertical)
  }

  if (filters?.tier) {
    conditions.push('tier = ?')
    params.push(filters.tier)
  }

  if (filters?.source_pipeline) {
    conditions.push('source_pipeline = ?')
    params.push(filters.source_pipeline)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM entities WHERE ${where}
    ORDER BY
      CASE tier WHEN 'hot' THEN 0 WHEN 'warm' THEN 1 WHEN 'cool' THEN 2 WHEN 'cold' THEN 3 ELSE 4 END,
      pain_score DESC,
      updated_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Entity>()
  return result.results
}

export async function getEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Entity | null> {
  return (
    (await db
      .prepare('SELECT * FROM entities WHERE id = ? AND org_id = ?')
      .bind(entityId, orgId)
      .first<Entity>()) ?? null
  )
}

export async function getEntityBySlug(
  db: D1Database,
  orgId: string,
  slug: string
): Promise<Entity | null> {
  return (
    (await db
      .prepare('SELECT * FROM entities WHERE slug = ? AND org_id = ?')
      .bind(slug, orgId)
      .first<Entity>()) ?? null
  )
}

export async function countEntitiesByStage(
  db: D1Database,
  orgId: string,
  stage: EntityStage
): Promise<number> {
  const result = await db
    .prepare('SELECT COUNT(*) as count FROM entities WHERE org_id = ? AND stage = ?')
    .bind(orgId, stage)
    .first<{ count: number }>()
  return result?.count ?? 0
}

// ---------------------------------------------------------------------------
// Signal metadata (for Signal list evidence density)
// ---------------------------------------------------------------------------

/**
 * Per-entity rollup of pipeline-generated signal metadata + last activity,
 * used to render evidence-dense signal rows without loading full context.
 *
 * Values come from the context table: `top_problems` and `outreach_angle`
 * are read from the metadata JSON of the most recent `signal` / `scorecard`
 * context entry; `last_activity_at` is the `created_at` of the most recent
 * context entry of ANY type.
 *
 * Missing fields stay `null` — callers must render nothing (not placeholders)
 * per CLAUDE.md Pattern B.
 */
export interface EntitySignalMetadata {
  entity_id: string
  top_problems: string[] | null
  outreach_angle: string | null
  last_activity_at: string | null
}

/**
 * Fetch latest signal metadata and last-activity timestamp for a batch of
 * entities in two parameterized queries (no N+1).
 *
 * Returns a Map keyed by entity_id. Entities with no context entries at all
 * are omitted from the map — caller should treat missing as "no metadata".
 */
export async function getSignalMetadataForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, EntitySignalMetadata>> {
  const out = new Map<string, EntitySignalMetadata>()
  if (entityIds.length === 0) return out

  const placeholders = entityIds.map(() => '?').join(', ')

  // Latest signal/scorecard metadata per entity.
  // Picks the most recent row via the correlated subquery on created_at.
  const signalSql = `
    SELECT c.entity_id, c.metadata
    FROM context c
    WHERE c.org_id = ?
      AND c.entity_id IN (${placeholders})
      AND c.type IN ('signal', 'scorecard')
      AND c.created_at = (
        SELECT MAX(c2.created_at)
        FROM context c2
        WHERE c2.entity_id = c.entity_id
          AND c2.type IN ('signal', 'scorecard')
      )
  `
  const signalRows = await db
    .prepare(signalSql)
    .bind(orgId, ...entityIds)
    .all<{ entity_id: string; metadata: string | null }>()

  for (const row of signalRows.results) {
    let topProblems: string[] | null = null
    let outreachAngle: string | null = null
    if (row.metadata) {
      try {
        const meta = JSON.parse(row.metadata) as Record<string, unknown>
        if (
          Array.isArray(meta.top_problems) &&
          meta.top_problems.every((p) => typeof p === 'string')
        ) {
          topProblems = (meta.top_problems as string[]).length
            ? (meta.top_problems as string[])
            : null
        }
        if (typeof meta.outreach_angle === 'string' && meta.outreach_angle.trim()) {
          outreachAngle = meta.outreach_angle.trim()
        }
      } catch {
        // Malformed JSON — treat as missing metadata.
      }
    }
    out.set(row.entity_id, {
      entity_id: row.entity_id,
      top_problems: topProblems,
      outreach_angle: outreachAngle,
      last_activity_at: null,
    })
  }

  // Last-activity across all context types.
  const activitySql = `
    SELECT entity_id, MAX(created_at) AS last_activity_at
    FROM context
    WHERE org_id = ?
      AND entity_id IN (${placeholders})
    GROUP BY entity_id
  `
  const activityRows = await db
    .prepare(activitySql)
    .bind(orgId, ...entityIds)
    .all<{ entity_id: string; last_activity_at: string | null }>()

  for (const row of activityRows.results) {
    const existing = out.get(row.entity_id)
    if (existing) {
      existing.last_activity_at = row.last_activity_at
    } else {
      out.set(row.entity_id, {
        entity_id: row.entity_id,
        top_problems: null,
        outreach_angle: null,
        last_activity_at: row.last_activity_at,
      })
    }
  }

  return out
}

// ---------------------------------------------------------------------------
// Find or Create (for pipeline ingestion)
// ---------------------------------------------------------------------------

/**
 * Find an existing entity by slug, or create a new one.
 * Used by the pipeline ingestion endpoint to ensure one entity per business.
 */
export async function findOrCreateEntity(
  db: D1Database,
  orgId: string,
  data: CreateEntityData
): Promise<FindOrCreateResult> {
  const slug = computeSlug(data.name, data.area)

  // Try to find existing
  const existing = await getEntityBySlug(db, orgId, slug)
  if (existing) {
    // Update phone/website if we have new info and existing is null
    if ((data.phone && !existing.phone) || (data.website && !existing.website)) {
      await db
        .prepare(
          `UPDATE entities SET
            phone = COALESCE(?, phone),
            website = COALESCE(?, website),
            updated_at = datetime('now')
          WHERE id = ? AND org_id = ?`
        )
        .bind(data.phone ?? null, data.website ?? null, existing.id, orgId)
        .run()
    }
    const entity = (await getEntity(db, orgId, existing.id))!
    return { status: 'found', entity }
  }

  // Create new
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO entities (
        id, org_id, name, slug, phone, website, stage, stage_changed_at,
        source_pipeline, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(org_id, slug) DO NOTHING`
    )
    .bind(
      id,
      orgId,
      data.name,
      slug,
      data.phone ?? null,
      data.website ?? null,
      data.stage ?? 'signal',
      now,
      data.source_pipeline ?? null,
      now,
      now
    )
    .run()

  // Handle race condition: another request may have created it
  const entity = (await getEntityBySlug(db, orgId, slug))!
  const wasCreated = entity.id === id
  return wasCreated ? { status: 'created', entity } : { status: 'found', entity }
}

// ---------------------------------------------------------------------------
// Create (for migration and manual entry)
// ---------------------------------------------------------------------------

export async function createEntity(
  db: D1Database,
  orgId: string,
  data: CreateEntityData & { id?: string; slug?: string }
): Promise<Entity> {
  const id = data.id ?? crypto.randomUUID()
  const slug = data.slug ?? computeSlug(data.name, data.area)
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO entities (
        id, org_id, name, slug, phone, website, stage, stage_changed_at,
        source_pipeline, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.name,
      slug,
      data.phone ?? null,
      data.website ?? null,
      data.stage ?? 'signal',
      now,
      data.source_pipeline ?? null,
      now,
      now
    )
    .run()

  const entity = await getEntity(db, orgId, id)
  if (!entity) throw new Error('Failed to retrieve created entity')
  return entity
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

export async function updateEntity(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: UpdateEntityData
): Promise<Entity | null> {
  const existing = await getEntity(db, orgId, entityId)
  if (!existing) return null

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.name !== undefined) {
    fields.push('name = ?')
    params.push(data.name)
  }
  if (data.phone !== undefined) {
    fields.push('phone = ?')
    params.push(data.phone)
  }
  if (data.website !== undefined) {
    fields.push('website = ?')
    params.push(data.website)
  }
  if (data.next_action !== undefined) {
    fields.push('next_action = ?')
    params.push(data.next_action)
  }
  if (data.next_action_at !== undefined) {
    fields.push('next_action_at = ?')
    params.push(data.next_action_at)
  }
  if (data.tier !== undefined) {
    fields.push('tier = ?')
    params.push(data.tier)
  }
  if (data.summary !== undefined) {
    fields.push('summary = ?')
    params.push(data.summary)
  }

  if (fields.length === 0) return existing

  fields.push("updated_at = datetime('now')")
  const sql = `UPDATE entities SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(entityId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()
  return getEntity(db, orgId, entityId)
}

// ---------------------------------------------------------------------------
// Stage transitions
// ---------------------------------------------------------------------------

/**
 * Transition an entity to a new stage. Validates against allowed transitions
 * and enforces lifecycle invariants (pre-conditions) before updating.
 *
 * Pre-conditions:
 * - proposing → engaged: requires at least one accepted quote
 * - delivered → ongoing: requires paid completion invoice OR force override
 *
 * Note: signal → meetings is blocked by VALID_TRANSITIONS. Booking flows
 * must walk through `prospect` as an intermediate state (signal → prospect → meetings).
 *
 * Records a stage_change context entry automatically.
 */
export async function transitionStage(
  db: D1Database,
  orgId: string,
  entityId: string,
  newStage: EntityStage,
  reason: string,
  options?: TransitionStageOptions
): Promise<Entity | null> {
  const entity = await getEntity(db, orgId, entityId)
  if (!entity) return null

  const allowed = VALID_TRANSITIONS[entity.stage as EntityStage]
  if (!allowed?.includes(newStage)) {
    throw new Error(
      `Invalid stage transition: ${entity.stage} → ${newStage}. Allowed: ${allowed?.join(', ')}`
    )
  }

  // Lost reason is required when transitioning to lost. Captured as
  // structured metadata on the stage_change context entry so the Lost
  // tab can filter and "why we lost" rollups are queryable.
  let lostReasonCode: LostReasonCode | null = null
  let lostReasonDetail: string | null = null
  if (newStage === 'lost') {
    if (!options?.lostReason?.code) {
      throw new Error(
        'Lost reason is required: provide options.lostReason.code when transitioning to lost.'
      )
    }
    if (!isLostReasonCode(options.lostReason.code)) {
      throw new Error(
        `Invalid lost reason code: ${options.lostReason.code}. See src/lib/db/lost-reasons.ts.`
      )
    }
    lostReasonCode = options.lostReason.code
    const rawDetail = options.lostReason.detail
    lostReasonDetail =
      typeof rawDetail === 'string' && rawDetail.trim().length > 0 ? rawDetail.trim() : null
  }

  // ---------------------------------------------------------------------------
  // Lifecycle invariant pre-conditions
  // ---------------------------------------------------------------------------

  // proposing → engaged: must have at least one accepted quote
  if (entity.stage === 'proposing' && newStage === 'engaged') {
    const acceptedQuote = await db
      .prepare(
        `SELECT 1 FROM quotes WHERE entity_id = ? AND org_id = ? AND status = 'accepted' LIMIT 1`
      )
      .bind(entityId, orgId)
      .first()
    if (!acceptedQuote) {
      throw new Error(
        'Cannot transition to engaged: no accepted quote found. ' +
          'A quote must be signed and accepted before an engagement can begin.'
      )
    }
  }

  // delivered → ongoing: must have paid completion invoice OR force override
  if (entity.stage === 'delivered' && newStage === 'ongoing') {
    if (options?.force) {
      // Log the override reason to context
      await appendContext(db, orgId, {
        entity_id: entityId,
        type: 'stage_change',
        content: `Force override: delivered → ongoing. Reason: ${options.force}`,
        source: 'system',
        metadata: { override: true, reason: options.force },
      })
    } else {
      const paidCompletion = await db
        .prepare(
          `SELECT 1 FROM invoices WHERE entity_id = ? AND org_id = ? AND type = 'completion' AND status = 'paid' LIMIT 1`
        )
        .bind(entityId, orgId)
        .first()
      if (!paidCompletion) {
        throw new Error(
          'Cannot transition to ongoing: completion invoice has not been paid. ' +
            'Either collect payment or provide a force override reason.'
        )
      }
    }
  }

  const now = new Date().toISOString()

  await db
    .prepare(
      `UPDATE entities SET
        stage = ?, stage_changed_at = ?, updated_at = ?
      WHERE id = ? AND org_id = ?`
    )
    .bind(newStage, now, now, entityId, orgId)
    .run()

  // Record stage change as context entry
  const contextId = crypto.randomUUID()
  const content = `Stage: ${entity.stage} → ${newStage}. ${reason}`
  const metadata: Record<string, unknown> = {
    from: entity.stage,
    to: newStage,
    reason,
  }
  if (lostReasonCode) {
    metadata.lost_reason = lostReasonCode
    if (lostReasonDetail) metadata.lost_detail = lostReasonDetail
  }
  await db
    .prepare(
      `INSERT INTO context (id, entity_id, org_id, type, content, source, content_size, metadata, created_at)
      VALUES (?, ?, ?, 'stage_change', ?, 'system', ?, ?, ?)`
    )
    .bind(contextId, entityId, orgId, content, content.length, JSON.stringify(metadata), now)
    .run()

  // Recompute cache after stage change
  await recomputeDeterministicCache(db, orgId, entityId)

  return getEntity(db, orgId, entityId)
}

/**
 * Returns the latest captured Lost reason code per entity, keyed by
 * entity_id. Reads from `stage_change` context entries where
 * `metadata.to = 'lost'`. Entities with no structured reason (e.g.
 * legacy Lost rows captured before #477) are absent from the map.
 *
 * This is the one place that rolls up structured Lost metadata for
 * list-rendering. Keep the query tight — it runs on every Lost-tab
 * page render.
 */
export async function getLatestLostReasonsByEntity(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, { code: LostReasonCode; detail: string | null }>> {
  const result = new Map<string, { code: LostReasonCode; detail: string | null }>()
  if (entityIds.length === 0) return result

  const placeholders = entityIds.map(() => '?').join(', ')
  // For each entity, pick the newest stage_change row whose metadata
  // marks a transition INTO lost. D1/SQLite supports json_extract.
  const rows = await db
    .prepare(
      `SELECT c.entity_id,
              json_extract(c.metadata, '$.lost_reason') AS lost_reason,
              json_extract(c.metadata, '$.lost_detail') AS lost_detail
         FROM context c
        WHERE c.org_id = ?
          AND c.type = 'stage_change'
          AND c.entity_id IN (${placeholders})
          AND json_extract(c.metadata, '$.to') = 'lost'
          AND c.created_at = (
            SELECT MAX(c2.created_at) FROM context c2
             WHERE c2.org_id = c.org_id
               AND c2.entity_id = c.entity_id
               AND c2.type = 'stage_change'
               AND json_extract(c2.metadata, '$.to') = 'lost'
          )`
    )
    .bind(orgId, ...entityIds)
    .all<{ entity_id: string; lost_reason: string | null; lost_detail: string | null }>()

  for (const row of rows.results) {
    if (row.lost_reason && isLostReasonCode(row.lost_reason)) {
      result.set(row.entity_id, {
        code: row.lost_reason,
        detail: row.lost_detail ?? null,
      })
    }
  }
  return result
}

// ---------------------------------------------------------------------------
// Merge
// ---------------------------------------------------------------------------

/**
 * Merge two entities. All context from `sourceId` moves to `targetId`.
 * The source entity is deleted after merge.
 */
export async function mergeEntities(
  db: D1Database,
  orgId: string,
  targetId: string,
  sourceId: string
): Promise<Entity | null> {
  const target = await getEntity(db, orgId, targetId)
  const source = await getEntity(db, orgId, sourceId)
  if (!target || !source) return null

  // Move all context entries from source to target
  await db
    .prepare('UPDATE context SET entity_id = ? WHERE entity_id = ? AND org_id = ?')
    .bind(targetId, sourceId, orgId)
    .run()

  // Move contacts
  await db
    .prepare('UPDATE contacts SET entity_id = ? WHERE entity_id = ? AND org_id = ?')
    .bind(targetId, sourceId, orgId)
    .run()

  // Record the merge in context
  const contextId = crypto.randomUUID()
  const content = `Merged entity "${source.name}" (${sourceId}) into this entity.`
  await db
    .prepare(
      `INSERT INTO context (id, entity_id, org_id, type, content, source, content_size, metadata, created_at)
      VALUES (?, ?, ?, 'note', ?, 'system', ?, ?, datetime('now'))`
    )
    .bind(
      contextId,
      targetId,
      orgId,
      content,
      content.length,
      JSON.stringify({ merged_from: sourceId, merged_name: source.name, merged_slug: source.slug })
    )
    .run()

  // Delete source entity
  await db.prepare('DELETE FROM entities WHERE id = ? AND org_id = ?').bind(sourceId, orgId).run()

  // Recompute cache for target
  await recomputeDeterministicCache(db, orgId, targetId)

  return getEntity(db, orgId, targetId)
}

// Re-export slug utility for convenience
export { computeSlug } from '../entities/slug.js'
