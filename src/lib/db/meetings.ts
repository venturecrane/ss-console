/**
 * Meeting data access layer (#469).
 *
 * Meetings generalize the former `assessments` table across every meeting
 * type — discovery calls, assessments, follow-ups, delivery reviews,
 * check-ins. The `meeting_type` column is nullable because the system does
 * not require callers to classify a meeting.
 *
 * Migration 0025 backfilled `meetings` from `assessments` preserving IDs, so
 * quotes.assessment_id continues to resolve to the same logical row during
 * the monitoring window. The legacy `assessments` table remains until a
 * follow-up drop migration lands.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID().
 */

export interface Meeting {
  id: string
  org_id: string
  entity_id: string
  /** Optional tag: 'assessment' | 'discovery' | 'follow_up' | 'review' | 'check_in' | null. */
  meeting_type: string | null
  scheduled_at: string | null
  completed_at: string | null
  duration_minutes: number | null
  transcript_path: string | null
  extraction: string | null
  live_notes: string | null
  /** Free-form completion notes written by the admin when the meeting is completed. */
  completion_notes: string | null
  status: MeetingStatus
  created_at: string
}

export type MeetingStatus = 'scheduled' | 'completed' | 'disqualified' | 'converted' | 'cancelled'

export const MEETING_STATUSES: { value: MeetingStatus; label: string }[] = [
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'disqualified', label: 'Disqualified' },
  { value: 'converted', label: 'Converted' },
]

/**
 * Valid status transitions enforced at the application layer. Mirrors the
 * legacy assessment state machine; the CHECK constraint on meetings.status
 * enforces this same set at the DB level.
 */
export const VALID_TRANSITIONS: Record<MeetingStatus, MeetingStatus[]> = {
  scheduled: ['completed', 'disqualified', 'cancelled'],
  completed: ['disqualified', 'converted'],
  disqualified: [],
  converted: [],
  cancelled: [],
}

export interface CreateMeetingData {
  scheduled_at?: string | null
  meeting_type?: string | null
}

export interface EnsureMeetingForAssessmentData {
  assessmentId: string
  scheduled_at?: string | null
  meeting_type?: string | null
}

export interface EnsureMeetingForAssessmentResult {
  meeting: Meeting
  created: boolean
  previousScheduledAt: string | null
}

export interface UpdateMeetingData {
  scheduled_at?: string | null
  completed_at?: string | null
  duration_minutes?: number | null
  transcript_path?: string | null
  extraction?: string | null
  live_notes?: string | null
  completion_notes?: string | null
  meeting_type?: string | null
}

/**
 * For a batch of entity ids, return a Map keyed by entity_id whose value
 * is the list of all meetings (any status) for that entity, ordered with
 * the most recent meeting first within each entity.
 *
 * Recency uses scheduled_at when set, falling back to created_at — same
 * order the meetings panel renders. The list page uses this to compute
 * (a) the next scheduled meeting date and (b) the meeting sub-state
 * (awaiting-booking / upcoming / completed-awaiting-proposal) on
 * meetings-stage rows, without an N+1.
 *
 * Empty input returns an empty Map without touching the DB.
 */
export async function getMeetingsForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, Meeting[]>> {
  const result = new Map<string, Meeting[]>()
  if (entityIds.length === 0) return result

  const entityIdsJson = JSON.stringify(entityIds)
  const rows = await db
    .prepare(
      `SELECT * FROM meetings
       WHERE org_id = ?
         AND entity_id IN (SELECT value FROM json_each(?))
       ORDER BY entity_id ASC, COALESCE(scheduled_at, created_at) DESC`
    )
    .bind(orgId, entityIdsJson)
    .all<Meeting>()

  for (const row of rows.results ?? []) {
    const list = result.get(row.entity_id)
    if (list) list.push(row)
    else result.set(row.entity_id, [row])
  }
  return result
}

/**
 * List meetings for an organization, optionally filtered by entity.
 */
