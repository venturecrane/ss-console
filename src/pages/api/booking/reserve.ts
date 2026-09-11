import type { APIContext, APIRoute } from 'astro'
import { ORG_ID } from '../../../lib/constants'
import { BOOKING_CONFIG } from '../../../lib/booking/config'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { acquireHold, releaseHold } from '../../../lib/booking/holds'
import type { PreSeededIntake } from '../../../lib/booking/intake-core'
import { readAttributionFromCookieHeader } from '../../../lib/marketing/attribution'
import { emitMetaEvent, mintMetaEventId } from '../../../lib/marketing/meta-capi'
import { verifyBookingLink } from '../../../lib/booking/signed-link'
import { getIntegration, getGoogleAccessToken } from '../../../lib/db/integrations'
import { sendConfirmationEmails } from '../../../lib/booking/confirmation-emails'
import {
  commitBookingToDb,
  type BookingCommitResult,
  type ReserveInput,
} from '../../../lib/booking/commit'
import { syncGoogleCalendarAndPromote } from '../../../lib/booking/calendar-sync'
import { formatSlotLabelLong, parseOptionalInt } from '../../../lib/booking/reserve-helpers'
import { requireAppBaseUrl } from '../../../lib/config/app-url'
import { trimString, isValidEmail, jsonResponse } from '../../../lib/api/helpers'
import { env } from 'cloudflare:workers'

const FALLBACK_EMAIL = 'team@smd.services'

/**
 * POST /api/booking/reserve
 *
 * Atomic 3-phase booking flow:
 *   1. Preflight  — rate limit, input validation
 *   2. DB commit  — Intake + schedule sidecars + hold + token
 *   3. Google sync — Create calendar event; compensating rollback on failure
 *   4. Post-commit — Promote stage, send confirmation email with ICS
 *
 * Google event creation failure = booking failure. No silent fallback.
 *
 * This route gates, validates, and delegates. The commit is
 * `src/lib/booking/commit.ts`, the calendar sync and rollback
 * `src/lib/booking/calendar-sync.ts`, the emails
 * `src/lib/booking/confirmation-emails.ts` (code review 2026-09-10,
 * Architecture 3).
 */

function deriveBusinessNameFromEmail(email: string): string {
  const domain = email.split('@')[1] ?? ''
  return domain || 'Unknown'
}

function validateSlotTiming(slotStartUtc: string): { slotEndUtc: string } | Response {
  const slotStart = new Date(slotStartUtc)
  if (isNaN(slotStart.getTime())) {
    return jsonResponse(400, { error: 'validation_failed', message: 'Invalid slot_start_utc' })
  }
  const earliest = Date.now() + BOOKING_CONFIG.min_notice_minutes * 60_000
  if (slotStart.getTime() < earliest) {
    return jsonResponse(400, {
      error: 'slot_unavailable',
      message: 'This slot is no longer available. Please choose a later time.',
    })
  }
  return {
    slotEndUtc: new Date(slotStart.getTime() + BOOKING_CONFIG.slot_minutes * 60_000).toISOString(),
  }
}

function validateReserveInput(body: Record<string, unknown>): ReserveInput | Response {
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessNameRaw = trimString(body.business_name)
  const phone = trimString(body.phone)
  const slotStartUtc = trimString(body.slot_start_utc)

  if (!name || !email || !slotStartUtc) {
    return jsonResponse(400, {
      error: 'validation_failed',
      message: 'name, email, and slot_start_utc are required',
    })
  }

  if (!isValidEmail(email)) {
    return jsonResponse(400, { error: 'validation_failed', message: 'Invalid email address' })
  }

  // business_name and phone are optional for web intakes. The V3 unified
  // intake form drops both fields in favor of a simpler "email + name +
  // message" shape. When business_name is absent we derive it from the
  // email domain so downstream entity rows and email/calendar templates
  // still have something to render. The admin "Send booking link" flow
  // continues to bypass all of this via prefill_token + PreSeededIntake.
  const businessName = businessNameRaw ?? deriveBusinessNameFromEmail(email)

  const slotTiming = validateSlotTiming(slotStartUtc)
  if (slotTiming instanceof Response) return slotTiming

  return {
    name,
    email,
    businessName,
    phone,
    slotStartUtc,
    slotEndUtc: slotTiming.slotEndUtc,
    website: trimString(body.website) || null,
    userMessage: typeof body.message === 'string' ? body.message.trim() : null,
    vertical: trimString(body.vertical) || null,
    employeeCount: parseOptionalInt(body.employee_count),
    yearsInBusiness: parseOptionalInt(body.years_in_business),
    biggestChallenge: trimString(body.biggest_challenge) || null,
    howHeard: trimString(body.how_heard) || null,
    guestTimezone: trimString(body.timezone) || null,
    prefillTokenRaw: trimString(body.prefill_token),
  }
}

async function resolvePreSeeded(prefillTokenRaw: string | null): Promise<PreSeededIntake | null> {
  if (!prefillTokenRaw) return null
  const verify = await verifyBookingLink(prefillTokenRaw)
  if (verify.ok) {
    return {
      entityId: verify.payload.entity_id,
      assessmentId: verify.payload.assessment_id,
      meetingType: verify.payload.meeting_type,
      contactId: verify.payload.contact_id,
    }
  }
  console.warn(
    `[api/booking/reserve] prefill token rejected: ${verify.error}; falling back to standard flow`
  )
  return null
}

