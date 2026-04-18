import type { APIRoute } from 'astro'
import { sendEmail } from '../../lib/email/resend'
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

export const POST: APIRoute = async ({ request }) => {
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot check — bots fill this hidden field, humans don't
  if (typeof body.website === 'string' && body.website.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  // Validate required fields
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

  if (Object.keys(errors).length > 0) {
    return jsonResponse(400, { error: 'Validation failed', fields: errors })
  }

  // Send notification email via Resend
  try {
    const escapedName = escapeHtml(name!)
    const escapedEmail = escapeHtml(email!)
    const escapedMessage = escapeHtml(message!).replace(/\n/g, '<br>')

    const result = await sendEmail(env.RESEND_API_KEY, {
      to: NOTIFY_EMAIL,
      reply_to: email!,
      subject: `Contact form: ${name}`,
      html: `<p><strong>From:</strong> ${escapedName} &lt;${escapedEmail}&gt;</p><hr><p>${escapedMessage}</p>`,
    })

    if (!result.success) {
      console.error('[api/contact] Resend error:', result.error)
      return jsonResponse(500, { error: 'Failed to send message' })
    }

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/contact] Error:', err)
    return jsonResponse(500, { error: 'Failed to send message' })
  }
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
