import type { APIRoute } from 'astro'
import { ORG_ID } from '../../../lib/constants'
import { BOOKING_CONFIG } from '../../../lib/booking/config'
import {
  getAvailableSlots,
  groupSlotsByLocalDate,
  formatSlotLabel,
} from '../../../lib/booking/availability'
import type { SlotRange } from '../../../lib/booking/availability'
import { getIntegration, getGoogleAccessToken } from '../../../lib/db/integrations'
import { env } from 'cloudflare:workers'

const FALLBACK_EMAIL = 'scott@smd.services'

/**
 * GET /api/booking/slots
 *
 * Public endpoint — returns available booking slots for the configured lookahead window.
 * No rate limiting (public availability is uninteresting to scrape and
 * rate-limiting punishes legitimate users behind shared NAT).
 *
 * Query params:
 *   - tz (optional): viewer timezone for labels. Defaults to consultant tz.
 *
 * Response shape:
 *   { days: [{ date: "2026-04-15", slots: [{ start_utc, end_utc, label }] }] }
 *
 * If the Google Calendar integration is not connected, returns 503 with a
 * fallback payload directing users to email directly.
 */
export const GET: APIRoute = async ({ url }) => {
  const viewerTz = url.searchParams.get('tz') || BOOKING_CONFIG.consultant.timezone

  try {
    // 1. Get active Google Calendar integration
    const integration = await getIntegration(env.DB, ORG_ID, 'google_calendar')

    if (!integration) {
      return jsonResponse(503, {
        error: 'calendar_unavailable',
        message: 'Online booking is temporarily unavailable.',
        fallback: {
          type: 'email',
          email: FALLBACK_EMAIL,
          message: `Please email ${FALLBACK_EMAIL} to schedule your call.`,
        },
      })
    }

    // 2. Get a valid access token (refreshes if needed)
    const accessToken = await getGoogleAccessToken(env.DB, integration, env)

    if (!accessToken) {
      return jsonResponse(503, {
        error: 'calendar_unavailable',
        message: 'Online booking is temporarily unavailable.',
        fallback: {
          type: 'email',
          email: FALLBACK_EMAIL,
          message: `Please email ${FALLBACK_EMAIL} to schedule your call.`,
        },
      })
    }

    // 3. Compute the time window
    const now = new Date()
    const fromUtc = now
    const toUtc = new Date(now.getTime() + BOOKING_CONFIG.max_lookahead_days * 24 * 60 * 60_000)

    // 4. Build the Google freeBusy fetcher
    const calendarId = integration.calendar_id || BOOKING_CONFIG.consultant.calendar_id
    const fetchGoogleBusy = buildGoogleBusyFetcher(accessToken, calendarId)

    // 5. Get available slots
    const slots = await getAvailableSlots(env, ORG_ID, fromUtc, toUtc, fetchGoogleBusy, now)

    // 6. Group by local date and add labels
    const grouped = groupSlotsByLocalDate(slots, viewerTz)
    const days = grouped.map((group) => ({
      date: group.date,
      slots: group.slots.map((slot) => ({
        start_utc: slot.start_utc,
        end_utc: slot.end_utc,
        label: formatSlotLabel(slot.start_utc, viewerTz),
      })),
    }))

    return jsonResponse(200, {
      days,
      timezone: viewerTz,
      slot_minutes: BOOKING_CONFIG.slot_minutes,
      meeting_label: BOOKING_CONFIG.meeting_label,
    })
  } catch (err) {
    console.error('[api/booking/slots] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

/**
 * Build a function that fetches busy ranges from Google Calendar's freeBusy
 * API. The returned function matches the signature expected by
 * `getAvailableSlots`.
 */
function buildGoogleBusyFetcher(
  accessToken: string,
  calendarId: string
): (from: Date, to: Date) => Promise<SlotRange[]> {
  return async (from: Date, to: Date): Promise<SlotRange[]> => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), BOOKING_CONFIG.google_call_timeout_ms)

    try {
      const response = await fetch('https://www.googleapis.com/calendar/v3/freeBusy', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          timeMin: from.toISOString(),
          timeMax: to.toISOString(),
          items: [{ id: calendarId }],
        }),
        signal: controller.signal,
      })

      if (!response.ok) {
        console.error(`[slots] Google freeBusy API ${response.status}: ${await response.text()}`)
        return []
      }

      const data = (await response.json()) as {
        calendars?: Record<string, { busy?: Array<{ start: string; end: string }> }>
      }

      const calendarBusy = data.calendars?.[calendarId]?.busy ?? []
      return calendarBusy.map((b) => ({
        start_utc: new Date(b.start).toISOString(),
        end_utc: new Date(b.end).toISOString(),
      }))
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        console.error('[slots] Google freeBusy request timed out')
      } else {
        console.error('[slots] Google freeBusy request failed:', err)
      }
      // On Google failure, return empty busy list — slots will be offered
      // without Google conflict checking. This is acceptable for the slots
      // endpoint; the reserve endpoint has its own Google check.
      return []
    } finally {
      clearTimeout(timeout)
    }
  }
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
