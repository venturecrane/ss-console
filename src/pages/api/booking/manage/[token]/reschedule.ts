import { escapeHtml, jsonResponse } from '../../../../../lib/api/helpers'
import type { APIContext, APIRoute } from 'astro'
import { ORG_ID } from '../../../../../lib/constants'
import { hashManageToken, computeManageTokenExpiry } from '../../../../../lib/booking/tokens'
import {
  getScheduleByManageToken,
  isManageTokenExpired,
  updateScheduleForReschedule,
  updateScheduleGoogleSync,
} from '../../../../../lib/booking/schedule'
import {
  getMeetingScheduleByManageToken,
  updateMeetingScheduleForReschedule,
  updateMeetingScheduleGoogleSync,
} from '../../../../../lib/booking/meeting-schedule'
import { BOOKING_CONFIG } from '../../../../../lib/booking/config'
import { acquireHold, releaseHold } from '../../../../../lib/booking/holds'
import { getIntegration, getGoogleAccessToken } from '../../../../../lib/db/integrations'
import { updateCalendarEvent } from '../../../../../lib/booking/google-calendar'
import { updateAssessment } from '../../../../../lib/db/assessments'
import { updateMeeting } from '../../../../../lib/db/meetings'
import { buildIcs, icsToBase64 } from '../../../../../lib/booking/ics'
import { sendBookingReschedule } from '../../../../../lib/email/booking-emails'
import { sendEmail } from '../../../../../lib/email/resend'
import { recordBookingError } from '../../../../../lib/booking/alerts'
import { requireAppBaseUrl } from '../../../../../lib/config/app-url'
import { formatInTimeZone } from 'date-fns-tz'
import { env } from 'cloudflare:workers'

const NOTIFY_EMAIL = 'team@smd.services'

/**
 * POST /api/booking/manage/[token]/reschedule
 *
 * Guest-initiated reschedule. The manage token IS the auth.
 * Cannot reschedule if already cancelled (409).
 *
 * Steps:
 *   1. Validate token + parse new slot from body
 *   2. Acquire hold on new slot
 *   3. Update Google Calendar event (failure = reschedule failure)
 *   4. Update schedule + assessment in DB
 *   5. Release hold, send emails
 */

type Schedule = NonNullable<Awaited<ReturnType<typeof getScheduleByManageToken>>>

async function updateGoogleCalendarEvent(
  schedule: Schedule,
  newSlotStartUtc: string,
  newSlotEndUtc: string
): Promise<string | null> {
  if (!schedule.google_event_id) return null

  const integration = await getIntegration(env.DB, ORG_ID, 'google_calendar')
  if (!integration) throw new Error('No Google Calendar integration')

  const accessToken = await getGoogleAccessToken(env.DB, integration, env)
  if (!accessToken) throw new Error('Failed to get Google access token')

  const calendarId = integration.calendar_id || BOOKING_CONFIG.consultant.calendar_id
  const updatedEvent = await updateCalendarEvent(
    accessToken,
    calendarId,
    schedule.google_event_id,
    {
      start: { dateTime: newSlotStartUtc, timeZone: 'UTC' },
      end: { dateTime: newSlotEndUtc, timeZone: 'UTC' },
    }
  )

  await updateScheduleGoogleSync(env.DB, schedule.id, {
    googleEventId: updatedEvent.id,
    googleEventLink: updatedEvent.htmlLink ?? null,
    googleMeetUrl: BOOKING_CONFIG.meeting_url,
  })

  // Mirror the sync update to meeting_schedule during the monitoring window.
  try {
    const meetingSchedule = await getMeetingScheduleByManageToken(
      env.DB,
      schedule.manage_token_hash
    )
    if (meetingSchedule) {
      await updateMeetingScheduleGoogleSync(env.DB, meetingSchedule.id, {
        googleEventId: updatedEvent.id,
        googleEventLink: updatedEvent.htmlLink ?? null,
        googleMeetUrl: BOOKING_CONFIG.meeting_url,
      })
    }
  } catch (mirrorErr) {
    console.error(
      '[api/booking/manage/reschedule] Meeting-schedule google sync mirror failed:',
      mirrorErr
    )
  }

  return updatedEvent.id
}

