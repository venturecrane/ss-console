/**
 * assessment_schedule data access layer.
 *
 * The assessment_schedule table is a 1:1 sidecar on assessments containing
 * booking-specific metadata: slot times, Google Calendar linkage, manage
 * token hash, and cancellation/reschedule tracking.
 *
 * All queries are parameterized. Primary keys use crypto.randomUUID().
 */

export interface AssessmentSchedule {
  id: string
  assessment_id: string
  org_id: string
  slot_start_utc: string
  slot_end_utc: string
  duration_minutes: number
  timezone: string
  guest_timezone: string | null
  guest_name: string
  guest_email: string
  google_event_id: string | null
  google_event_link: string | null
  google_meet_url: string | null
  google_sync_state: 'pending' | 'synced' | 'error' | 'cancelled'
  google_last_error: string | null
  manage_token_hash: string
  manage_token_expires_at: string | null
  cancelled_at: string | null
  cancelled_reason: string | null
  cancelled_by: 'guest' | 'admin' | 'system' | null
  reschedule_count: number
  previous_slot_utc: string | null
  reminder_sent_at: string | null
  created_at: string
  updated_at: string
}

export interface CreateScheduleData {
  assessmentId: string
  orgId: string
  slotStartUtc: string
  slotEndUtc: string
  durationMinutes: number
  timezone: string
  guestTimezone?: string | null
  guestName: string
  guestEmail: string
  manageTokenHash: string
  manageTokenExpiresAt: string
}

/**
 * Create an assessment_schedule sidecar row. Returns a D1PreparedStatement
 * suitable for batching with other statements in `db.batch()`.
 */
export function createScheduleStatement(
  db: D1Database,
  data: CreateScheduleData
): { statement: D1PreparedStatement; id: string } {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  const statement = db
    .prepare(
      `INSERT INTO assessment_schedule (
        id, assessment_id, org_id,
        slot_start_utc, slot_end_utc, duration_minutes, timezone, guest_timezone,
        guest_name, guest_email,
        manage_token_hash, manage_token_expires_at,
        created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.assessmentId,
      data.orgId,
      data.slotStartUtc,
      data.slotEndUtc,
      data.durationMinutes,
      data.timezone,
      data.guestTimezone ?? null,
      data.guestName,
      data.guestEmail,
      data.manageTokenHash,
      data.manageTokenExpiresAt,
      now,
      now
    )

  return { statement, id }
}

/**
 * Get the schedule sidecar for an assessment.
 */
export async function getScheduleByAssessmentId(
  db: D1Database,
  orgId: string,
  assessmentId: string
): Promise<AssessmentSchedule | null> {
  return (
    (await db
      .prepare('SELECT * FROM assessment_schedule WHERE assessment_id = ? AND org_id = ?')
      .bind(assessmentId, orgId)
      .first<AssessmentSchedule>()) ?? null
  )
}

/**
 * Update Google Calendar sync fields after successful event creation.
 */
export async function updateScheduleGoogleSync(
  db: D1Database,
  scheduleId: string,
  data: {
    googleEventId: string
    googleEventLink: string | null
    googleMeetUrl: string | null
  }
): Promise<void> {
  await db
    .prepare(
      `UPDATE assessment_schedule SET
        google_event_id = ?,
        google_event_link = ?,
        google_meet_url = ?,
        google_sync_state = 'synced',
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(data.googleEventId, data.googleEventLink, data.googleMeetUrl, scheduleId)
    .run()
}

/**
 * Mark the schedule's Google sync as failed.
 */
export async function markScheduleGoogleError(
  db: D1Database,
  scheduleId: string,
  error: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE assessment_schedule SET
        google_sync_state = 'error',
        google_last_error = ?,
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(error, scheduleId)
    .run()
}
