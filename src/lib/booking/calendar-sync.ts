/**
 * Phase 3 of `POST /api/booking/reserve`: create the Google Calendar event,
 * record the sync on both schedule sidecars, promote the entity stage, and
 * on any failure roll the whole booking back so the CRM never shows a
 * meeting the calendar does not hold.
 *
 * Lived inside the route until 2026-09-10 (code review 2026-09-10,
 * Architecture 3). Returns a result, not a Response: what the client is told
 * on failure is the route's decision.
 */

import { env } from 'cloudflare:workers'
import { ORG_ID } from '../constants'
import { BOOKING_CONFIG } from './config'
import { transitionStage } from '../db/entities'
import { rollbackFailedBooking } from './rollback'
import { updateScheduleGoogleSync } from './schedule'
import { updateMeetingScheduleGoogleSync } from './meeting-schedule'
import { createGoogleCalendarEvent, buildEventDescription } from './reserve-helpers'
import type { BookingCommitResult, ReserveInput } from './commit'
import type { PreSeededIntake } from './intake-core'

export interface CalendarSyncArgs {
  accessToken: string
  calendarId: string
  input: ReserveInput
  dbResult: BookingCommitResult
  holdId: string
  preSeeded: PreSeededIntake | null
  manageUrl: string
}

export type CalendarSyncResult = { ok: true; meetUrl: string } | { ok: false }

export async function syncGoogleCalendarAndPromote(
  args: CalendarSyncArgs
): Promise<CalendarSyncResult> {
  const { accessToken, calendarId, input, dbResult, holdId, preSeeded, manageUrl } = args
  const { name, email, businessName, slotStartUtc, slotEndUtc } = input
  // prettier-ignore
  const { assessmentId, meetingId, entityId, scheduleId, meetingScheduleId, entityCreated, contactCreated, contactId, contextId, previousAssessmentScheduledAt, previousMeetingScheduledAt } = dbResult

  const meetUrl = BOOKING_CONFIG.meeting_url

  try {
    const eventResult = await createGoogleCalendarEvent(accessToken, calendarId, {
      summary: `Assessment: ${businessName} (${name})`,
      description: buildEventDescription(
        name,
        email,
        businessName,
        dbResult.intakeLines,
        manageUrl
      ),
      startUtc: slotStartUtc,
      endUtc: slotEndUtc,
      guestEmail: email,
      assessmentId,
    })

    // Update both schedules with Google sync data (dual-write during monitoring window).
    const syncData = {
      googleEventId: eventResult.eventId,
      googleEventLink: eventResult.htmlLink,
      googleMeetUrl: meetUrl,
    }
    await updateScheduleGoogleSync(env.DB, scheduleId, syncData)
    await updateMeetingScheduleGoogleSync(env.DB, meetingScheduleId, syncData)

    // Promote entity only after Google sync — prevents false "meeting scheduled" CRM state.
    try {
      await transitionStage(
        env.DB,
        ORG_ID,
        entityId,
        'meetings',
        'Booking reserve: meeting scheduled'
      )
    } catch {
      // Entity may already be past prospect. Do not fail the booking.
    }

    return { ok: true, meetUrl }
  } catch (err) {
    console.error('[booking/calendar-sync] Google Calendar event creation failed:', err)
    try {
      await rollbackFailedBooking(env.DB, {
        orgId: ORG_ID,
        holdId,
        scheduleId,
        meetingScheduleId,
        assessmentId,
        meetingId,
        preserveBookingRows: Boolean(preSeeded),
        previousAssessmentScheduledAt,
        previousMeetingScheduledAt,
        entityId,
        entityCreated,
        contactId,
        contactCreated,
        contextId,
      })
    } catch (rollbackErr) {
      console.error('[booking/calendar-sync] Rollback failed:', rollbackErr)
    }
    return { ok: false }
  }
}
