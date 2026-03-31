/**
 * Milestone data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

export interface Milestone {
  id: string
  engagement_id: string
  name: string
  description: string | null
  due_date: string | null
  completed_at: string | null
  status: string
  payment_trigger: number
  sort_order: number
  created_at: string
}

export type MilestoneStatus = 'pending' | 'in_progress' | 'completed' | 'skipped'

export const MILESTONE_STATUSES: { value: MilestoneStatus; label: string }[] = [
  { value: 'pending', label: 'Pending' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'skipped', label: 'Skipped' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * pending     -> in_progress | skipped
 * in_progress -> completed | skipped
 * completed   -> (terminal)
 * skipped     -> (terminal)
 */
export const VALID_TRANSITIONS: Record<MilestoneStatus, MilestoneStatus[]> = {
  pending: ['in_progress', 'skipped'],
  in_progress: ['completed', 'skipped'],
  completed: [],
  skipped: [],
}

export interface CreateMilestoneData {
  name: string
  description?: string | null
  due_date?: string | null
  payment_trigger?: boolean
  sort_order?: number
}

export interface UpdateMilestoneData {
  name?: string
  description?: string | null
  due_date?: string | null
  payment_trigger?: boolean
  sort_order?: number
}

/**
 * List milestones for an engagement, ordered by sort_order ascending.
 */
export async function listMilestones(db: D1Database, engagementId: string): Promise<Milestone[]> {
  const result = await db
    .prepare('SELECT * FROM milestones WHERE engagement_id = ? ORDER BY sort_order ASC')
    .bind(engagementId)
    .all<Milestone>()
  return result.results
}

/**
 * Get a single milestone by ID.
 */
export async function getMilestone(db: D1Database, milestoneId: string): Promise<Milestone | null> {
  const result = await db
    .prepare('SELECT * FROM milestones WHERE id = ?')
    .bind(milestoneId)
    .first<Milestone>()

  return result ?? null
}

/**
 * Create a new milestone linked to an engagement. Returns the created record.
 */
export async function createMilestone(
  db: D1Database,
  engagementId: string,
  data: CreateMilestoneData
): Promise<Milestone> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO milestones (id, engagement_id, name, description, due_date, status, payment_trigger, sort_order)
     VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)`
    )
    .bind(
      id,
      engagementId,
      data.name,
      data.description ?? null,
      data.due_date ?? null,
      data.payment_trigger ? 1 : 0,
      data.sort_order ?? 0
    )
    .run()

  const milestone = await getMilestone(db, id)
  if (!milestone) {
    throw new Error('Failed to retrieve created milestone')
  }
  return milestone
}

/**
 * Update an existing milestone. Returns the updated record.
 */
export async function updateMilestone(
  db: D1Database,
  milestoneId: string,
  data: UpdateMilestoneData
): Promise<Milestone | null> {
  const existing = await getMilestone(db, milestoneId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.name !== undefined) {
    fields.push('name = ?')
    params.push(data.name)
  }

  if (data.description !== undefined) {
    fields.push('description = ?')
    params.push(data.description)
  }

  if (data.due_date !== undefined) {
    fields.push('due_date = ?')
    params.push(data.due_date)
  }

  if (data.payment_trigger !== undefined) {
    fields.push('payment_trigger = ?')
    params.push(data.payment_trigger ? 1 : 0)
  }

  if (data.sort_order !== undefined) {
    fields.push('sort_order = ?')
    params.push(data.sort_order)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE milestones SET ${fields.join(', ')} WHERE id = ?`
  params.push(milestoneId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getMilestone(db, milestoneId)
}

/**
 * Transition milestone status with validation.
 * Returns the updated record or null if the milestone was not found.
 * Throws if the transition is invalid.
 *
 * When transitioning to completed, auto-sets completed_at.
 */
export async function updateMilestoneStatus(
  db: D1Database,
  milestoneId: string,
  newStatus: MilestoneStatus
): Promise<Milestone | null> {
  const existing = await getMilestone(db, milestoneId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status as MilestoneStatus
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  const updates: string[] = ['status = ?']
  const params: (string | number | null)[] = [newStatus]

  // When transitioning to completed, auto-set completed_at
  if (newStatus === 'completed') {
    updates.push('completed_at = ?')
    params.push(new Date().toISOString())
  }

  const sql = `UPDATE milestones SET ${updates.join(', ')} WHERE id = ?`
  params.push(milestoneId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getMilestone(db, milestoneId)
}

/**
 * Bulk create milestones for an engagement (e.g. from a template).
 * Returns the array of created milestones.
 */
export async function bulkCreateMilestones(
  db: D1Database,
  engagementId: string,
  milestones: CreateMilestoneData[]
): Promise<Milestone[]> {
  const created: Milestone[] = []

  for (let i = 0; i < milestones.length; i++) {
    const data = milestones[i]
    const milestone = await createMilestone(db, engagementId, {
      ...data,
      sort_order: data.sort_order ?? i,
    })
    created.push(milestone)
  }

  return created
}

/**
 * Delete a milestone. Returns true if the milestone was found and deleted.
 */
export async function deleteMilestone(db: D1Database, milestoneId: string): Promise<boolean> {
  const existing = await getMilestone(db, milestoneId)
  if (!existing) {
    return false
  }

  await db.prepare('DELETE FROM milestones WHERE id = ?').bind(milestoneId).run()

  return true
}
