import type { APIRoute } from 'astro'
import { ORG_ID } from '../../../lib/constants'
import { BOOKING_CONFIG } from '../../../lib/booking/config'
import { verifyTurnstileToken } from '../../../lib/booking/turnstile'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { acquireHold, releaseHold } from '../../../lib/booking/holds'
import {
  generateManageToken,
  hashManageToken,
  computeManageTokenExpiry,
} from '../../../lib/booking/tokens'
import { buildIcs, icsToBase64 } from '../../../lib/booking/ics'
import { processIntakeSubmission } from '../../../lib/booking/intake-core'
import { createScheduleStatement, updateScheduleGoogleSync } from '../../../lib/booking/schedule'
import { getIntegration, getGoogleAccessToken } from '../../../lib/db/integrations'
import { transitionStage } from '../../../lib/db/entities'
import { formatInTimeZone } from 'date-fns-tz'
import { sendEmail } from '../../../lib/email/resend'
import {
  bookingConfirmationEmailHtml,
  bookingAdminNotificationEmailHtml,
} from '../../../lib/email/templates'
import { requireAppBaseUrl } from '../../../lib/config/app-url'

const FALLBACK_EMAIL = 'scott@smd.services'
const NOTIFY_EMAIL = 'team@smd.services'

/**
 * POST /api/booking/reserve
 *
 * Atomic 3-phase booking flow:
 *   1. Preflight  — Turnstile, rate limit, input validation
 *   2. DB commit  — Intake (entity + contact + assessment) + schedule + hold + token
 *   3. Google sync — Create calendar event; compensating rollback on failure
 *   4. Post-commit — Send confirmation email with ICS
 *
 * Google event creation failure = booking failure. No silent fallback.
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const env = locals.runtime.env

  // -----------------------------------------------------------------------
  // Parse body
  // -----------------------------------------------------------------------
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // -----------------------------------------------------------------------
  // Phase 1: Preflight
  // -----------------------------------------------------------------------

  // 1a. Turnstile verification
  const turnstileToken = typeof body.turnstile_token === 'string' ? body.turnstile_token : null
  const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
  const turnstileResult = await verifyTurnstileToken(
    env.TURNSTILE_SECRET_KEY,
    turnstileToken,
    clientIp
  )
  if (!turnstileResult.success) {
    return jsonResponse(403, { error: 'turnstile_failed', message: turnstileResult.error })
  }

  // 1b. IP rate limiting (10/hour)
  const rateLimitResult = await rateLimitByIp(env.BOOKING_CACHE, 'reserve', clientIp)
  if (!rateLimitResult.allowed) {
    return jsonResponse(429, {
      error: 'rate_limited',
      message: 'Too many booking attempts. Please try again later.',
    })
  }

  // 1c. Input validation
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)
  const slotStartUtc = trimString(body.slot_start_utc)

  if (!name || !email || !businessName || !slotStartUtc) {
    return jsonResponse(400, {
      error: 'validation_failed',
      message: 'name, email, business_name, and slot_start_utc are required',
    })
  }

  if (!isValidEmail(email)) {
    return jsonResponse(400, { error: 'validation_failed', message: 'Invalid email address' })
  }

  // Validate slot_start_utc is a valid ISO date
  const slotStart = new Date(slotStartUtc)
  if (isNaN(slotStart.getTime())) {
    return jsonResponse(400, { error: 'validation_failed', message: 'Invalid slot_start_utc' })
  }

  // Slot must be in the future (with min notice)
  const now = new Date()
  const earliest = now.getTime() + BOOKING_CONFIG.min_notice_minutes * 60_000
  if (slotStart.getTime() < earliest) {
    return jsonResponse(400, {
      error: 'slot_unavailable',
      message: 'This slot is no longer available. Please choose a later time.',
    })
  }

  // Compute slot end
  const slotEndUtc = new Date(
    slotStart.getTime() + BOOKING_CONFIG.slot_minutes * 60_000
  ).toISOString()

  // Optional intake fields
  const vertical = trimString(body.vertical) || null
  const employeeCount = parseOptionalInt(body.employee_count)
  const yearsInBusiness = parseOptionalInt(body.years_in_business)
  const biggestChallenge = trimString(body.biggest_challenge) || null
  const howHeard = trimString(body.how_heard) || null
  const guestTimezone = trimString(body.timezone) || null

  // -----------------------------------------------------------------------
  // Phase 1d: Verify Google integration before doing any DB work
  // -----------------------------------------------------------------------
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

  // -----------------------------------------------------------------------
  // Phase 2: DB commit (hold + intake + schedule + token)
  // -----------------------------------------------------------------------

  // 2a. Acquire pessimistic hold on the slot
  const holdResult = await acquireHold(env.DB, ORG_ID, slotStartUtc, email)
  if (!holdResult.acquired) {
    return jsonResponse(409, {
      error: 'slot_taken',
      message: 'This time slot was just taken. Please choose another time.',
    })
  }

  let assessmentId: string
  let entityId: string
  let scheduleId: string
  let manageToken: string
  let intakeLines: string[]

  try {
    // 2b. Process intake (entity + contact + assessment)
    const intakeResult = await processIntakeSubmission(
      env.DB,
      ORG_ID,
      {
        name,
        email,
        businessName,
        vertical,
        employeeCount,
        yearsInBusiness,
        biggestChallenge,
        howHeard,
      },
      slotStartUtc
    )

    assessmentId = intakeResult.assessmentId
    entityId = intakeResult.entityId
    intakeLines = intakeResult.intakeLines

    // 2c. Generate manage token + hash
    manageToken = generateManageToken()
    const manageTokenHash = await hashManageToken(manageToken)
    const manageTokenExpiresAt = computeManageTokenExpiry(
      slotEndUtc,
      BOOKING_CONFIG.manage_token_ttl_hours_after_slot
    )

    // 2d. Create assessment_schedule sidecar
    const { statement: scheduleStmt, id: newScheduleId } = createScheduleStatement(env.DB, {
      assessmentId,
      orgId: ORG_ID,
      slotStartUtc,
      slotEndUtc,
      durationMinutes: BOOKING_CONFIG.slot_minutes,
      timezone: BOOKING_CONFIG.consultant.timezone,
      guestTimezone: guestTimezone,
      guestName: name,
      guestEmail: email,
      manageTokenHash,
      manageTokenExpiresAt,
    })
    scheduleId = newScheduleId

    await scheduleStmt.run()

    // 2e. Promote entity stage to 'assessing' (only if currently 'prospect')
    try {
      await transitionStage(
        env.DB,
        ORG_ID,
        entityId,
        'assessing',
        'Booking reserve: assessment scheduled'
      )
    } catch {
      // Stage transition may fail if entity is already past prospect — that's fine
    }
  } catch (err) {
    // DB commit failed — release hold and return error
    console.error('[api/booking/reserve] DB commit failed:', err)
    await releaseHold(env.DB, holdResult.id!)
    return jsonResponse(500, { error: 'Internal server error' })
  }

  // -----------------------------------------------------------------------
  // Phase 3: Google Calendar sync
  // -----------------------------------------------------------------------
  const calendarId = integration.calendar_id || BOOKING_CONFIG.consultant.calendar_id
  let googleEventId: string | null = null
  let googleEventLink: string | null = null
  let googleMeetUrl: string | null = null

  try {
    const slotLabel = formatSlotLabelLong(slotStartUtc, BOOKING_CONFIG.consultant.timezone)

    const eventResult = await createGoogleCalendarEvent(accessToken, calendarId, {
      summary: `Assessment: ${businessName} (${name})`,
      description: buildEventDescription(name, email, businessName, intakeLines),
      startUtc: slotStartUtc,
      endUtc: slotEndUtc,
      guestEmail: email,
      assessmentId,
    })

    googleEventId = eventResult.eventId
    googleEventLink = eventResult.htmlLink
    googleMeetUrl = eventResult.meetUrl

    // Update schedule with Google sync data
    await updateScheduleGoogleSync(env.DB, scheduleId, {
      googleEventId: eventResult.eventId,
      googleEventLink: eventResult.htmlLink,
      googleMeetUrl: eventResult.meetUrl,
    })
  } catch (err) {
    // Google sync failed — compensating rollback
    console.error('[api/booking/reserve] Google Calendar event creation failed:', err)

    try {
      // Delete assessment_schedule, assessment, and hold
      await env.DB.batch([
        env.DB.prepare('DELETE FROM assessment_schedule WHERE id = ?').bind(scheduleId),
        env.DB.prepare('DELETE FROM assessments WHERE id = ? AND org_id = ?').bind(
          assessmentId,
          ORG_ID
        ),
        env.DB.prepare('DELETE FROM booking_holds WHERE id = ?').bind(holdResult.id!),
      ])
    } catch (rollbackErr) {
      console.error('[api/booking/reserve] Rollback failed:', rollbackErr)
    }

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

  // Release the hold — the live assessment row is now the lock
  await releaseHold(env.DB, holdResult.id!)

  // -----------------------------------------------------------------------
  // Phase 4: Post-commit (confirmation email + admin notification)
  // -----------------------------------------------------------------------
  const displayTz = guestTimezone || BOOKING_CONFIG.consultant.timezone
  const slotLabel = formatSlotLabelLong(slotStartUtc, displayTz)

  let appBaseUrl: string
  try {
    appBaseUrl = requireAppBaseUrl(env)
  } catch {
    appBaseUrl = 'https://smd.services'
  }
  const manageUrl = `${appBaseUrl}/book/manage?token=${manageToken}`

  // Build ICS attachment
  let icsAttachment: { filename: string; content: string; content_type: string } | null = null
  try {
    const icsResult = buildIcs({
      scheduleId,
      sequence: 0,
      method: 'REQUEST',
      startUtc: slotStartUtc,
      durationMinutes: BOOKING_CONFIG.slot_minutes,
      title: `${BOOKING_CONFIG.meeting_label} — SMD Services`,
      description: `Assessment call with SMD Services for ${businessName}.\n\nManage your booking: ${manageUrl}`,
      location: googleMeetUrl ?? undefined,
      organizerName: BOOKING_CONFIG.consultant.name,
      organizerEmail: BOOKING_CONFIG.consultant.email,
      guestName: name,
      guestEmail: email,
    })
    icsAttachment = {
      filename: 'invite.ics',
      content: icsToBase64(icsResult.ics),
      content_type: icsResult.contentType,
    }
  } catch (icsErr) {
    console.error('[api/booking/reserve] ICS generation failed:', icsErr)
    // ICS failure doesn't block the booking — the event is already in Google Calendar
  }

  // Send confirmation email to guest (fire and forget)
  try {
    const confirmationHtml = bookingConfirmationEmailHtml({
      guestName: name,
      businessName,
      slotLabel,
      meetUrl: googleMeetUrl,
      manageUrl,
      meetingLabel: BOOKING_CONFIG.meeting_label,
    })

    await sendEmail(env.RESEND_API_KEY, {
      to: email,
      subject: `Confirmed: ${BOOKING_CONFIG.meeting_label} with SMD Services`,
      html: confirmationHtml,
      ...(icsAttachment ? { attachments: [icsAttachment] } : {}),
    })
  } catch (emailErr) {
    console.error('[api/booking/reserve] Confirmation email failed:', emailErr)
  }

  // Send admin notification (fire and forget)
  try {
    const adminHtml = bookingAdminNotificationEmailHtml({
      guestName: name,
      guestEmail: email,
      businessName,
      slotLabel: formatSlotLabelLong(slotStartUtc, BOOKING_CONFIG.consultant.timezone),
      intakeLines,
      entityAdminUrl: `${appBaseUrl}/admin/entities/${entityId}`,
    })

    await sendEmail(env.RESEND_API_KEY, {
      to: NOTIFY_EMAIL,
      reply_to: email,
      subject: `New booking: ${businessName} — ${formatSlotLabelLong(slotStartUtc, BOOKING_CONFIG.consultant.timezone)}`,
      html: adminHtml,
    })
  } catch (emailErr) {
    console.error('[api/booking/reserve] Admin notification email failed:', emailErr)
  }

  // -----------------------------------------------------------------------
  // Response
  // -----------------------------------------------------------------------
  return jsonResponse(201, {
    ok: true,
    assessment_id: assessmentId,
    schedule_id: scheduleId,
    slot_start_utc: slotStartUtc,
    slot_end_utc: slotEndUtc,
    slot_label: slotLabel,
    meet_url: googleMeetUrl,
    manage_url: manageUrl,
  })
}

// ---------------------------------------------------------------------------
// Google Calendar event creation
// ---------------------------------------------------------------------------

interface CreateEventResult {
  eventId: string
  htmlLink: string | null
  meetUrl: string | null
}

async function createGoogleCalendarEvent(
  accessToken: string,
  calendarId: string,
  params: {
    summary: string
    description: string
    startUtc: string
    endUtc: string
    guestEmail: string
    assessmentId: string
  }
): Promise<CreateEventResult> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), BOOKING_CONFIG.google_call_timeout_ms)

  try {
    const response = await fetch(
      `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events?conferenceDataVersion=1&sendUpdates=all`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          summary: params.summary,
          description: params.description,
          start: { dateTime: params.startUtc, timeZone: 'UTC' },
          end: { dateTime: params.endUtc, timeZone: 'UTC' },
          attendees: [{ email: params.guestEmail }],
          conferenceData: {
            createRequest: {
              requestId: params.assessmentId,
              conferenceSolutionKey: { type: 'hangoutsMeet' },
            },
          },
          extendedProperties: {
            private: {
              assessmentId: params.assessmentId,
            },
          },
          reminders: {
            useDefault: false,
            overrides: [
              { method: 'email', minutes: 60 },
              { method: 'popup', minutes: 15 },
            ],
          },
        }),
        signal: controller.signal,
      }
    )

    if (!response.ok) {
      const body = await response.text()
      throw new Error(`Google Calendar API ${response.status}: ${body}`)
    }

    const data = (await response.json()) as {
      id: string
      htmlLink?: string
      hangoutLink?: string
      conferenceData?: {
        entryPoints?: Array<{ entryPointType: string; uri: string }>
      }
    }

    // Extract Meet URL from conferenceData or hangoutLink
    let meetUrl: string | null = data.hangoutLink ?? null
    if (!meetUrl && data.conferenceData?.entryPoints) {
      const videoEntry = data.conferenceData.entryPoints.find((ep) => ep.entryPointType === 'video')
      if (videoEntry) meetUrl = videoEntry.uri
    }

    return {
      eventId: data.id,
      htmlLink: data.htmlLink ?? null,
      meetUrl,
    }
  } finally {
    clearTimeout(timeout)
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildEventDescription(
  name: string,
  email: string,
  businessName: string,
  intakeLines: string[]
): string {
  const lines = [`Guest: ${name} <${email}>`, `Business: ${businessName}`]
  if (intakeLines.length > 0) {
    lines.push('', '--- Intake ---', ...intakeLines)
  }
  return lines.join('\n')
}

function formatSlotLabelLong(slotStartUtc: string, tz: string): string {
  return formatInTimeZone(new Date(slotStartUtc), tz, "EEEE, MMMM d 'at' h:mm a (zzz)")
}

function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function parseOptionalInt(value: unknown): number | null {
  if (typeof value === 'number') return Math.floor(value)
  if (typeof value === 'string') {
    const parsed = parseInt(value, 10)
    return isNaN(parsed) ? null : parsed
  }
  return null
}

function isValidEmail(email: string): boolean {
  if (email.length > 254) return false
  const parts = email.split('@')
  if (parts.length !== 2) return false
  const [local, domain] = parts
  if (!local || !domain) return false
  if (domain.indexOf('.') === -1) return false
  return true
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
