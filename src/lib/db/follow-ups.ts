/**
 * Follow-up data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 *
 * Follow-ups track scheduled outreach at key moments:
 * - Proposal cadence: 3-touch over 7 days (Decision #19)
 * - Review request: 2 days post-handoff (Decision #26)
 * - Referral ask: at handoff (Decision #23)
 * - Safety net check-in: 7 days post-handoff
 * - Feedback survey: 30 days post-handoff (Decision #29)
 */

export interface FollowUp {
  id: string
  org_id: string
  client_id: string
  engagement_id: string | null
  quote_id: string | null
  type: string
  scheduled_for: string
  completed_at: string | null
  status: string
  notes: string | null
  created_at: string
}

export type FollowUpType =
  | 'proposal_day2'
  | 'proposal_day5'
  | 'proposal_day7'
  | 'review_request'
  | 'referral_ask'
  | 'safety_net_checkin'
  | 'feedback_30day'

export type FollowUpStatus = 'scheduled' | 'completed' | 'skipped'

export const FOLLOW_UP_TYPES: { value: FollowUpType; label: string }[] = [
  { value: 'proposal_day2', label: 'Proposal - Day 2' },
  { value: 'proposal_day5', label: 'Proposal - Day 5' },
  { value: 'proposal_day7', label: 'Proposal - Day 7' },
  { value: 'review_request', label: 'Review Request' },
  { value: 'referral_ask', label: 'Referral Ask' },
  { value: 'safety_net_checkin', label: 'Safety Net Check-in' },
  { value: 'feedback_30day', label: '30-Day Feedback' },
]

export interface FollowUpFilters {
  status?: FollowUpStatus
  type?: FollowUpType
  upcoming?: boolean
  overdue?: boolean
}

export interface CreateFollowUpData {
  client_id: string
  engagement_id?: string | null
  quote_id?: string | null
  type: FollowUpType
  scheduled_for: string
  notes?: string | null
}

/**
 * List follow-ups for an organization with optional filters.
 */
export async function listFollowUps(
  db: D1Database,
  orgId: string,
  filters?: FollowUpFilters
): Promise<FollowUp[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.status) {
    conditions.push('status = ?')
    params.push(filters.status)
  }

  if (filters?.type) {
    conditions.push('type = ?')
    params.push(filters.type)
  }

  if (filters?.upcoming) {
    conditions.push("scheduled_for >= datetime('now')")
    conditions.push("status = 'scheduled'")
  }

  if (filters?.overdue) {
    conditions.push("scheduled_for < datetime('now')")
    conditions.push("status = 'scheduled'")
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM follow_ups WHERE ${where} ORDER BY scheduled_for ASC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<FollowUp>()
  return result.results
}

/**
 * Get a single follow-up by ID, scoped to an organization.
 */
export async function getFollowUp(
  db: D1Database,
  orgId: string,
  id: string
): Promise<FollowUp | null> {
  const result = await db
    .prepare('SELECT * FROM follow_ups WHERE id = ? AND org_id = ?')
    .bind(id, orgId)
    .first<FollowUp>()

  return result ?? null
}

/**
 * Create a new follow-up. Returns the created record.
 */
export async function createFollowUp(
  db: D1Database,
  orgId: string,
  data: CreateFollowUpData
): Promise<FollowUp> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO follow_ups (id, org_id, client_id, engagement_id, quote_id, type, scheduled_for, status, notes)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)`
    )
    .bind(
      id,
      orgId,
      data.client_id,
      data.engagement_id ?? null,
      data.quote_id ?? null,
      data.type,
      data.scheduled_for,
      data.notes ?? null
    )
    .run()

  const followUp = await getFollowUp(db, orgId, id)
  if (!followUp) {
    throw new Error('Failed to retrieve created follow-up')
  }
  return followUp
}

/**
 * Mark a follow-up as completed with optional notes.
 */
export async function completeFollowUp(
  db: D1Database,
  orgId: string,
  id: string,
  notes?: string | null
): Promise<FollowUp | null> {
  const existing = await getFollowUp(db, orgId, id)
  if (!existing) {
    return null
  }

  const params: (string | null)[] = [new Date().toISOString()]
  let sql = `UPDATE follow_ups SET status = 'completed', completed_at = ?`

  if (notes !== undefined) {
    sql += ', notes = ?'
    params.push(notes ?? null)
  }

  sql += ' WHERE id = ? AND org_id = ?'
  params.push(id, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getFollowUp(db, orgId, id)
}

/**
 * Mark a follow-up as skipped with optional notes.
 */
export async function skipFollowUp(
  db: D1Database,
  orgId: string,
  id: string,
  notes?: string | null
): Promise<FollowUp | null> {
  const existing = await getFollowUp(db, orgId, id)
  if (!existing) {
    return null
  }

  const params: (string | null)[] = []
  let sql = `UPDATE follow_ups SET status = 'skipped'`

  if (notes !== undefined) {
    sql += ', notes = ?'
    params.push(notes ?? null)
  }

  sql += ' WHERE id = ? AND org_id = ?'
  params.push(id, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getFollowUp(db, orgId, id)
}

/**
 * Bulk create follow-ups for scheduling a cadence.
 * Returns the array of created follow-ups.
 */
export async function bulkCreateFollowUps(
  db: D1Database,
  orgId: string,
  followUps: CreateFollowUpData[]
): Promise<FollowUp[]> {
  const created: FollowUp[] = []

  for (const data of followUps) {
    const followUp = await createFollowUp(db, orgId, data)
    created.push(followUp)
  }

  return created
}
