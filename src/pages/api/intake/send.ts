import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { ORG_ID } from '../../../lib/constants'
import { resolveTurnstileConfig, verifyTurnstileToken } from '../../../lib/booking/turnstile'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { processIntakeSubmission } from '../../../lib/booking/intake-core'
import { appendContext } from '../../../lib/db/context'
import { generateConversationReply, ConversationApiError } from '../../../lib/claude/conversation'
import { sendEmail } from '../../../lib/email/resend'
import { buildAdminUrl } from '../../../lib/config/app-url'

const NOTIFY_EMAIL = 'team@smd.services'
const RATE_LIMIT_PER_HOUR = 10
const MAX_MESSAGE_CHARS = 5000

/**
 * POST /api/intake/send
 *
 * The "Send — we'll reach out" path of the unified /book intake. Lower-intent
 * sibling of /api/booking/reserve: captures the same identity fields but no
 * meeting / no calendar / no slot pick. After persisting the lead, generates
 * a single AI follow-up to whatever the prospect typed in the textarea so the
 * conversation feels alive, then notifies the team via email.
 *
 * Single-turn by design (V1). The architectural complexity of multi-turn
 * (entity authority, replay safety, Turnstile single-use, server-side
 * conversation_id lookup) is deferred to a future PR with proper auth.
 *
 * Security: honeypot + Turnstile + IP rate limiting (10/hour).
 */
