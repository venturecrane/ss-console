import type { APIRoute } from 'astro'
import { ORG_ID } from '../../../../../lib/constants'
import { hashManageToken } from '../../../../../lib/booking/tokens'
import {
  getScheduleByManageToken,
  isManageTokenExpired,
  cancelSchedule,
} from '../../../../../lib/booking/schedule'
import { getIntegration, getGoogleAccessToken } from '../../../../../lib/db/integrations'
import { deleteCalendarEvent } from '../../../../../lib/booking/google-calendar'
import { updateAssessmentStatus } from '../../../../../lib/db/assessments'
import { buildIcs, icsToBase64 } from '../../../../../lib/booking/ics'
import { BOOKING_CONFIG } from '../../../../../lib/booking/config'
import { sendBookingCancellation } from '../../../../../lib/email/booking-emails'
import { sendEmail } from '../../../../../lib/email/resend'
import { formatInTimeZone } from 'date-fns-tz'
import { env } from 'cloudflare:workers'

const NOTIFY_EMAIL = 'team@smd.services'

/**
 * POST /api/booking/manage/[token]/cancel
 *
 * Guest-initiated cancellation. The manage token IS the auth.
 * Cancelled is terminal — no reschedule from cancelled state.
 *
 * Steps:
 *   1. Validate token (hash, lookup, expiry, not already cancelled)
 *   2. Cancel schedule + transition assessment to 'cancelled'
 *   3. Delete Google Calendar event (best effort)
 *   4. Send cancellation email to guest + admin notification
 */
export const POST: APIRoute = async ({ params, request }) => {
  const rawToken = params.token

  if (!rawToken || typeof rawToken !== 'string') {
    return jsonResponse(400, { error: 'Missing token' })
  }

  // Parse optional reason from body
  let reason: string | null = null
  try {
    const body = (await request.json()) as Record<string, unknown>
    if (typeof body.reason === 'string' && body.reason.trim()) {
      reason = body.reason.trim().slice(0, 500)
    }
  } catch {
    // Empty body is fine — reason is optional
  }

  try {
    // 1. Resolve token
    const tokenHash = await hashManageToken(rawToken)
    const schedule = await getScheduleByManageToken(env.DB, tokenHash)

    if (!schedule) {
      return jsonResponse(404, {
        error: 'not_found',
        message: 'This booking link is not valid.',
      })
    }

    if (isManageTokenExpired(schedule)) {
      return jsonResponse(410, {
        error: 'expired',
        message: 'This manage link has expired. Please contact us if you need to make changes.',
      })
    }

    if (schedule.cancelled_at) {
      return jsonResponse(409, {
        error: 'already_cancelled',
        message: 'This booking has already been cancelled.',
      })
    }

    // 2. Cancel in DB
    await cancelSchedule(env.DB, schedule.id, 'guest', reason)

    try {
      await updateAssessmentStatus(env.DB, ORG_ID, schedule.assessment_id, 'cancelled')
    } catch (err) {
      console.error('[api/booking/manage/cancel] Assessment status transition failed:', err)
    }

    // 3. Delete Google Calendar event (best effort)
    if (schedule.google_event_id) {
      try {
        const integration = await getIntegration(env.DB, ORG_ID, 'google_calendar')
        if (integration) {
          const accessToken = await getGoogleAccessToken(env.DB, integration, env)
          if (accessToken) {
            const calendarId = integration.calendar_id || BOOKING_CONFIG.consultant.calendar_id
            await deleteCalendarEvent(accessToken, calendarId, schedule.google_event_id)
          }
        }
      } catch (err) {
        console.error('[api/booking/manage/cancel] Google Calendar delete failed:', err)
      }
    }

    // 4. Send emails
    const displayTz = schedule.guest_timezone || schedule.timezone
    const slotLabel = formatInTimeZone(
      new Date(schedule.slot_start_utc),
      displayTz,
      "EEEE, MMMM d 'at' h:mm a (zzz)"
    )

    // Build ICS CANCEL attachment
    let icsAttachment: { filename: string; content: string; content_type: string } | null = null
    try {
      const icsResult = buildIcs({
        scheduleId: schedule.id,
        sequence: schedule.reschedule_count + 1,
        method: 'CANCEL',
        startUtc: schedule.slot_start_utc,
        durationMinutes: schedule.duration_minutes,
        title: `${BOOKING_CONFIG.meeting_label} — SMD Services`,
        description: 'This event has been cancelled.',
        organizerName: BOOKING_CONFIG.consultant.name,
        organizerEmail: BOOKING_CONFIG.consultant.email,
        guestName: schedule.guest_name,
        guestEmail: schedule.guest_email,
      })
      icsAttachment = {
        filename: 'cancel.ics',
        content: icsToBase64(icsResult.ics),
        content_type: icsResult.contentType,
      }
    } catch (icsErr) {
      console.error('[api/booking/manage/cancel] ICS generation failed:', icsErr)
    }

    try {
      await sendBookingCancellation(env.RESEND_API_KEY, {
        guestName: schedule.guest_name,
        guestEmail: schedule.guest_email,
        businessName: '',
        slotLabel,
        rebookUrl: 'https://smd.services/book',
        icsAttachment,
      })
    } catch (emailErr) {
      console.error('[api/booking/manage/cancel] Cancellation email failed:', emailErr)
    }

    // Admin notification
    try {
      await sendEmail(env.RESEND_API_KEY, {
        to: NOTIFY_EMAIL,
        reply_to: schedule.guest_email,
        subject: `Cancelled: ${schedule.guest_name} — ${slotLabel}`,
        html: `<p><strong>${escapeHtml(schedule.guest_name)}</strong> cancelled their assessment call scheduled for <strong>${escapeHtml(slotLabel)}</strong>.</p>${reason ? `<p>Reason: ${escapeHtml(reason)}</p>` : ''}`,
      })
    } catch (emailErr) {
      console.error('[api/booking/manage/cancel] Admin notification failed:', emailErr)
    }

    return jsonResponse(200, { ok: true, cancelled: true })
  } catch (err) {
    console.error('[api/booking/manage/cancel] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
