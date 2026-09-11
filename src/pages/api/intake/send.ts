import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { ORG_ID } from '../../../lib/constants'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { processIntakeSubmission } from '../../../lib/booking/intake-core'
import { ALLOWED_INTERESTS, interestLabel } from '../../../lib/booking/config'
import {
  trimString,
  isValidEmail,
  escapeHtml,
  jsonResponse,
  errorResponse,
} from '../../../lib/api/helpers'
import { sendEmail } from '../../../lib/email/resend'
import { buildAdminUrl } from '../../../lib/config/app-url'
import {
  attributionSummary,
  readAttributionFromCookieHeader,
  type AdAttribution,
} from '../../../lib/marketing/attribution'
import { emitMetaEvent, mintMetaEventId } from '../../../lib/marketing/meta-capi'

const NOTIFY_EMAIL = 'team@smd.services'
const RATE_LIMIT_PER_HOUR = 10
const MAX_MESSAGE_CHARS = 5000

/**
 * POST /api/intake/send
 *
 * Creates the entity from a /book intake submission, persists the
 * prospect's message as context, and sends the admin notification email.
 *
 * Response: { ok: true, entity_id }. The chat surface that used to
 * generate an AI follow-up reply and issue a conversation cookie was
 * removed when we moved AI fully backstage — the visible /book flow
 * is now intro form → slot picker → confirmation.
 *
 * Security: render-timestamp check + IP rate limiting (10/hour). Bot
 * Fight Mode runs at the Cloudflare edge before requests reach here.
 *
 * Bot defense detail: the client captures `Date.now()` at form-script-
 * execute time and sends it as `rendered_at`. Submissions under 2 seconds
 * old are treated as bot-driven (200 silent OK so the bot thinks it
 * succeeded). Real users take 30+ seconds to fill the form.
 */
const MIN_FORM_FILL_MS = 2000

// The interest allow-list and its slug→label map both live in
// lib/booking/config — one source of truth, enforced at /book (page
// prefill) and here (API boundary), and rendered via interestLabel().

interface ValidatedSendBody {
  name: string
  email: string
  businessName: string
  phone: string | null
  website: string | null
  messageRaw: string
  interest: string | null
}

function validateSendBody(body: Record<string, unknown>): ValidatedSendBody | Response {
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)
  const phone = trimString(body.phone)
  const messageRaw = typeof body.message === 'string' ? body.message.trim() : ''

  const fieldErrors: Record<string, string> = {}
  if (!name) fieldErrors.name = 'Name is required.'
  if (!email) fieldErrors.email = 'Email is required.'
  else if (!isValidEmail(email)) fieldErrors.email = 'Email looks invalid.'
  if (!businessName) fieldErrors.business_name = 'Business name is required.'
  if (!messageRaw) fieldErrors.message = 'Tell us a bit about the business.'

  if (Object.keys(fieldErrors).length > 0) {
    return errorResponse(400, 'validation_failed', 'Some required fields are missing.', {
      field_errors: fieldErrors,
    })
  }

  if (messageRaw.length > MAX_MESSAGE_CHARS) {
    return errorResponse(
      400,
      'validation_failed',
      `Your message is too long (max ${MAX_MESSAGE_CHARS} characters).`
    )
  }

  const interestRaw = trimString(body.interest)
  const interest = interestRaw && ALLOWED_INTERESTS.has(interestRaw) ? interestRaw : null

  return {
    name: name!,
    email: email!,
    businessName: businessName!,
    phone,
    website: trimString(body.website),
    messageRaw,
    interest,
  }
}