export async function listMeetings(
  db: D1Database,
  orgId: string,
  entityId?: string
): Promise<Meeting[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (entityId) {
    conditions.push('entity_id = ?')
    params.push(entityId)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM meetings WHERE ${where} ORDER BY created_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Meeting>()
  return result.results
}

/**
 * Get a single meeting by ID, scoped to an organization.
 */
export async function getMeeting(
  db: D1Database,
  orgId: string,
  meetingId: string
): Promise<Meeting | null> {
  const result = await db
    .prepare('SELECT * FROM meetings WHERE id = ? AND org_id = ?')
    .bind(meetingId, orgId)
    .first<Meeting>()

  return result ?? null
}

/**
 * Create a new meeting linked to an entity. Returns the created record.
 */
export async function createMeeting(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: CreateMeetingData
): Promise<Meeting> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO meetings (id, org_id, entity_id, meeting_type, scheduled_at, status, created_at)
     VALUES (?, ?, ?, ?, ?, 'scheduled', ?)`
    )
    .bind(id, orgId, entityId, data.meeting_type ?? null, data.scheduled_at ?? null, now)
    .run()

  const meeting = await getMeeting(db, orgId, id)
  if (!meeting) {
    throw new Error('Failed to retrieve created meeting')
  }
  return meeting
}

/**
 * Create the canonical meeting row and the legacy assessment mirror row using
 * the same primary key during the monitoring window.
 */
export async function createMeetingWithLegacyAssessment(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: CreateMeetingData
): Promise<Meeting> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  try {
    await db.batch([
      db
        .prepare(
          `INSERT INTO meetings (
            id, org_id, entity_id, meeting_type, scheduled_at, status, created_at
          ) VALUES (?, ?, ?, ?, ?, 'scheduled', ?)`
        )
        .bind(id, orgId, entityId, data.meeting_type ?? null, data.scheduled_at ?? null, now),
      db
        .prepare(
          `INSERT INTO assessments (
            id, org_id, entity_id, scheduled_at, status, created_at
          ) VALUES (?, ?, ?, ?, 'scheduled', ?)`
        )
        .bind(id, orgId, entityId, data.scheduled_at ?? null, now),
    ])
  } catch (error) {
    await db.batch([
      db.prepare('DELETE FROM meetings WHERE id = ? AND org_id = ?').bind(id, orgId),
      db.prepare('DELETE FROM assessments WHERE id = ? AND org_id = ?').bind(id, orgId),
    ])
    throw error
  }

  const meeting = await getMeeting(db, orgId, id)
  if (!meeting) {
    throw new Error('Failed to retrieve created meeting')
  }
  return meeting
}

/**
 * Ensure a canonical meeting row exists for a legacy assessment id, then keep
 * its scheduled_at / meeting_type aligned with the booking flow.
 */
export async function ensureMeetingForAssessment(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: EnsureMeetingForAssessmentData
): Promise<EnsureMeetingForAssessmentResult> {
  const existing = await getMeeting(db, orgId, data.assessmentId)
  if (existing) {
    const updated = await updateMeeting(db, orgId, data.assessmentId, {
      scheduled_at: data.scheduled_at,
      ...(data.meeting_type !== undefined ? { meeting_type: data.meeting_type } : {}),
    })
    if (!updated) {
      throw new Error('Failed to update meeting for legacy assessment')
    }
    return {
      meeting: updated,
      created: false,
      previousScheduledAt: existing.scheduled_at,
    }
  }

  const assessment = await db
    .prepare(
      `SELECT entity_id, scheduled_at, status, created_at
       FROM assessments
       WHERE id = ? AND org_id = ?`
    )
    .bind(data.assessmentId, orgId)
    .first<{
      entity_id: string
      scheduled_at: string | null
      status: string
      created_at: string
    }>()

  if (!assessment) {
    throw new Error(`Assessment not found: ${data.assessmentId}`)
  }

  if (assessment.entity_id !== entityId) {
    throw new Error(`Assessment ${data.assessmentId} does not belong to entity ${entityId}`)
  }

  try {
    await db
      .prepare(
        `INSERT INTO meetings (
          id, org_id, entity_id, meeting_type, scheduled_at, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        data.assessmentId,
        orgId,
        entityId,
        data.meeting_type ?? 'assessment',
        data.scheduled_at ?? assessment.scheduled_at,
        assessment.status,
        assessment.created_at
      )
      .run()
  } catch (error) {
    const raced = await getMeeting(db, orgId, data.assessmentId)
    if (!raced) throw error

    const updated = await updateMeeting(db, orgId, data.assessmentId, {
      scheduled_at: data.scheduled_at,
      ...(data.meeting_type !== undefined ? { meeting_type: data.meeting_type } : {}),
    })
    if (!updated) {
      throw new Error('Failed to update raced meeting for legacy assessment')
    }
    return {
      meeting: updated,
      created: false,
      previousScheduledAt: raced.scheduled_at,
    }
  }

  const meeting = await getMeeting(db, orgId, data.assessmentId)
  if (!meeting) {
    throw new Error('Failed to retrieve meeting for legacy assessment')
  }

  return {
    meeting,
    created: true,
    previousScheduledAt: null,
  }
}

/**
 * Update an existing meeting. Returns the updated record.
 */
export async function updateMeeting(
  db: D1Database,
  orgId: string,
  meetingId: string,
  data: UpdateMeetingData
): Promise<Meeting | null> {
  const existing = await getMeeting(db, orgId, meetingId)
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

  if (data.completion_notes !== undefined) {
    fields.push('completion_notes = ?')
    params.push(data.completion_notes)
  }

  if (data.meeting_type !== undefined) {
    fields.push('meeting_type = ?')
    params.push(data.meeting_type)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE meetings SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(meetingId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getMeeting(db, orgId, meetingId)
}

/**
 * Transition meeting status with validation.
 * Returns the updated record or null if the meeting was not found.
 * Throws if the transition is invalid.
 */
export async function updateMeetingStatus(
  db: D1Database,
  orgId: string,
  meetingId: string,
  newStatus: MeetingStatus
): Promise<Meeting | null> {
  const existing = await getMeeting(db, orgId, meetingId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  const updates: string[] = ['status = ?']
  const params: (string | number | null)[] = [newStatus]

  if (newStatus === 'completed' && !existing.completed_at) {
    updates.push('completed_at = ?')
    params.push(new Date().toISOString())
  }

  const sql = `UPDATE meetings SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(meetingId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getMeeting(db, orgId, meetingId)
}
