import type { APIRoute } from 'astro'
import { createMagicLink } from '../../../lib/auth/magic-link'
import { requirePortalBaseUrl } from '../../../lib/config/app-url'
import { sendEmail } from '../../../lib/email/resend'
import { buildMagicLinkUrl, magicLinkEmailHtml } from '../../../lib/email/templates'

interface UserRow {
  id: string
  org_id: string
  email: string
  name: string
  role: string
  entity_id: string | null
}

/**
 * POST /api/auth/magic-link
 *
 * Generates a magic link for the given email and sends it via Resend.
 * Only works for client-role users (not admin users).
 *
 * On success: redirects to portal login with status=sent.
 * On failure: redirects to portal login with appropriate error.
 *
 * Security: Always returns the same response whether or not the email
 * exists, to prevent email enumeration.
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  try {
    const formData = await request.formData()
    const email = formData.get('email')

    if (!email || typeof email !== 'string') {
      return redirect('/auth/portal-login?error=server', 302)
    }

    const normalizedEmail = email.toLowerCase().trim()
    const env = locals.runtime.env

    // Look up client user by email
    const user = await env.DB.prepare(`SELECT * FROM users WHERE email = ? AND role = 'client'`)
      .bind(normalizedEmail)
      .first<UserRow>()

    if (!user) {
      // Don't reveal whether the email exists — show same success message
      // This prevents email enumeration attacks
      return redirect('/auth/portal-login?status=sent', 302)
    }

    // Create magic link token
    const token = await createMagicLink(env.DB, normalizedEmail)

    // Build the verification URL from the canonical PORTAL_BASE_URL.
    // Never derive from request host — see issue #173.
    const baseUrl = requirePortalBaseUrl(env)
    const magicLinkUrl = buildMagicLinkUrl(baseUrl, token)

    // Send the email
    const html = magicLinkEmailHtml(user.name, magicLinkUrl)
    const result = await sendEmail(env.RESEND_API_KEY, {
      to: normalizedEmail,
      subject: 'Sign in to your SMD Services portal',
      html,
    })

    if (!result.success) {
      console.error(`[magic-link] Failed to send email to ${normalizedEmail}: ${result.error}`)
      // Still show success to prevent enumeration
    }

    return redirect('/auth/portal-login?status=sent', 302)
  } catch (err) {
    console.error('[magic-link] Error:', err)
    return redirect('/auth/portal-login?error=server', 302)
  }
}
