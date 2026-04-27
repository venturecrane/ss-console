import { recomputeDeterministicCache } from '../entities/recompute.js'

export interface RollbackFailedBookingInput {
  orgId: string
  holdId: string
  scheduleId: string
  meetingScheduleId: string
  assessmentId: string
  meetingId: string
  preserveBookingRows: boolean
  previousAssessmentScheduledAt: string | null
  previousMeetingScheduledAt: string | null
  entityId?: string
  entityCreated?: boolean
  contactId?: string
  contactCreated?: boolean
  contextId?: string | null
}

/**
 * Compensating rollback for failed booking reserves.
 *
 * A booking is only "real" after Google sync succeeds. If any later phase
 * fails, remove or restore every artifact created during intake so the CRM
 * does not drift into a false partially-booked state.
 */
export async function rollbackFailedBooking(
  db: D1Database,
  input: RollbackFailedBookingInput
): Promise<void> {
  await db.prepare('DELETE FROM meeting_schedule WHERE id = ?').bind(input.meetingScheduleId).run()
  await db.prepare('DELETE FROM assessment_schedule WHERE id = ?').bind(input.scheduleId).run()

  if (input.preserveBookingRows) {
    await db
      .prepare('UPDATE meetings SET scheduled_at = ? WHERE id = ? AND org_id = ?')
      .bind(input.previousMeetingScheduledAt, input.meetingId, input.orgId)
      .run()
    await db
      .prepare('UPDATE assessments SET scheduled_at = ? WHERE id = ? AND org_id = ?')
      .bind(input.previousAssessmentScheduledAt, input.assessmentId, input.orgId)
      .run()
  } else {
    await db
      .prepare('DELETE FROM meetings WHERE id = ? AND org_id = ?')
      .bind(input.meetingId, input.orgId)
      .run()
    await db
      .prepare('DELETE FROM assessments WHERE id = ? AND org_id = ?')
      .bind(input.assessmentId, input.orgId)
      .run()
  }

  if (input.contextId) {
    await db
      .prepare('DELETE FROM context WHERE id = ? AND org_id = ?')
      .bind(input.contextId, input.orgId)
      .run()
  }

  if (input.contactCreated && input.contactId) {
    await db
      .prepare('DELETE FROM contacts WHERE id = ? AND org_id = ?')
      .bind(input.contactId, input.orgId)
      .run()
  }

  if (input.entityCreated && input.entityId) {
    await db
      .prepare('DELETE FROM entities WHERE id = ? AND org_id = ?')
      .bind(input.entityId, input.orgId)
      .run()
  } else if (input.contextId && input.entityId) {
    await recomputeDeterministicCache(db, input.orgId, input.entityId)
  }

  await db.prepare('DELETE FROM booking_holds WHERE id = ?').bind(input.holdId).run()
}
