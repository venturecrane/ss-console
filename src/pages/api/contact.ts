import {
  escapeHtml,
  isValidEmail,
  jsonResponse,
  trimString,
  errorResponse,
} from '../../lib/api/helpers'
import type { APIContext, APIRoute } from 'astro'
import { sendEmail } from '../../lib/email/resend'
import { rateLimitByIp } from '../../lib/booking/rate-limit'
import { env } from 'cloudflare:workers'

/**
 * POST /api/contact
 *
 * Public contact form endpoint. Validates input, rejects bots via honeypot,
 * and sends a notification email via Resend.
 *
 * Security:
 * - Honeypot field rejects bot submissions silently
 * - Input validation with control character rejection
 * - No auth required (public-facing)
 */

const CONTROL_CHAR_RE = /[\r\n\0]/
const NOTIFY_EMAIL = 'team@smd.services'

function validateContactBody(body: Record<string, unknown>):
  | {
      name: string
      email: string
      message: string
    }
  | { errors: Record<string, string> } {
  const name = trimString(body.name)
  const email = trimString(body.email)
  const message = trimString(body.message)
  const errors: Record<string, string> = {}

  if (!name) {
    errors.name = 'Name is required'
  } else if (name.length > 200) {
    errors.name = 'Name must be 200 characters or fewer'
  } else if (CONTROL_CHAR_RE.test(name)) {
    errors.name = 'Name contains invalid characters'
  }

  if (!email) {
    errors.email = 'Email is required'
  } else if (CONTROL_CHAR_RE.test(email)) {
    errors.email = 'Email contains invalid characters'
  } else if (!isValidEmail(email)) {
    errors.email = 'Please enter a valid email address'
  }

  if (!message) {
    errors.message = 'Message is required'
  } else if (message.length > 5000) {
    errors.message = 'Message must be 5,000 characters or fewer'
  } else if (CONTROL_CHAR_RE.test(message.replace(/\n/g, ''))) {
    errors.message = 'Message contains invalid characters'
  }

  if (Object.keys(errors).length > 0) return { errors }
  return { name: name!, email: email!, message: message! }
}

async function handlePost({ request }: APIContext): Promise<Response> {
  // Rate limit: 3 requests/hour per IP
  const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
  const rateLimitResult = await rateLimitByIp(env.BOOKING_CACHE, 'contact', clientIp, 3)
  if (!rateLimitResult.allowed) {
    return errorResponse(429, 'rate_limited')
  }

  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  // Honeypot check — bots fill this hidden field, humans don't
  if (typeof body.website === 'string' && body.website.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  const validated = validateContactBody(body)
  if ('errors' in validated) {
    return errorResponse(400, 'validation_failed', undefined, { fields: validated.errors })
  }

  const { name, email, message } = validated

  // Send notification email via Resend
  try {
    const escapedName = escapeHtml(name)
    const escapedEmail = escapeHtml(email)
    const escapedMessage = escapeHtml(message).replace(/\n/g, '<br>')

    const result = await sendEmail(env.RESEND_API_KEY, {
      to: NOTIFY_EMAIL,
      reply_to: email,
      subject: `Contact form: ${name}`,
      html: `<p><strong>From:</strong> ${escapedName} &lt;${escapedEmail}&gt;</p><hr><p>${escapedMessage}</p>`,
    })

    if (!result.success) {
      console.error('[api/contact] Resend error:', result.error)
      return errorResponse(500, 'unavailable', 'The message could not be sent.')
    }

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/contact] Error:', err)
    return errorResponse(500, 'unavailable', 'The message could not be sent.')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
