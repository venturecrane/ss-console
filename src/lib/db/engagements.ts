/**
 * Engagement data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

import { scheduleEngagementCadence } from '../follow-ups/scheduler'
import { transitionStage } from './entities'

export interface Engagement {
  id: string
  org_id: string
  entity_id: string
  quote_id: string
  scope_summary: string | null
  start_date: string | null
  estimated_end: string | null
  actual_end: string | null
  handoff_date: string | null
  safety_net_end: string | null
  status: string
  estimated_hours: number | null
  actual_hours: number
  created_at: string
  updated_at: string
}

export type EngagementStatus =
  | 'scheduled'
  | 'active'
  | 'handoff'
  | 'safety_net'
  | 'completed'
  | 'cancelled'

export const ENGAGEMENT_STATUSES: { value: EngagementStatus; label: string }[] = [
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'active', label: 'Active' },
  { value: 'handoff', label: 'Handoff' },
  { value: 'safety_net', label: 'Safety Net' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * scheduled   -> active | cancelled
 * active      -> handoff | cancelled
 * handoff     -> safety_net | cancelled
 * safety_net  -> completed | cancelled
 * completed   -> (terminal)
 * cancelled   -> (terminal)
 */
export const VALID_TRANSITIONS: Record<EngagementStatus, EngagementStatus[]> = {
  scheduled: ['active', 'cancelled'],
  active: ['handoff', 'cancelled'],
  handoff: ['safety_net', 'cancelled'],
  safety_net: ['completed', 'cancelled'],
  completed: [],
  cancelled: [],
}

export interface CreateEngagementData {
  entity_id: string
  quote_id: string
  scope_summary?: string | null
  start_date?: string | null
  estimated_end?: string | null
  estimated_hours?: number | null
}

export interface UpdateEngagementData {
  scope_summary?: string | null
  start_date?: string | null
  estimated_end?: string | null
  actual_end?: string | null
  handoff_date?: string | null
  safety_net_end?: string | null
  estimated_hours?: number | null
  actual_hours?: number | null
}

/**
 * List engagements for an organization, optionally filtered by entity.
 */
export async function listEngagements(
  db: D1Database,
  orgId: string,
  entityId?: string
): Promise<Engagement[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (entityId) {
    conditions.push('entity_id = ?')
    params.push(entityId)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM engagements WHERE ${where} ORDER BY created_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Engagement>()
  return result.results
}

/**
 * Get a single engagement by ID, scoped to an organization.
 */
export async function getEngagement(
  db: D1Database,
  orgId: string,
  engagementId: string
): Promise<Engagement | null> {
  const result = await db
    .prepare('SELECT * FROM engagements WHERE id = ? AND org_id = ?')
    .bind(engagementId, orgId)
    .first<Engagement>()

  return result ?? null
}

/**
 * Create a new engagement. Returns the created engagement record.
 */
export async function createEngagement(
  db: D1Database,
  orgId: string,
  data: CreateEngagementData
): Promise<Engagement> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO engagements (id, org_id, entity_id, quote_id, scope_summary, start_date, estimated_end, status, estimated_hours, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entity_id,
      data.quote_id,
      data.scope_summary ?? null,
      data.start_date ?? null,
      data.estimated_end ?? null,
      data.estimated_hours ?? null,
      now,
      now
    )
    .run()

  const engagement = await getEngagement(db, orgId, id)
  if (!engagement) {
    throw new Error('Failed to retrieve created engagement')
  }
  return engagement
}

/**
 * Update an existing engagement. Returns the updated engagement record.
 */
export async function updateEngagement(
  db: D1Database,
  orgId: string,
  engagementId: string,
  data: UpdateEngagementData
): Promise<Engagement | null> {
  const existing = await getEngagement(db, orgId, engagementId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.scope_summary !== undefined) {
    fields.push('scope_summary = ?')
    params.push(data.scope_summary)
  }

  if (data.start_date !== undefined) {
    fields.push('start_date = ?')
    params.push(data.start_date)
  }

  if (data.estimated_end !== undefined) {
    fields.push('estimated_end = ?')
    params.push(data.estimated_end)
  }

  if (data.actual_end !== undefined) {
    fields.push('actual_end = ?')
    params.push(data.actual_end)
  }

  if (data.handoff_date !== undefined) {
    fields.push('handoff_date = ?')
    params.push(data.handoff_date)
  }

  if (data.safety_net_end !== undefined) {
    fields.push('safety_net_end = ?')
    params.push(data.safety_net_end)
  }

  if (data.estimated_hours !== undefined) {
    fields.push('estimated_hours = ?')
    params.push(data.estimated_hours)
  }

  if (data.actual_hours !== undefined) {
    fields.push('actual_hours = ?')
    params.push(data.actual_hours)
  }

  if (fields.length === 0) {
    return existing
  }

  fields.push("updated_at = datetime('now')")

  const sql = `UPDATE engagements SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(engagementId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getEngagement(db, orgId, engagementId)
}

/**
 * Transition engagement status with validation.
 * Returns the updated record or null if the engagement was not found.
 * Throws if the transition is invalid.
 *
 * When transitioning to handoff, auto-sets handoff_date to now and
 * safety_net_end to handoff_date + 14 days.
 */
export async function updateEngagementStatus(
  db: D1Database,
  orgId: string,
  engagementId: string,
  newStatus: EngagementStatus
): Promise<Engagement | null> {
  const existing = await getEngagement(db, orgId, engagementId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status as EngagementStatus
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  const updates: string[] = ['status = ?', "updated_at = datetime('now')"]
  const params: (string | number | null)[] = [newStatus]

  // When transitioning to handoff, auto-set handoff_date and safety_net_end,
  // schedule the engagement follow-up cadence, and transition entity to delivered.
  if (newStatus === 'handoff') {
    const handoffDate = new Date()
    const safetyNetEnd = new Date(handoffDate)
    safetyNetEnd.setDate(safetyNetEnd.getDate() + 14)

    updates.push('handoff_date = ?')
    params.push(handoffDate.toISOString())
    updates.push('safety_net_end = ?')
    params.push(safetyNetEnd.toISOString())

    // Schedule handoff follow-up cadence (referral_ask, review_request, safety_net_checkin, feedback_30day)
    await scheduleEngagementCadence(
      db,
      orgId,
      engagementId,
      existing.entity_id,
      handoffDate.toISOString()
    )

    // Transition entity stage to delivered
    await transitionStage(db, orgId, existing.entity_id, 'delivered', 'Engagement entered handoff')
  }

  // When transitioning to completed, auto-set actual_end
  if (newStatus === 'completed' && !existing.actual_end) {
    updates.push('actual_end = ?')
    params.push(new Date().toISOString())
  }

  const sql = `UPDATE engagements SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(engagementId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getEngagement(db, orgId, engagementId)
}
