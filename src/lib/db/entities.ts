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
  | 'assessing'
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
  { value: 'assessing', label: 'Assessing' },
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
 *
 * NOTE: signal -> assessing is intentionally absent. Booking flows must walk
 * through `prospect` as an intermediate state so that enrichment and triage
 * happen before an assessment is scheduled.
 */
const VALID_TRANSITIONS: Record<EntityStage, EntityStage[]> = {
  signal: ['prospect', 'lost'],
  prospect: ['assessing', 'lost'],
  assessing: ['proposing', 'lost'],
  proposing: ['engaged', 'lost'],
  engaged: ['delivered', 'lost'],
  delivered: ['ongoing', 'prospect', 'lost'],
  ongoing: ['prospect', 'lost'],
  lost: ['prospect'],
}

export interface TransitionOptions {
  /** Override reason — bypasses the paid-invoice check for delivered -> ongoing. */
  force?: string
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
 * and enforces lifecycle invariant pre-conditions.
 * Records a stage_change context entry automatically.
 *
 * Pre-conditions:
 * - proposing -> engaged: requires at least one accepted quote
 * - delivered -> ongoing: requires paid completion invoice OR force override
 */
export async function transitionStage(
  db: D1Database,
  orgId: string,
  entityId: string,
  newStage: EntityStage,
  reason: string,
  opts?: TransitionOptions
): Promise<Entity | null> {
  const entity = await getEntity(db, orgId, entityId)
  if (!entity) return null

  const allowed = VALID_TRANSITIONS[entity.stage as EntityStage]
  if (!allowed?.includes(newStage)) {
    throw new Error(
      `Invalid stage transition: ${entity.stage} → ${newStage}. Allowed: ${allowed?.join(', ')}`
    )
  }

  // ---------------------------------------------------------------------------
  // Invariant: proposing -> engaged requires an accepted quote
  // ---------------------------------------------------------------------------
  if (entity.stage === 'proposing' && newStage === 'engaged') {
    const acceptedQuote = await db
      .prepare(`SELECT 1 FROM quotes WHERE entity_id = ? AND status = 'accepted' LIMIT 1`)
      .bind(entityId)
      .first()
    if (!acceptedQuote) {
      throw new Error('Cannot transition to engaged: no accepted quote found for this entity')
    }
  }

  // ---------------------------------------------------------------------------
  // Invariant: delivered -> ongoing requires paid completion invoice or override
  // ---------------------------------------------------------------------------
  if (entity.stage === 'delivered' && newStage === 'ongoing') {
    if (opts?.force) {
      // Log the override reason in context below (included in metadata)
    } else {
      const paidCompletion = await db
        .prepare(
          `SELECT 1 FROM invoices WHERE entity_id = ? AND type = 'completion' AND status = 'paid' LIMIT 1`
        )
        .bind(entityId)
        .first()
      if (!paidCompletion) {
        throw new Error(
          'Cannot transition to ongoing: no paid completion invoice found. Pass force option with reason to override.'
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
  const metadata: Record<string, unknown> = { from: entity.stage, to: newStage, reason }
  if (opts?.force) {
    metadata.force_override = opts.force
  }
  const content = `Stage: ${entity.stage} → ${newStage}. ${reason}`
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
