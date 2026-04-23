import type { APIRoute } from 'astro'
import { getEntity, transitionStage } from '../../../../../lib/db/entities'
import { createAssessment } from '../../../../../lib/db/assessments'
import { listContacts } from '../../../../../lib/db/contacts'
import { appendContext } from '../../../../../lib/db/context'
import {
  signBookingLink,
  DEFAULT_BOOKING_LINK_TTL_DAYS,
} from '../../../../../lib/booking/signed-link'
import { BOOKING_CONFIG } from '../../../../../lib/booking/config'
import { requireAppBaseUrl } from '../../../../../lib/config/app-url'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/send-booking-link
 *
 * Replaces the old "Book Assessment" stage-transition button with an action
 * that actually matches its label (#467).
 *
 * Flow:
 *   1. Create an assessment row in `scheduled` status with no `scheduled_at`
 *      yet — the schedule sidecar row is added by `/api/booking/reserve` when
 *      the prospect actually picks a slot.
 *   2. Transition the entity `prospect → assessing` (stage rename to
 *      `meetings` is tracked separately in #466 and will be adopted then).
 *   3. Sign a booking-link token that carries the entity_id, contact_id,
 *      assessment_id, and admin-chosen duration. TTL defaults to 14 days.
 *   4. Append a context entry noting the link was sent, with a copyable
 *      outreach template and mailto URL.
 *   5. Return JSON with the signed URL and outreach template so the admin
 *      UI can copy-to-clipboard and open the mail client.
 *
 * Response is JSON (not a redirect) because the admin UI drives the copy
 * and mailto steps client-side.
 */
export const POST: APIRoute = async ({ params, request, locals }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return jsonResponse(401, { error: 'Unauthorized' })
  }

  const entityId = params.id
  if (!entityId) {
    return jsonResponse(400, { error: 'missing_entity_id' })
  }

  // --- Parse form data / json body -----------------------------------------
  let bodyData: Record<string, unknown> = {}
  const contentType = request.headers.get('content-type') ?? ''
  try {
    if (contentType.includes('application/json')) {
      bodyData = (await request.json()) as Record<string, unknown>
    } else {
      const fd = await request.formData()
      for (const [k, v] of fd.entries()) bodyData[k] = v
    }
  } catch {
    return jsonResponse(400, { error: 'invalid_body' })
  }

  const duration = parseDuration(bodyData.duration_minutes)
  const meetingTypeRaw = typeof bodyData.meeting_type === 'string' ? bodyData.meeting_type : null
  const meetingType =
    meetingTypeRaw && meetingTypeRaw.trim().length > 0 ? meetingTypeRaw.trim().slice(0, 100) : null

  try {
    // --- Load entity --------------------------------------------------------
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return jsonResponse(404, { error: 'entity_not_found' })
    }

    // Only issue booking links from the prospect stage. This mirrors the
    // previous Book Assessment button's reachability.
    if (entity.stage !== 'prospect') {
      return jsonResponse(409, {
        error: 'invalid_stage',
        message: `Entity must be in the 'prospect' stage; current stage is '${entity.stage}'.`,
      })
    }

    // --- Primary contact (for email prefill) --------------------------------
    const contacts = await listContacts(env.DB, session.orgId, entityId)
    const primaryContact = contacts.find((c) => c.email) ?? contacts[0] ?? null
    const contactEmail = primaryContact?.email ?? null
    const contactName = primaryContact?.name ?? null

    // --- 1. Create the assessment row in scheduled status -------------------
    //
    // scheduled_at stays null until the prospect picks a slot via the public
    // booking flow. The create helper sets status = 'scheduled' by default.
    const assessment = await createAssessment(env.DB, session.orgId, entityId, {
      scheduled_at: null,
    })

    // --- 2. Transition entity stage prospect → assessing --------------------
    //
    // We do this AFTER creating the assessment row so the acceptance-criteria
    // invariant (stage transitions only after the meeting row exists) holds
    // even if the caller retries. If the stage transition fails we leave the
    // orphan `scheduled` assessment in place — it is harmless and a subsequent
    // retry will pick it up.
    try {
      await transitionStage(
        env.DB,
        session.orgId,
        entityId,
        'assessing',
        'Booking link sent to prospect.'
      )
    } catch (err) {
      console.error('[api/admin/entities/send-booking-link] stage transition failed:', err)
      return jsonResponse(500, {
        error: 'stage_transition_failed',
        message: err instanceof Error ? err.message : 'Stage transition failed.',
      })
    }

    // --- 3. Sign the booking token -----------------------------------------
    let token: string
    try {
      token = await signBookingLink({
        entity_id: entityId,
        contact_id: primaryContact?.id ?? null,
        assessment_id: assessment.id,
        duration_minutes: duration,
        meeting_type: meetingType,
      })
    } catch (err) {
      console.error('[api/admin/entities/send-booking-link] signing failed:', err)
      return jsonResponse(500, {
        error: 'signing_failed',
        message: 'Server is not configured to issue booking links.',
      })
    }

    // --- Build the booking URL ---------------------------------------------
    let appBaseUrl: string
    try {
      appBaseUrl = requireAppBaseUrl(env)
    } catch {
      // In dev without APP_BASE_URL set, fall back to a relative path so the
      // URL at least opens correctly when tested from the same host.
      appBaseUrl = ''
    }
    const bookingUrl = `${appBaseUrl}/book?t=${encodeURIComponent(token)}`

    // --- 4. Append context entry with the outreach template ----------------
    const outreachTemplate = buildOutreachTemplate({
      contactName,
      businessName: entity.name,
      bookingUrl,
    })

    await appendContext(env.DB, session.orgId, {
      entity_id: entityId,
      type: 'outreach_draft',
      content: outreachTemplate,
      source: 'send_booking_link',
      metadata: {
        trigger: 'send_booking_link',
        assessment_id: assessment.id,
        duration_minutes: duration,
        meeting_type: meetingType,
        token_ttl_days: DEFAULT_BOOKING_LINK_TTL_DAYS,
      },
    })

    // --- 5. Return JSON for the client -------------------------------------
    const mailtoUrl = buildMailtoUrl({
      to: contactEmail,
      subject: `Let's set up a call — ${entity.name}`,
      body: outreachTemplate,
    })

    return jsonResponse(200, {
      ok: true,
      assessment_id: assessment.id,
      booking_url: bookingUrl,
      token_ttl_days: DEFAULT_BOOKING_LINK_TTL_DAYS,
      contact_email: contactEmail,
      outreach_template: outreachTemplate,
      mailto_url: mailtoUrl,
    })
  } catch (err) {
    console.error('[api/admin/entities/send-booking-link] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return jsonResponse(500, { error: 'server', message })
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Only durations that the availability engine currently supports are allowed.
 * The engine uses a single global `slot_minutes` today (see booking/config.ts),
 * so the only sensible UI choice is the configured slot length. If we later
 * add multi-duration support, expand this list.
 */
const ALLOWED_DURATIONS = Array.from(new Set([BOOKING_CONFIG.slot_minutes, 30, 45, 60]))

function parseDuration(raw: unknown): number {
  const def = BOOKING_CONFIG.slot_minutes
  if (raw == null) return def
  const num = typeof raw === 'number' ? raw : parseInt(String(raw), 10)
  if (!Number.isFinite(num) || num <= 0) return def
  return ALLOWED_DURATIONS.includes(num) ? num : def
}

/**
 * Outreach template text. Kept deliberately free of specific timeframes,
 * scope language, or business promises (CLAUDE.md "no fabricated client
 * content" rule). The admin is expected to edit before sending.
 */
function buildOutreachTemplate(params: {
  contactName: string | null
  businessName: string
  bookingUrl: string
}): string {
  const greeting = params.contactName ? `Hi ${params.contactName},` : 'Hi,'
  const lines = [
    greeting,
    '',
    `Following up on ${params.businessName}. When you have time, pick a slot that works for a quick intro call:`,
    '',
    params.bookingUrl,
    '',
    'Looking forward to talking.',
    '',
    'Scott',
  ]
  return lines.join('\n')
}

function buildMailtoUrl(params: { to: string | null; subject: string; body: string }): string {
  const address = params.to ?? ''
  const query = new URLSearchParams({
    subject: params.subject,
    body: params.body,
  })
  return `mailto:${encodeURIComponent(address)}?${query.toString()}`
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