function calendarSyncFailedJson(): Response {
  return jsonResponse(503, {
    error: 'calendar_sync_failed',
    message: 'We could not create the calendar event. Please try again or email us directly.',
    fallback: {
      type: 'email',
      email: FALLBACK_EMAIL,
      message: `Please email ${FALLBACK_EMAIL} to schedule your call.`,
    },
  })
}

function calendarUnavailableJson(): Response {
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

async function handlePost({ request, locals }: APIContext): Promise<Response> {
  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Phase 1a: IP rate limiting
  const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
  const rateLimitResult = await rateLimitByIp(env.BOOKING_CACHE, 'reserve', clientIp)
  if (!rateLimitResult.allowed) {
    return jsonResponse(429, {
      error: 'rate_limited',
      message: 'Too many booking attempts. Please try again later.',
    })
  }

  // Phase 1b: Input validation
  const validated = validateReserveInput(body)
  if (validated instanceof Response) return validated

  // Optional prefill token (admin "Send booking link" flow — #467).
  // Invalid/expired tokens fall back to standard flow silently.
  const preSeeded = await resolvePreSeeded(validated.prefillTokenRaw)

  // Phase 1c: Verify Google integration before doing any DB work
  const integration = await getIntegration(env.DB, ORG_ID, 'google_calendar')
  if (!integration) return calendarUnavailableJson()

  const accessToken = await getGoogleAccessToken(env.DB, integration, env)
  if (!accessToken) return calendarUnavailableJson()

  // Phase 2: DB commit
  const holdResult = await acquireHold(env.DB, ORG_ID, validated.slotStartUtc, validated.email)
  if (!holdResult.acquired) {
    return jsonResponse(409, {
      error: 'slot_taken',
      message: 'This time slot was just taken. Please choose another time.',
    })
  }

  // First-touch ad attribution, set by middleware on the landing request
  // (ADR 0066 gate 1). Server-side read — the client never sends it.
  const attribution = readAttributionFromCookieHeader(request.headers.get('cookie'))

  let dbResult: BookingCommitResult
  try {
    dbResult = await commitBookingToDb({ input: validated, preSeeded, attribution })
  } catch (err) {
    console.error('[api/booking/reserve] DB commit failed:', err)
    await releaseHold(env.DB, holdResult.id!)
    return jsonResponse(500, { error: 'Internal server error' })
  }

  // Build the manage URL once, up front, so we can thread it through the
  // calendar event description (where reschedule needs to be findable from
  // the calendar app, not just the confirmation email).
  let appBaseUrl: string
  try {
    appBaseUrl = requireAppBaseUrl(env)
  } catch {
    appBaseUrl = 'https://smd.services'
  }
  const manageUrl = `${appBaseUrl}/book/manage?token=${dbResult.manageToken}`

  // Phase 3: Google Calendar sync + entity stage promotion
  const calendarId = integration.calendar_id || BOOKING_CONFIG.consultant.calendar_id
  const googleSyncResult = await syncGoogleCalendarAndPromote({
    accessToken,
    calendarId,
    input: validated,
    dbResult,
    holdId: holdResult.id!,
    preSeeded,
    manageUrl,
  })
  if (!googleSyncResult.ok) return calendarSyncFailedJson()
  const googleMeetUrl = googleSyncResult.meetUrl

  // Release the hold — the live assessment row is now the lock
  await releaseHold(env.DB, holdResult.id!)

  // Phase 4: confirmation emails + conversion event + 201 response
  return finalizeBooking({ request, locals, validated, dbResult, googleMeetUrl, manageUrl })
}

interface FinalizeBookingArgs {
  request: Request
  locals: APIContext['locals']
  validated: ReserveInput
  dbResult: BookingCommitResult
  googleMeetUrl: string
  manageUrl: string
}

async function finalizeBooking(args: FinalizeBookingArgs): Promise<Response> {
  const { request, locals, validated, dbResult, googleMeetUrl, manageUrl } = args
  const { slotStartUtc, slotEndUtc, guestTimezone } = validated
  const { assessmentId, meetingId, scheduleId, meetingScheduleId } = dbResult
  const displayTz = guestTimezone || BOOKING_CONFIG.consultant.timezone

  // Confirmation emails (best-effort)
  await sendConfirmationEmails({ input: validated, dbResult, googleMeetUrl, manageUrl })

  // Meta CAPI Schedule event (ADR 0066 gate 2, #1723) — server half of the
  // dedup pair; browser fires the same event_name with this eventID.
  // Fail-closed no-op until the pixel/token are configured.
  const metaEventId = mintMetaEventId()
  await emitMetaEvent(
    env,
    import.meta.env.PUBLIC_META_PIXEL_ID,
    { eventName: 'Schedule', eventId: metaEventId, request, email: validated.email },
    locals.cfContext ? (p) => locals.cfContext!.waitUntil(p) : undefined
  )

  return jsonResponse(201, {
    meta_event_id: metaEventId,
    ok: true,
    // assessment_id and meeting_id are equal by construction during the
    // monitoring window — callers can use either. New code should prefer meeting_id.
    assessment_id: assessmentId,
    meeting_id: meetingId,
    schedule_id: scheduleId,
    meeting_schedule_id: meetingScheduleId,
    slot_start_utc: slotStartUtc,
    slot_end_utc: slotEndUtc,
    slot_label: formatSlotLabelLong(slotStartUtc, displayTz),
    meet_url: googleMeetUrl,
    manage_url: manageUrl,
  })
}

export const POST: APIRoute = (ctx) => handlePost(ctx)

// ---------------------------------------------------------------------------
