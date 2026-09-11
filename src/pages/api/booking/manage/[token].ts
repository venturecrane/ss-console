import { jsonResponse, errorResponse } from '../../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { hashManageToken } from '../../../../lib/booking/tokens'
import { getScheduleByManageToken, isManageTokenExpired } from '../../../../lib/booking/schedule'
import { BOOKING_CONFIG } from '../../../../lib/booking/config'
import { formatInTimeZone } from 'date-fns-tz'
import { env } from 'cloudflare:workers'

/**
 * GET /api/booking/manage/[token]
 *
 * Resolve a raw manage token into booking details. The token itself IS the
 * auth — no session required. Used by the /book/manage/[token] Astro page
 * to hydrate the manage UI.
 *
 * Returns booking details as JSON, or 404/410 for invalid/expired tokens.
 */
export const GET: APIRoute = async ({ params }) => {
  const rawToken = params.token

  if (!rawToken || typeof rawToken !== 'string') {
    return errorResponse(400, 'missing_token')
  }

  try {
    const tokenHash = await hashManageToken(rawToken)
    const schedule = await getScheduleByManageToken(env.DB, tokenHash)

    if (!schedule) {
      return errorResponse(
        404,
        'not_found',
        'This booking link is not valid. It may have already been used or the booking was cancelled.'
      )
    }

    if (isManageTokenExpired(schedule)) {
      return errorResponse(
        410,
        'expired',
        'This manage link has expired. Please contact us if you need to make changes.'
      )
    }

    const displayTz = schedule.guest_timezone || schedule.timezone
    const slotLabel = formatInTimeZone(
      new Date(schedule.slot_start_utc),
      displayTz,
      "EEEE, MMMM d 'at' h:mm a (zzz)"
    )

    return jsonResponse(200, {
      schedule_id: schedule.id,
      assessment_id: schedule.assessment_id,
      slot_start_utc: schedule.slot_start_utc,
      slot_end_utc: schedule.slot_end_utc,
      slot_label: slotLabel,
      duration_minutes: schedule.duration_minutes,
      timezone: displayTz,
      guest_name: schedule.guest_name,
      guest_email: schedule.guest_email,
      google_meet_url: schedule.google_meet_url,
      cancelled_at: schedule.cancelled_at,
      reschedule_count: schedule.reschedule_count,
      meeting_label: BOOKING_CONFIG.meeting_label,
    })
  } catch (err) {
    console.error('[api/booking/manage] Error:', err)
    return errorResponse(500, 'internal_error')
  }
}
