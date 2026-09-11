import { jsonResponse, errorResponse } from '../../../../../lib/api/helpers'
import type { APIContext, APIRoute } from 'astro'
import { getEntity, transitionStage } from '../../../../../lib/db/entities'
import { createMeetingWithLegacyAssessment } from '../../../../../lib/db/meetings'
import { listContacts } from '../../../../../lib/db/contacts'
import { appendContext } from '../../../../../lib/db/context'
import {
  signBookingLink,
  DEFAULT_BOOKING_LINK_TTL_DAYS,
} from '../../../../../lib/booking/signed-link'
import { BOOKING_CONFIG } from '../../../../../lib/booking/config'
import { requireAppBaseUrl } from '../../../../../lib/config/app-url'
import { sendOutreachEmail } from '../../../../../lib/email/resend'
import { bookingLinkInviteEmailHtml } from '../../../../../lib/email/templates'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

/**
 * POST /api/admin/entities/[id]/send-booking-link
 *
 * Replaces the old "Book Assessment" stage-transition button with an action
 * that actually matches its label (#467).
 *
 * Flow:
 *   1. Create a canonical meeting row in `scheduled` status with no
 *      `scheduled_at` yet, and seed the legacy assessment mirror row with the
 *      same primary key during the monitoring window.
 *   2. Transition the entity `prospect → meetings`.
 *   3. Sign a booking-link token that carries the entity_id, contact_id,
 *      assessment_id, meeting_type, and admin-chosen duration. TTL defaults
 *      to 14 days.
 *   4. Send the booking-link email server-side via `sendOutreachEmail` so
 *      the send is recorded in `outreach_events` (entity-attributed) and
 *      Resend's tracking pixel + link rewrites attribute downstream
 *      open/click/bounce/reply events back to this entity (#587 path).
 *      Skipped when the entity has no contact email — the admin still gets
 *      a copy-paste template + mailto fallback.
 *   5. Append a context entry noting the link was sent, with the same
 *      outreach template the admin gets back in the response.
 *   6. Return JSON with the signed URL, send status, and outreach template
 *      so the admin UI can copy-to-clipboard or open mailto when the
 *      server-side send was skipped.
 *
 * Response is JSON (not a redirect) because the admin UI reflects send
 * status inline rather than navigating away.
 */

// ---------------------------------------------------------------------------
// Helpers (declared first so handler can reference them)
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
  const rawStr = typeof raw === 'string' ? raw : typeof raw === 'number' ? String(raw) : null
  const num = typeof raw === 'number' ? raw : rawStr != null ? parseInt(rawStr, 10) : NaN
  if (!Number.isFinite(num) || num <= 0) return def
  return ALLOWED_DURATIONS.includes(num) ? num : def
}

function parseBoolean(raw: unknown, defaultValue: boolean): boolean {
  if (raw === undefined || raw === null) return defaultValue
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'number') return raw !== 0
  if (typeof raw === 'string') {
    const v = raw.trim().toLowerCase()
    if (v === 'true' || v === '1' || v === 'yes' || v === 'on') return true
    if (v === 'false' || v === '0' || v === 'no' || v === 'off' || v === '') return false
  }
  return defaultValue
}

function buildSubject(businessName: string): string {
  return `Quick call about ${businessName}`
}

