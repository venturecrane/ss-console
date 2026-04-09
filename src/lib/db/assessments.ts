/**
 * Assessment data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

export interface Assessment {
  id: string
  org_id: string
  entity_id: string
  scheduled_at: string | null
  completed_at: string | null
  duration_minutes: number | null
  transcript_path: string | null
  extraction: string | null
  live_notes: string | null
  status: string
  created_at: string
}

export type AssessmentStatus =
  | 'scheduled'
  | 'completed'
  | 'disqualified'
  | 'converted'
  | 'cancelled'

export const ASSESSMENT_STATUSES: { value: AssessmentStatus; label: string }[] = [
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'disqualified', label: 'Disqualified' },
  { value: 'converted', label: 'Converted' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * scheduled   -> completed | disqualified | cancelled
 * completed   -> disqualified | converted
 * disqualified -> (terminal)
 * converted   -> (terminal)
 * cancelled   -> (terminal — cancelled guests start a new booking flow)
 *
 * Cancelled is a first-class status added in migration 0011 to support
 * guest-initiated cancellation through the booking flow. The CHECK
 * constraint on assessments.status enforces this set at the DB level.
 */
export const VALID_TRANSITIONS: Record<AssessmentStatus, AssessmentStatus[]> = {
  scheduled: ['completed', 'disqualified', 'cancelled'],
  completed: ['disqualified', 'converted'],
  disqualified: [],
  converted: [],
  cancelled: [],
}

export interface CreateAssessmentData {
  scheduled_at?: string | null
}

export interface UpdateAssessmentData {
  scheduled_at?: string | null
  completed_at?: string | null
  duration_minutes?: number | null
  transcript_path?: string | null
  extraction?: string | null
  live_notes?: string | null
}

/**
 * List assessments for an organization, optionally filtered by entity.
 */
export async function listAssessments(
  db: D1Database,
  orgId: string,
  entityId?: string
): Promise<Assessment[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (entityId) {
    conditions.push('entity_id = ?')
    params.push(entityId)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM assessments WHERE ${where} ORDER BY created_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Assessment>()
  return result.results
}

/**
 * Get a single assessment by ID, scoped to an organization.
 */
export async function getAssessment(
  db: D1Database,
  orgId: string,
  assessmentId: string
): Promise<Assessment | null> {
  const result = await db
    .prepare('SELECT * FROM assessments WHERE id = ? AND org_id = ?')
    .bind(assessmentId, orgId)
    .first<Assessment>()

  return result ?? null
}

/**
 * Create a new assessment linked to an entity. Returns the created record.
 */
export async function createAssessment(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: CreateAssessmentData
): Promise<Assessment> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
     VALUES (?, ?, ?, ?, 'scheduled', ?)`
    )
    .bind(id, orgId, entityId, data.scheduled_at ?? null, now)
    .run()

  const assessment = await getAssessment(db, orgId, id)
  if (!assessment) {
    throw new Error('Failed to retrieve created assessment')
  }
  return assessment
}

/**
 * Update an existing assessment. Returns the updated record.
 */
export async function updateAssessment(
  db: D1Database,
  orgId: string,
  assessmentId: string,
  data: UpdateAssessmentData
): Promise<Assessment | null> {
  const existing = await getAssessment(db, orgId, assessmentId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.scheduled_at !== undefined) {
    fields.push('scheduled_at = ?')
    params.push(data.scheduled_at)
  }

  if (data.completed_at !== undefined) {
    fields.push('completed_at = ?')
    params.push(data.completed_at)
  }

  if (data.duration_minutes !== undefined) {
    fields.push('duration_minutes = ?')
    params.push(data.duration_minutes)
  }

  if (data.transcript_path !== undefined) {
    fields.push('transcript_path = ?')
    params.push(data.transcript_path)
  }

  if (data.extraction !== undefined) {
    fields.push('extraction = ?')
    params.push(data.extraction)
  }

  if (data.live_notes !== undefined) {
    fields.push('live_notes = ?')
    params.push(data.live_notes)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE assessments SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(assessmentId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getAssessment(db, orgId, assessmentId)
}

/**
 * Transition assessment status with validation.
 * Returns the updated record or null if the assessment was not found.
 * Throws if the transition is invalid.
 */
export async function updateAssessmentStatus(
  db: D1Database,
  orgId: string,
  assessmentId: string,
  newStatus: AssessmentStatus
): Promise<Assessment | null> {
  const existing = await getAssessment(db, orgId, assessmentId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status as AssessmentStatus
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  // When transitioning to completed, auto-set completed_at if not already set
  const updates: string[] = ['status = ?']
  const params: (string | number | null)[] = [newStatus]

  if (newStatus === 'completed' && !existing.completed_at) {
    updates.push('completed_at = ?')
    params.push(new Date().toISOString())
  }

  const sql = `UPDATE assessments SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(assessmentId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getAssessment(db, orgId, assessmentId)
}