async function mirrorRescheduleToMeetings(
  manageTokenHash: string,
  newSlotStartUtc: string,
  newSlotEndUtc: string,
  newManageTokenExpiresAt: string,
  assessmentId: string
): Promise<void> {
  try {
    const meetingSchedule = await getMeetingScheduleByManageToken(env.DB, manageTokenHash)
    if (meetingSchedule) {
      await updateMeetingScheduleForReschedule(
        env.DB,
        meetingSchedule.id,
        newSlotStartUtc,
        newSlotEndUtc,
        newManageTokenExpiresAt
      )
    }
  } catch (err) {
    console.error('[api/booking/manage/reschedule] Meeting schedule reschedule failed:', err)
  }

  try {
    await updateAssessment(env.DB, ORG_ID, assessmentId, { scheduled_at: newSlotStartUtc })
  } catch (err) {
    console.error('[api/booking/manage/reschedule] Assessment update failed:', err)
  }

  try {
    await updateMeeting(env.DB, ORG_ID, assessmentId, { scheduled_at: newSlotStartUtc })
  } catch (err) {
    console.error('[api/booking/manage/reschedule] Meeting update failed:', err)
  }
}

function buildRescheduleIcs(
  schedule: Schedule,
  newSlotStartUtc: string,
  manageUrl: string
): { filename: string; content: string; content_type: string } | null {
  try {
    const icsResult = buildIcs({
      scheduleId: schedule.id,
      sequence: schedule.reschedule_count + 1,
      method: 'REQUEST',
      startUtc: newSlotStartUtc,
      durationMinutes: BOOKING_CONFIG.slot_minutes,
      title: `${BOOKING_CONFIG.meeting_label} — SMD Services`,
      description: `Assessment call with SMD Services.\n\nManage your booking: ${manageUrl}`,
      location: schedule.google_meet_url ?? undefined,
      organizerName: BOOKING_CONFIG.consultant.name,
      organizerEmail: BOOKING_CONFIG.consultant.email,
      guestName: schedule.guest_name,
      guestEmail: schedule.guest_email,
    })
    return {
      filename: 'invite.ics',
      content: icsToBase64(icsResult.ics),
      content_type: icsResult.contentType,
    }
  } catch (icsErr) {
    console.error('[api/booking/manage/reschedule] ICS generation failed:', icsErr)
    return null
  }
}

async function sendRescheduleEmails(
  schedule: Schedule,
  newSlotStartUtc: string,
  oldSlotLabel: string,
  newSlotLabel: string,
  manageUrl: string
): Promise<void> {
  const icsAttachment = buildRescheduleIcs(schedule, newSlotStartUtc, manageUrl)

  async function alertGuestEmailFailure(message: string): Promise<void> {
    try {
      await recordBookingError(env.DB, env.RESEND_API_KEY, 'guest_email_delivery_failed', {
        assessmentId: schedule.assessment_id,
        scheduleId: schedule.id,
        message,
      })
    } catch (alertErr) {
      console.error('[api/booking/manage/reschedule] Failed to record guest email alert:', alertErr)
    }
  }

  try {
    const guestResult = await sendBookingReschedule(env.RESEND_API_KEY, {
      guestName: schedule.guest_name,
      guestEmail: schedule.guest_email,
      businessName: '',
      oldSlotLabel,
      newSlotLabel,
      meetUrl: schedule.google_meet_url,
      manageUrl,
      meetingLabel: BOOKING_CONFIG.meeting_label,
      icsAttachment,
      meetingId: schedule.assessment_id,
    })
    if (!guestResult.success) {
      console.error('[api/booking/manage/reschedule] Reschedule email NOT sent:', guestResult.error)
      await alertGuestEmailFailure(
        `Reschedule email to ${schedule.guest_email} was rejected: ${guestResult.error ?? 'unknown error'}`
      )
    }
  } catch (emailErr) {
    console.error('[api/booking/manage/reschedule] Reschedule email failed:', emailErr)
    await alertGuestEmailFailure(
      `Reschedule email to ${schedule.guest_email} threw: ${
        emailErr instanceof Error ? emailErr.message : String(emailErr)
      }`
    )
  }

  try {
    const adminNewLabel = formatInTimeZone(
      new Date(newSlotStartUtc),
      BOOKING_CONFIG.consultant.timezone,
      "EEEE, MMMM d 'at' h:mm a (zzz)"
    )
    const adminResult = await sendEmail(env.RESEND_API_KEY, {
      to: NOTIFY_EMAIL,
      reply_to: schedule.guest_email,
      subject: `Rescheduled: ${schedule.guest_name} — ${adminNewLabel}`,
      html: `<p><strong>${escapeHtml(schedule.guest_name)}</strong> rescheduled their assessment call.</p><p>Old: ${escapeHtml(oldSlotLabel)}</p><p>New: <strong>${escapeHtml(newSlotLabel)}</strong></p>`,
    })
    if (!adminResult.success) {
      console.error(
        '[api/booking/manage/reschedule] Admin notification NOT sent:',
        adminResult.error
      )
    }
  } catch (emailErr) {
    console.error('[api/booking/manage/reschedule] Admin notification failed:', emailErr)
  }
}

interface ParsedSlot {
  newSlotStartUtc: string
  newSlotEndUtc: string
}