/**
 * Plain-text outreach body. Used both as the synced clipboard payload (when
 * the admin chooses copy-paste rather than server-side send) and as the
 * stored context entry.
 *
 * Voice: "we" only. No consultant names. No response-time promises. No
 * uncontracted next-step language. CLAUDE.md "no fabricated client content"
 * rule, Pattern A.
 *
 * The signature is the brand name, never an individual — even if a future
 * field stores a consultant name, surfacing it here would imply ownership
 * of a single-person reply that we have not contracted to provide.
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
    `Following up on ${params.businessName}. When it works for you, pick a time for a quick call so we can learn how things run and where you're trying to go.`,
    '',
    params.bookingUrl,
    '',
    '— SMD Services',
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

async function parseRequestBody(request: Request): Promise<Record<string, unknown> | Response> {
  const contentType = request.headers.get('content-type') ?? ''
  try {
    if (contentType.includes('application/json')) {
      return await request.json()
    }
    const fd = await request.formData()
    const result: Record<string, unknown> = {}
    for (const [k, v] of fd.entries()) result[k] = v
    return result
  } catch {
    return errorResponse(400, 'invalid_body')
  }
}

type EmailStatus = 'sent' | 'skipped_no_recipient' | 'skipped_by_caller' | 'send_failed'

interface EmailResult {
  emailStatus: EmailStatus
  messageId: string | null
  outreachEventId: string | null
  sendError: string | null
}

interface ProvisionResult {
  meeting: { id: string }
  token: string
  bookingUrl: string
}

interface ProvisionArgs {
  orgId: string
  entityId: string
  contactId: string | null
  meetingType: string | null
  duration: number
}

async function provisionMeetingAndToken(args: ProvisionArgs): Promise<ProvisionResult | Response> {
  const { orgId, entityId, contactId, meetingType, duration } = args

  const meeting = await createMeetingWithLegacyAssessment(env.DB, orgId, entityId, {
    scheduled_at: null,
    meeting_type: meetingType,
  })

  try {
    await transitionStage(env.DB, orgId, entityId, 'meetings', 'Booking link sent to prospect.')
  } catch (err) {
    console.error('[api/admin/entities/send-booking-link] stage transition failed:', err)
    return errorResponse(500, 'stage_transition_failed', undefined, {
      message: err instanceof Error ? err.message : 'Stage transition failed.',
    })
  }

  let token: string
  try {
    token = await signBookingLink({
      entity_id: entityId,
      contact_id: contactId,
      assessment_id: meeting.id,
      duration_minutes: duration,
      meeting_type: meetingType,
    })
  } catch (err) {
    console.error('[api/admin/entities/send-booking-link] signing failed:', err)
    return errorResponse(500, 'signing_failed', 'Server is not configured to issue booking links.')
  }

  let appBaseUrl: string
  try {
    appBaseUrl = requireAppBaseUrl(env)
  } catch {
    appBaseUrl = ''
  }

  return { meeting, token, bookingUrl: `${appBaseUrl}/book?t=${encodeURIComponent(token)}` }
}

interface AppendLinkContextArgs {
  orgId: string
  entityId: string
  meetingId: string
  duration: number
  meetingType: string | null
  outreachTemplate: string
  emailResult: EmailResult
  contactEmail: string | null
}

async function appendLinkContext(args: AppendLinkContextArgs): Promise<void> {
  const {
    orgId,
    entityId,
    meetingId,
    duration,
    meetingType,
    outreachTemplate,
    emailResult,
    contactEmail,
  } = args
  await appendContext(env.DB, orgId, {
    entity_id: entityId,
    type: 'outreach_draft',
    content: outreachTemplate,
    source: 'send_booking_link',
    metadata: {
      trigger: 'send_booking_link',
      assessment_id: meetingId,
      meeting_id: meetingId,
      duration_minutes: duration,
      meeting_type: meetingType,
      token_ttl_days: DEFAULT_BOOKING_LINK_TTL_DAYS,
      email_status: emailResult.emailStatus,
      recipient_email: contactEmail,
      message_id: emailResult.messageId,
      outreach_event_id: emailResult.outreachEventId,
      send_error: emailResult.sendError,
    },
  })
}

interface EntityContactResult {
  entity: { id: string; name: string; stage: string }
  contactEmail: string | null
  contactName: string | null
  contactId: string | null
}

async function resolveEntityAndContacts(
  orgId: string,
  entityId: string
): Promise<EntityContactResult | Response> {
  const entity = await getEntity(env.DB, orgId, entityId)
  if (!entity) return errorResponse(404, 'entity_not_found')
  if (entity.stage !== 'prospect') {
    return errorResponse(
      409,
      'invalid_stage',
      `Entity must be in the 'prospect' stage; current stage is '${entity.stage}'.`
    )
  }
  const contacts = await listContacts(env.DB, orgId, entityId)
  const primaryContact = contacts.find((c) => c.email) ?? contacts[0] ?? null
  return {
    entity,
    contactEmail: primaryContact?.email ?? null,
    contactName: primaryContact?.name ?? null,
    contactId: primaryContact?.id ?? null,
  }
}

async function maybeSendEmail(params: {
  sendEmailFlag: boolean
  contactEmail: string | null
  contactName: string | null
  businessName: string
  bookingUrl: string
  subject: string
  orgId: string
  entityId: string
}): Promise<EmailResult> {
  const {
    sendEmailFlag,
    contactEmail,
    contactName,
    businessName,
    bookingUrl,
    subject,
    orgId,
    entityId,
  } = params

  if (!sendEmailFlag) {
    return {
      emailStatus: 'skipped_by_caller',
      messageId: null,
      outreachEventId: null,
      sendError: null,
    }
  }
  if (!contactEmail) {
    return {
      emailStatus: 'skipped_no_recipient',
      messageId: null,
      outreachEventId: null,
      sendError: null,
    }
  }

  const html = bookingLinkInviteEmailHtml({ contactName, businessName, bookingUrl })
  try {
    const sendResult = await sendOutreachEmail(
      env.RESEND_API_KEY,
      { to: contactEmail, subject, html },
      { db: env.DB, orgId, entityId }
    )
    if (sendResult.success) {
      return {
        emailStatus: 'sent',
        messageId: sendResult.id ?? null,
        outreachEventId: sendResult.outreach_event_id ?? null,
        sendError: null,
      }
    }
    console.error('[api/admin/entities/send-booking-link] email send failed:', sendResult.error)
    return {
      emailStatus: 'send_failed',
      messageId: null,
      outreachEventId: null,
      sendError: sendResult.error ?? 'unknown_send_error',
    }
  } catch (err) {
    console.error('[api/admin/entities/send-booking-link] email send threw:', err)
    return {
      emailStatus: 'send_failed',
      messageId: null,
      outreachEventId: null,
      sendError: err instanceof Error ? err.message : 'send_threw',
    }
  }
}

function parseMeetingType(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  return trimmed.length > 0 ? trimmed.slice(0, 100) : null
}

async function handlePost({ params, request, locals }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth
  const entityId = params.id
  if (!entityId) return errorResponse(400, 'missing_entity_id')

  const bodyOrError = await parseRequestBody(request)
  if (bodyOrError instanceof Response) return bodyOrError

  const duration = parseDuration(bodyOrError.duration_minutes)
  const meetingType = parseMeetingType(bodyOrError.meeting_type)
  const sendEmailFlag = parseBoolean(bodyOrError.send_email, true)

  try {
    const resolved = await resolveEntityAndContacts(session.orgId, entityId)
    if (resolved instanceof Response) return resolved
    const { entity, contactEmail, contactName, contactId } = resolved

    const provisionResult = await provisionMeetingAndToken({
      orgId: session.orgId,
      entityId,
      contactId,
      meetingType,
      duration,
    })
    if (provisionResult instanceof Response) return provisionResult
    const { meeting, bookingUrl } = provisionResult

    const subject = buildSubject(entity.name)
    const outreachTemplate = buildOutreachTemplate({
      contactName,
      businessName: entity.name,
      bookingUrl,
    })
    const emailResult = await maybeSendEmail({
      sendEmailFlag,
      contactEmail,
      contactName,
      businessName: entity.name,
      bookingUrl,
      subject,
      orgId: session.orgId,
      entityId,
    })

    await appendLinkContext({
      orgId: session.orgId,
      entityId,
      meetingId: meeting.id,
      duration,
      meetingType,
      outreachTemplate,
      emailResult,
      contactEmail,
    })

    return jsonResponse(200, {
      ok: true,
      assessment_id: meeting.id,
      meeting_id: meeting.id,
      booking_url: bookingUrl,
      token_ttl_days: DEFAULT_BOOKING_LINK_TTL_DAYS,
      contact_email: contactEmail,
      outreach_template: outreachTemplate,
      mailto_url: buildMailtoUrl({ to: contactEmail, subject, body: outreachTemplate }),
      email_status: emailResult.emailStatus,
      message_id: emailResult.messageId,
      outreach_event_id: emailResult.outreachEventId,
      send_error: emailResult.sendError,
    })
  } catch (err) {
    console.error('[api/admin/entities/send-booking-link] Error:', err)
    return errorResponse(500, 'internal_error', undefined, {
      message: err instanceof Error ? err.message : 'server',
    })
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
