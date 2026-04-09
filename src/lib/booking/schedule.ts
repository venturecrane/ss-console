/**
 * Assessment schedule data access layer.
 *
 * The assessment_schedule table is a 1:1 sidecar on assessments, holding
 * booking-specific metadata: slot times, guest identity, Google Calendar
 * linkage, manage token, and cancellation tracking.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID().
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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
  google_sync_state: GoogleSyncState
  google_last_error: string | null
  manage_token_hash: string
  manage_token_expires_at: string | null
  cancelled_at: string | null
  cancelled_reason: string | null
  cancelled_by: string | null
  reschedule_count: number
  previous_slot_utc: string | null
  reminder_sent_at: string | null
  created_at: string
  updated_at: string
}

export type GoogleSyncState = 'pending' | 'synced' | 'error' | 'cancelled'

export interface CreateAssessmentScheduleInput {
  assessment_id: string
  org_id: string
  slot_start_utc: string
  slot_end_utc: string
  duration_minutes?: number
  timezone?: string
  guest_timezone?: string | null
  guest_name: string
  guest_email: string
  manage_token_hash: string
  manage_token_expires_at?: string | null
}

/** Result of getScheduleByManageToken — includes assessment status for guard checks. */
export interface ScheduleWithStatus extends AssessmentSchedule {
  assessment_status: string
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/**
 * Create a new assessment schedule sidecar row.
 */
export async function createAssessmentSchedule(
  db: D1Database,
  input: CreateAssessmentScheduleInput
): Promise<AssessmentSchedule> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
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
      input.assessment_id,
      input.org_id,
      input.slot_start_utc,
      input.slot_end_utc,
      input.duration_minutes ?? 30,
      input.timezone ?? 'America/Phoenix',
      input.guest_timezone ?? null,
      input.guest_name,
      input.guest_email,
      input.manage_token_hash,
      input.manage_token_expires_at ?? null,
      now,
      now
    )
    .run()

  const schedule = await getScheduleById(db, id)
  if (!schedule) throw new Error('Failed to retrieve created assessment schedule')
  return schedule
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

async function getScheduleById(db: D1Database, id: string): Promise<AssessmentSchedule | null> {
  const result = await db
    .prepare('SELECT * FROM assessment_schedule WHERE id = ?')
    .bind(id)
    .first<AssessmentSchedule>()

  return result ?? null
}

/**
 * Get the schedule sidecar for an assessment.
 */
export async function getScheduleByAssessmentId(
  db: D1Database,
  assessmentId: string
): Promise<AssessmentSchedule | null> {
  const result = await db
    .prepare('SELECT * FROM assessment_schedule WHERE assessment_id = ?')
    .bind(assessmentId)
    .first<AssessmentSchedule>()

  return result ?? null
}

/**
 * Look up a schedule by its manage token hash. Joins assessments to include
 * the assessment status, which callers need for guard checks (e.g. preventing
 * cancellation of already-completed assessments).
 */
export async function getScheduleByManageToken(
  db: D1Database,
  tokenHash: string
): Promise<ScheduleWithStatus | null> {
  const result = await db
    .prepare(
      `SELECT s.*, a.status AS assessment_status
       FROM assessment_schedule s
       JOIN assessments a ON a.id = s.assessment_id
       WHERE s.manage_token_hash = ?`
    )
    .bind(tokenHash)
    .first<ScheduleWithStatus>()

  return result ?? null
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

/**
 * Update Google Calendar sync state and related fields after a sync attempt.
 */
export async function updateGoogleSyncState(
  db: D1Database,
  scheduleId: string,
  state: GoogleSyncState,
  eventId?: string,
  eventLink?: string,
  meetUrl?: string,
  lastError?: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE assessment_schedule SET
        google_sync_state = ?,
        google_event_id = COALESCE(?, google_event_id),
        google_event_link = COALESCE(?, google_event_link),
        google_meet_url = COALESCE(?, google_meet_url),
        google_last_error = ?,
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(state, eventId ?? null, eventLink ?? null, meetUrl ?? null, lastError ?? null, scheduleId)
    .run()
}

/**
 * Mark a schedule as cancelled by setting google_sync_state to 'cancelled'.
 */
export async function cancelSchedule(db: D1Database, scheduleId: string): Promise<void> {
  await db
    .prepare(
      `UPDATE assessment_schedule SET
        google_sync_state = 'cancelled',
        updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(scheduleId)
    .run()
}