async function handlePost({ request, clientAddress, locals }: APIContext): Promise<Response> {
  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  const renderedAt = typeof body.rendered_at === 'number' ? body.rendered_at : NaN
  if (!Number.isFinite(renderedAt) || Date.now() - renderedAt < MIN_FORM_FILL_MS) {
    return jsonResponse(200, { ok: true })
  }

  const rateResult = await rateLimitByIp(
    env.BOOKING_CACHE,
    'intake_send',
    clientAddress,
    RATE_LIMIT_PER_HOUR
  )
  if (!rateResult.allowed) {
    return errorResponse(429, 'rate_limited')
  }

  const validated = validateSendBody(body)
  if (validated instanceof Response) return validated

  // First-touch ad attribution, set by middleware on the landing request
  // (ADR 0066 gate 1). Server-side read — the client never sends it.
  const attribution = readAttributionFromCookieHeader(request.headers.get('cookie'))

  let intakeResult: Awaited<ReturnType<typeof processIntakeSubmission>>
  try {
    intakeResult = await processIntakeSubmission(
      env.DB,
      ORG_ID,
      {
        name: validated.name,
        email: validated.email,
        businessName: validated.businessName,
        phone: validated.phone ?? '',
        website: validated.website,
        userMessage: validated.messageRaw || null,
        interest: validated.interest,
        attribution,
      },
      { source: 'website_intake_send' }
    )
  } catch (err) {
    console.error('[api/intake/send] processIntakeSubmission failed:', err)
    return errorResponse(500, 'internal_error')
  }

  // Meta CAPI Lead event (ADR 0066 gate 2, #1723) — server half of the
  // dedup pair; the browser fires the same event_name with this eventID.
  // Fail-closed no-op until the pixel/token are configured.
  const metaEventId = mintMetaEventId()
  await emitMetaEvent(
    env,
    import.meta.env.PUBLIC_META_PIXEL_ID,
    { eventName: 'Lead', eventId: metaEventId, request, email: validated.email },
    locals.cfContext ? (p) => locals.cfContext!.waitUntil(p) : undefined
  )

  try {
    await sendAdminNotification(env, {
      ...validated,
      entityId: intakeResult.entityId,
      message: validated.messageRaw,
      attribution,
    })
  } catch (emailErr) {
    console.error('[api/intake/send] Admin notification failed:', emailErr)
  }

  return jsonResponse(200, {
    ok: true,
    entity_id: intakeResult.entityId,
    meta_event_id: metaEventId,
  })
}

export const POST: APIRoute = (ctx) => handlePost(ctx)

interface AdminNotificationParams {
  name: string
  email: string
  businessName: string
  phone: string | null
  website: string | null
  message: string
  entityId: string
  interest: string | null
  attribution: AdAttribution | null
}

async function sendAdminNotification(
  workerEnv: typeof env,
  params: AdminNotificationParams
): Promise<void> {
  const adminUrl = buildAdminUrl(workerEnv, `/admin/entities/${params.entityId}`)
  const escapedName = escapeHtml(params.name)
  const escapedEmail = escapeHtml(params.email)
  const escapedBusiness = escapeHtml(params.businessName)
  const escapedPhone = params.phone ? escapeHtml(params.phone) : null
  const escapedWebsite = params.website ? escapeHtml(params.website) : null
  const escapedMessage = params.message ? escapeHtml(params.message) : null
  const intentLabel = interestLabel(params.interest)
  const escapedInterest = intentLabel ? escapeHtml(intentLabel) : null
  const attrSummary = attributionSummary(params.attribution)
  const escapedAttribution = attrSummary ? escapeHtml(attrSummary) : null

  const html = [
    `<p><strong>${escapedName}</strong> &lt;${escapedEmail}&gt; from <strong>${escapedBusiness}</strong> sent a message via the Send path on /book.</p>`,
    escapedInterest ? `<p><strong>Inquiring about:</strong> ${escapedInterest}</p>` : '',
    escapedAttribution ? `<p><strong>Ad source:</strong> ${escapedAttribution}</p>` : '',
    escapedPhone ? `<p>Phone: ${escapedPhone}</p>` : '',
    escapedWebsite ? `<p>Website: <a href="${escapedWebsite}">${escapedWebsite}</a></p>` : '',
    '<hr>',
    escapedMessage
      ? `<p><strong>What they wrote:</strong></p><blockquote>${escapedMessage.replace(/\n/g, '<br>')}</blockquote>`
      : '<p><em>No message.</em></p>',
    '<hr>',
    `<p><a href="${adminUrl}">View in admin →</a></p>`,
  ]
    .filter(Boolean)
    .join('')

  // Pre-existing bug fixed alongside #1722: this interpolated the imported
  // interestLabel FUNCTION (always truthy — every subject got function
  // source), not the resolved label for this lead.
  const subjectPrefix = intentLabel ? `[${intentLabel}] ` : ''
  await sendEmail(workerEnv.RESEND_API_KEY, {
    to: NOTIFY_EMAIL,
    reply_to: params.email,
    subject: `${subjectPrefix}[Send-path lead] ${params.businessName}`,
    html,
  })
}

// trimString / isValidEmail / escapeHtml / jsonResponse come from the
// shared lib/api/helpers module (2026-06-12 code review dedup).
