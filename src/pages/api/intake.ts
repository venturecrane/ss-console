import type { APIRoute } from 'astro'
import { processIntakeSubmission } from '../../lib/booking/intake-core'
import { verifyTurnstileToken } from '../../lib/booking/turnstile'
import { rateLimitByIp } from '../../lib/booking/rate-limit'
import { sendEmail } from '../../lib/email/resend'
import { ORG_ID } from '../../lib/constants'
import { buildAdminUrl } from '../../lib/config/app-url'

const NOTIFY_EMAIL = 'team@smd.services'
const RATE_LIMIT = 5

/**
 * POST /api/intake
 *
 * Standalone intake endpoint — prospects share business info without
 * booking a call. Lower commitment than /api/booking/reserve.
 *
 * Creates entity + contact + context (no assessment — that happens when
 * a call is actually scheduled). Sends admin notification.
 *
 * Security: Turnstile + honeypot + IP rate limiting (5/hour).
 */
export const POST: APIRoute = async ({ request, locals, clientAddress }) => {
  const env = locals.runtime.env

  // Parse JSON body
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot check — bots fill this hidden field, humans don't
  if (typeof body.website_url === 'string' && body.website_url.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  // Turnstile verification
  const turnstileResult = await verifyTurnstileToken(
    env.TURNSTILE_SECRET_KEY,
    typeof body.turnstile_token === 'string' ? body.turnstile_token : null,
    clientAddress
  )
  if (!turnstileResult.success) {
    return jsonResponse(403, { error: 'Bot verification failed' })
  }

  // Rate limiting
  const rateResult = await rateLimitByIp(env.BOOKING_CACHE, 'intake', clientAddress, RATE_LIMIT)
  if (!rateResult.allowed) {
    return jsonResponse(429, { error: 'Too many submissions. Please try again later.' })
  }

  // Validate required fields
  const name = trimString(body.name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)
  const biggestChallenge = trimString(body.biggest_challenge)

  if (!name || !email || !businessName || !biggestChallenge) {
    return jsonResponse(400, {
      error: 'name, email, business_name, and biggest_challenge are required',
    })
  }

  if (!isValidEmail(email)) {
    return jsonResponse(400, { error: 'Invalid email address' })
  }

  // Optional fields
  const vertical = trimString(body.vertical) || null
  const employeeCount = trimString(body.employee_count) || null
  const yearsInBusiness = trimString(body.years_in_business) || null
  const howHeard = trimString(body.how_heard)

  try {
    const result = await processIntakeSubmission(
      env.DB,
      ORG_ID,
      {
        name,
        email,
        businessName,
        vertical,
        employeeCount: employeeCount ? parseInt(employeeCount, 10) || null : null,
        yearsInBusiness: yearsInBusiness ? parseInt(yearsInBusiness, 10) || null : null,
        biggestChallenge,
        howHeard,
      },
      null, // no scheduledAt — standalone intake
      'website_intake'
    )

    // Notify the team — fire and forget
    try {
      const escapedName = escapeHtml(name)
      const escapedEmail = escapeHtml(email)
      const escapedBusiness = escapeHtml(businessName)
      const detailLines = result.intakeLines.map((line) => `<p>${escapeHtml(line)}</p>`).join('')

      await sendEmail(env.RESEND_API_KEY, {
        to: NOTIFY_EMAIL,
        reply_to: email,
        subject: `New inquiry: ${businessName}`,
        html:
          `<p><strong>${escapedName}</strong> &lt;${escapedEmail}&gt; from <strong>${escapedBusiness}</strong> shared info about their business (no call scheduled yet).</p>` +
          `<hr>${detailLines}` +
          `<hr><p><a href="${buildAdminUrl(env, `/admin/entities/${result.entityId}`)}">View in admin →</a></p>`,
      })
    } catch (emailErr) {
      console.error('[api/intake] Notification email error:', emailErr)
    }

    return jsonResponse(201, { ok: true })
  } catch (err) {
    console.error('[api/intake] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
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

function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