function parseSlotParams(body: Record<string, unknown>): ParsedSlot | Response {
  const newSlotStartUtc =
    typeof body.slot_start_utc === 'string' ? body.slot_start_utc.trim() : null
  if (!newSlotStartUtc) return jsonResponse(400, { error: 'slot_start_utc is required' })

  const newSlotStart = new Date(newSlotStartUtc)
  if (isNaN(newSlotStart.getTime())) return jsonResponse(400, { error: 'Invalid slot_start_utc' })

  const earliest = Date.now() + BOOKING_CONFIG.min_notice_minutes * 60_000
  if (newSlotStart.getTime() < earliest) {
    return jsonResponse(400, {
      error: 'slot_unavailable',
      message: 'This slot is no longer available. Please choose a later time.',
    })
  }

  const newSlotEndUtc = new Date(
    newSlotStart.getTime() + BOOKING_CONFIG.slot_minutes * 60_000
  ).toISOString()
  return { newSlotStartUtc, newSlotEndUtc }
}

async function commitRescheduleAndNotify(
  schedule: Schedule,
  newSlotStartUtc: string,
  newSlotEndUtc: string,
  holdId: string,
  rawToken: string
): Promise<Response> {
  try {
    await updateGoogleCalendarEvent(schedule, newSlotStartUtc, newSlotEndUtc)
  } catch (err) {
    console.error('[api/booking/manage/reschedule] Google Calendar update failed:', err)
    await releaseHold(env.DB, holdId)
    return jsonResponse(503, {
      error: 'calendar_sync_failed',
      message: 'We could not update the calendar event. Please try again.',
    })
  }

  const newManageTokenExpiresAt = computeManageTokenExpiry(
    newSlotEndUtc,
    BOOKING_CONFIG.manage_token_ttl_hours_after_slot
  )
  await updateScheduleForReschedule(
    env.DB,
    schedule.id,
    newSlotStartUtc,
    newSlotEndUtc,
    newManageTokenExpiresAt
  )
  await mirrorRescheduleToMeetings(
    schedule.manage_token_hash,
    newSlotStartUtc,
    newSlotEndUtc,
    newManageTokenExpiresAt,
    schedule.assessment_id
  )
  await releaseHold(env.DB, holdId)

  const displayTz = schedule.guest_timezone || schedule.timezone
  const slotFmt = "EEEE, MMMM d 'at' h:mm a (zzz)"
  const oldSlotLabel = formatInTimeZone(new Date(schedule.slot_start_utc), displayTz, slotFmt)
  const newSlotLabel = formatInTimeZone(new Date(newSlotStartUtc), displayTz, slotFmt)

  let appBaseUrl: string
  try {
    appBaseUrl = requireAppBaseUrl(env)
  } catch {
    appBaseUrl = 'https://smd.services'
  }
  const manageUrl = `${appBaseUrl}/book/manage?token=${rawToken}`

  await sendRescheduleEmails(schedule, newSlotStartUtc, oldSlotLabel, newSlotLabel, manageUrl)

  return jsonResponse(200, {
    ok: true,
    slot_start_utc: newSlotStartUtc,
    slot_end_utc: newSlotEndUtc,
    slot_label: newSlotLabel,
  })
}

async function handlePost({ params, request }: APIContext): Promise<Response> {
  const rawToken = params.token
  if (!rawToken || typeof rawToken !== 'string')
    return jsonResponse(400, { error: 'Missing token' })

  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  const slotOrError = parseSlotParams(body)
  if (slotOrError instanceof Response) return slotOrError
  const { newSlotStartUtc, newSlotEndUtc } = slotOrError

  try {
    const tokenHash = await hashManageToken(rawToken)
    const schedule = await getScheduleByManageToken(env.DB, tokenHash)

    if (!schedule)
      return jsonResponse(404, { error: 'not_found', message: 'This booking link is not valid.' })
    if (isManageTokenExpired(schedule)) {
      return jsonResponse(410, {
        error: 'expired',
        message: 'This manage link has expired. Please contact us if you need to make changes.',
      })
    }
    if (schedule.cancelled_at) {
      return jsonResponse(409, {
        error: 'cancelled',
        message: 'This booking has been cancelled and cannot be rescheduled.',
      })
    }

    const holdResult = await acquireHold(env.DB, ORG_ID, newSlotStartUtc, schedule.guest_email)
    if (!holdResult.acquired) {
      return jsonResponse(409, {
        error: 'slot_taken',
        message: 'This time slot was just taken. Please choose another time.',
      })
    }

    return await commitRescheduleAndNotify(
      schedule,
      newSlotStartUtc,
      newSlotEndUtc,
      holdResult.id!,
      rawToken
    )
  } catch (err) {
    console.error('[api/booking/manage/reschedule] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
