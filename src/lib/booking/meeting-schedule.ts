/**
 * meeting_schedule data access layer (#469).
 *
 * 1:1 sidecar on meetings holding booking-specific metadata: slot times,
 * Google Calendar linkage, manage token hash, and tracking for
 * cancellations and reschedules.
 *
 * Mirrors the legacy `src/lib/booking/schedule.ts` (assessment_schedule) API
 * shape exactly — the only difference is the FK/column name (meeting_id vs.
 * assessment_id). Both files coexist during the monitoring window. The
 * booking flow now writes to meeting_schedule; the legacy schedule module
 * stays available for read-path compatibility until the assessment tables
 * are dropped in a follow-up migration.
 *
 * All queries are parameterized. Primary keys use crypto.randomUUID().
 */

export interface MeetingSchedule {
  id: string
  meeting_id: string
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

export interface CreateMeetingScheduleData {
  meetingId: string
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
 * Create a meeting_schedule sidecar row. Returns a D1PreparedStatement
 * suitable for batching with other statements in `db.batch()`.
 */
export function createMeetingScheduleStatement(
  db: D1Database,
  data: CreateMeetingScheduleData
): { statement: D1PreparedStatement; id: string } {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  const statement = db
    .prepare(
      `INSERT INTO meeting_schedule (
        id, meeting_id, org_id,
        slot_start_utc, slot_end_utc, duration_minutes, timezone, guest_timezone,
        guest_name, guest_email,
        manage_token_hash, manage_token_expires_at,
        created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.meetingId,
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
 * Get the schedule sidecar for a meeting.
 */
export async function getMeetingScheduleByMeetingId(
  db: D1Database,
  orgId: string,
  meetingId: string
): Promise<MeetingSchedule | null> {
  return (
    (await db
      .prepare('SELECT * FROM meeting_schedule WHERE meeting_id = ? AND org_id = ?')
      .bind(meetingId, orgId)
      .first<MeetingSchedule>()) ?? null
  )
}

/**
 * Update Google Calendar sync fields after successful event creation.
 */
export async function updateMeetingScheduleGoogleSync(
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
      `UPDATE meeting_schedule SET
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
 * Look up a schedule by its manage_token_hash. Used by the manage endpoints
 * to resolve the raw token from the URL into a schedule row.
 */
export async function getMeetingScheduleByManageToken(
  db: D1Database,
  tokenHash: string
): Promise<MeetingSchedule | null> {
  return (
    (await db
      .prepare('SELECT * FROM meeting_schedule WHERE manage_token_hash = ?')
      .bind(tokenHash)
      .first<MeetingSchedule>()) ?? null
  )
}

/**
 * Check whether a manage token has expired.
 */
export function isMeetingManageTokenExpired(schedule: MeetingSchedule): boolean {
  if (!schedule.manage_token_expires_at) return false
  return new Date(schedule.manage_token_expires_at).getTime() < Date.now()
}

/**
 * Mark a schedule as cancelled. Sets cancelled_at, cancelled_by, and
 * cancelled_reason. Also marks Google sync state as 'cancelled'.
 */
export async function cancelMeetingSchedule(
  db: D1Database,
  scheduleId: string,
  cancelledBy: 'guest' | 'admin' | 'system',
  reason: string | null
): Promise<void> {
  await db
    .prepare(
      `UPDATE meeting_schedule SET
        cancelled_at = datetime('now'),
        cancelled_by = ?,
        cancelled_reason = ?,
        google_sync_state = 'cancelled',
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(cancelledBy, reason, scheduleId)
    .run()
}

/**
 * Update a schedule for a reschedule. Stores the old slot in previous_slot_utc,
 * sets new slot times, increments reschedule_count, and updates the manage
 * token expiry for the new slot.
 */
export async function updateMeetingScheduleForReschedule(
  db: D1Database,
  scheduleId: string,
  newSlotStartUtc: string,
  newSlotEndUtc: string,
  newManageTokenExpiresAt: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE meeting_schedule SET
        previous_slot_utc = slot_start_utc,
        slot_start_utc = ?,
        slot_end_utc = ?,
        manage_token_expires_at = ?,
        reschedule_count = reschedule_count + 1,
        google_sync_state = 'pending',
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(newSlotStartUtc, newSlotEndUtc, newManageTokenExpiresAt, scheduleId)
    .run()
}