export const POST: APIRoute = async ({ request, clientAddress }) => {
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot — bots fill this hidden field, humans don't. 200 silent OK so the
  // bot thinks it succeeded (matches /api/intake pattern).
  if (typeof body.website_url === 'string' && body.website_url.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  // Turnstile — fail-fast on misconfiguration so we never silently bypass bot
  // verification (matches /api/booking/reserve and /api/intake patterns).
  const turnstileConfig = resolveTurnstileConfig(env)
  const turnstileResult = await verifyTurnstileToken(
    turnstileConfig,
    typeof body.turnstile_token === 'string' ? body.turnstile_token : null,
    clientAddress
  )
  if (!turnstileResult.success) {
    return jsonResponse(403, { error: 'Bot verification failed' })
  }

  // Rate limit per IP.
  const rateResult = await rateLimitByIp(
    env.BOOKING_CACHE,
    'intake_send',
    clientAddress,
    RATE_LIMIT_PER_HOUR
  )
  if (!rateResult.allowed) {
    return jsonResponse(429, { error: 'Too many submissions. Please try again later.' })
  }

  // Required identity fields. Phone is required everywhere per Captain — the
  // consultant cannot follow up without a way to call.
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)
  const phone = trimString(body.phone)

  const fieldErrors: Record<string, string> = {}
  if (!name) fieldErrors.name = 'Name is required.'
  if (!email) fieldErrors.email = 'Email is required.'
  else if (!isValidEmail(email)) fieldErrors.email = 'Email looks invalid.'
  if (!businessName) fieldErrors.business_name = 'Business name is required.'
  if (!phone) fieldErrors.phone = 'Phone is required.'

  if (Object.keys(fieldErrors).length > 0) {
    return jsonResponse(400, {
      error: 'validation_failed',
      message: 'Some required fields are missing.',
      field_errors: fieldErrors,
    })
  }

  const website = trimString(body.website)
  const messageRaw = typeof body.message === 'string' ? body.message.trim() : ''
  if (messageRaw.length > MAX_MESSAGE_CHARS) {
    return jsonResponse(400, {
      error: 'validation_failed',
      message: `Your message is too long (max ${MAX_MESSAGE_CHARS} characters).`,
    })
  }

  // Persist entity + contact + intake context (handles all dedup logic).
  let intakeResult: Awaited<ReturnType<typeof processIntakeSubmission>>
  try {
    intakeResult = await processIntakeSubmission(
      env.DB,
      ORG_ID,
      {
        name: name!,
        email: email!,
        businessName: businessName!,
        phone,
        website,
        userMessage: messageRaw || null,
      },
      null, // no scheduledAt — Send path doesn't book a meeting
      'website_intake_send'
    )
  } catch (err) {
    console.error('[api/intake/send] processIntakeSubmission failed:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }

  // Generate AI follow-up if the prospect typed something. Empty message =>
  // skip the Claude call (no point talking to a void). The lead is still
  // captured and the admin notification still fires.
  let aiReply: string | null = null
  if (messageRaw) {
    const apiKey = env.ANTHROPIC_API_KEY
    if (!apiKey) {
      console.error('[api/intake/send] ANTHROPIC_API_KEY not configured')
      // Don't fail the whole submission — the lead is captured, AI is bonus.
    } else {
      try {
        aiReply = await generateConversationReply(apiKey, messageRaw, [])
        // Persist the AI reply so the consultant sees it in the timeline.
        try {
          await appendContext(env.DB, ORG_ID, {
            entity_id: intakeResult.entityId,
            type: 'intake',
            content: aiReply,
            source: 'website_intake_send_ai_reply',
            metadata: {
              model: 'claude',
              prospect_message: messageRaw,
            },
          })
        } catch (ctxErr) {
          console.error('[api/intake/send] AI reply context append failed:', ctxErr)
          // Don't fail — the reply still goes back to the prospect.
        }
      } catch (err) {
        if (err instanceof ConversationApiError) {
          console.error('[api/intake/send] Claude API error:', err.message, {
            status: err.statusCode,
            body: err.responseBody?.slice(0, 500),
          })
        } else {
          console.error('[api/intake/send] Unexpected Claude error:', err)
        }
        // Soft fail — return success on the lead capture, just no AI reply.
      }
    }
  }

  // Admin notification — fire and forget. Closes the lead-leak that this
  // whole refactor was about: the lead is in the DB AND someone gets pinged.
  try {
    await sendAdminNotification(env, {
      name: name!,
      email: email!,
      businessName: businessName!,
      phone: phone!,
      website,
      message: messageRaw,
      aiReply,
      entityId: intakeResult.entityId,
    })
  } catch (emailErr) {
    console.error('[api/intake/send] Admin notification failed:', emailErr)
  }

  return jsonResponse(200, {
    ok: true,
    entity_id: intakeResult.entityId,
    ai_reply: aiReply,
  })
}

interface AdminNotificationParams {
  name: string
  email: string
  businessName: string
  phone: string
  website: string | null
  message: string
  aiReply: string | null
  entityId: string
}

async function sendAdminNotification(
  workerEnv: typeof env,
  params: AdminNotificationParams
): Promise<void> {
  const adminUrl = buildAdminUrl(workerEnv, `/admin/entities/${params.entityId}`)
  const escapedName = escapeHtml(params.name)
  const escapedEmail = escapeHtml(params.email)
  const escapedBusiness = escapeHtml(params.businessName)
  const escapedPhone = escapeHtml(params.phone)
  const escapedWebsite = params.website ? escapeHtml(params.website) : null
  const escapedMessage = params.message ? escapeHtml(params.message) : null
  const escapedAiReply = params.aiReply ? escapeHtml(params.aiReply) : null

  const html = [
    `<p><strong>${escapedName}</strong> &lt;${escapedEmail}&gt; from <strong>${escapedBusiness}</strong> sent a message via the Send path on /book.</p>`,
    `<p>Phone: ${escapedPhone}</p>`,
    escapedWebsite ? `<p>Website: <a href="${escapedWebsite}">${escapedWebsite}</a></p>` : '',
    '<hr>',
    escapedMessage
      ? `<p><strong>What they wrote:</strong></p><blockquote>${escapedMessage.replace(/\n/g, '<br>')}</blockquote>`
      : '<p><em>No message — they submitted just contact info.</em></p>',
    escapedAiReply
      ? `<p><strong>AI follow-up sent back to them:</strong></p><blockquote>${escapedAiReply.replace(/\n/g, '<br>')}</blockquote>`
      : '',
    '<hr>',
    `<p><a href="${adminUrl}">View in admin →</a></p>`,
  ]
    .filter(Boolean)
    .join('')

  await sendEmail(workerEnv.RESEND_API_KEY, {
    to: NOTIFY_EMAIL,
    reply_to: params.email,
    subject: `[Send-path lead] ${params.businessName}`,
    html,
  })
}

function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
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
