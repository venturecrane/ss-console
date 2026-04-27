/**
 * Engagement data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

import { scheduleEngagementCadence } from '../follow-ups/scheduler'
import { transitionStage } from './entities'
import { getDefaultOriginatingSignalId } from './signal-attribution'

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
  consultant_name: string | null
  consultant_photo_url: string | null
  consultant_role: string | null
  consultant_phone: string | null
  next_touchpoint_at: string | null
  next_touchpoint_label: string | null
  /**
   * Context-row id (type='signal') that this engagement is attributed to (#589).
   * NULL when the entity had no signals at engagement creation time, or when
   * the admin explicitly cleared the attribution. Powers the per-pipeline ROI
   * roll-up via `getEngagementsBySourcePipeline` in `signal-attribution.ts`.
   */
  originating_signal_id: string | null
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
  /**
   * Originating signal attribution (#589). Three states:
   * - `undefined` (omitted): caller defers to the default — most-recent signal
   *   for the entity, or NULL if the entity has none.
   * - `string`: caller has an explicit signal id (e.g. admin override). Stored
   *   as-is. The caller is responsible for validating the id belongs to this
   *   entity/org.
   * - `null`: caller explicitly wants the engagement unattributed (e.g.
   *   inbound referral with no signal on file). Default-resolution is skipped.
   */
  originating_signal_id?: string | null
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
  consultant_name?: string | null
  consultant_photo_url?: string | null
  consultant_role?: string | null
  consultant_phone?: string | null
  next_touchpoint_at?: string | null
  next_touchpoint_label?: string | null
  /**
   * Edit attribution post-creation (#589). Pass `null` to clear, a string
   * to set, or omit to leave unchanged. The caller validates the id belongs
   * to this entity/org before calling — see `getSignalById` in
   * `signal-attribution.ts`.
   */
  originating_signal_id?: string | null
}

/**
 * For a batch of entity ids, return a Map keyed by entity_id whose
 * value is the most recent non-terminal engagement (status NOT IN
 * 'completed', 'cancelled'). Used by the Engaged-stage list to render
 * engagement progress (`actual / estimated hours`) on each row without
 * an N+1.
 *
 * If an entity has no active engagement (e.g. it's at engaged stage
 * but the engagement was just cancelled), it's absent from the Map.
 *
 * Empty input returns an empty Map without touching the DB.
 */
export async function getActiveEngagementForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, Engagement>> {
  const result = new Map<string, Engagement>()
  if (entityIds.length === 0) return result

  const entityIdsJson = JSON.stringify(entityIds)
  const rows = await db
    .prepare(
      `SELECT * FROM engagements
       WHERE org_id = ?
         AND status NOT IN ('completed', 'cancelled')
         AND entity_id IN (SELECT value FROM json_each(?))
       ORDER BY entity_id ASC, created_at DESC`
    )
    .bind(orgId, entityIdsJson)
    .all<Engagement>()

  for (const row of rows.results ?? []) {
    if (!result.has(row.entity_id)) {
      result.set(row.entity_id, row)
    }
  }
  return result
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

  // Resolve originating-signal attribution (#589). `undefined` means "use
  // the default" — most-recent signal context-row for the entity, or NULL
  // if the entity has none. Explicit `null` skips defaulting (admin chose
  // to leave the engagement unattributed).
  const originatingSignalId =
    data.originating_signal_id === undefined
      ? await getDefaultOriginatingSignalId(db, orgId, data.entity_id)
      : data.originating_signal_id

  await db
    .prepare(
      `INSERT INTO engagements (id, org_id, entity_id, quote_id, scope_summary, start_date, estimated_end, status, estimated_hours, originating_signal_id, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)`
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
      originatingSignalId,
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

  if (data.consultant_name !== undefined) {
    fields.push('consultant_name = ?')
    params.push(data.consultant_name)
  }

  if (data.consultant_photo_url !== undefined) {
    fields.push('consultant_photo_url = ?')
    params.push(data.consultant_photo_url)
  }

  if (data.consultant_role !== undefined) {
    fields.push('consultant_role = ?')
    params.push(data.consultant_role)
  }

  if (data.consultant_phone !== undefined) {
    fields.push('consultant_phone = ?')
    params.push(data.consultant_phone)
  }

  if (data.next_touchpoint_at !== undefined) {
    fields.push('next_touchpoint_at = ?')
    params.push(data.next_touchpoint_at)
  }

  if (data.next_touchpoint_label !== undefined) {
    fields.push('next_touchpoint_label = ?')
    params.push(data.next_touchpoint_label)
  }

  if (data.originating_signal_id !== undefined) {
    fields.push('originating_signal_id = ?')
    params.push(data.originating_signal_id)
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
