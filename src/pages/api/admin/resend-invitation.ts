import type { APIContext, APIRoute } from 'astro'
import { createMagicLink, MAGIC_LINK_EXPIRY_MS } from '../../../lib/auth/magic-link'
import { requirePortalBaseUrl } from '../../../lib/config/app-url'
import { sendEmail } from '../../../lib/email/resend'
import { buildMagicLinkUrl, portalInvitationEmailHtml } from '../../../lib/email/templates'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../lib/auth/admin-session'
import { normalizeEmail } from '../../../lib/identity/email'
import { errorResponse, jsonResponse } from '../../../lib/api/helpers'

interface UserRow {
  id: string
  org_id: string
  email: string
  name: string
  role: string
  entity_id: string | null
}

/**
 * POST /api/admin/resend-invitation
 *
 * Admin endpoint to re-send a portal invitation to a client.
 * Used when the original invitation email bounced (OQ-010: admin corrects
 * email and re-sends).
 *
 * Protected by middleware — requires admin role session.
 *
 * Request body (JSON):
 *   { "userId": string, "email"?: string }
 *
 * If email is provided, updates the user's email before sending.
 * This supports the OQ-010 flow where admin corrects a bounced email.
 */
async function maybeUpdateEmail(
  orgId: string,
  userId: string,
  currentEmail: string,
  newEmail: unknown
): Promise<string | Response> {
  if (!newEmail || typeof newEmail !== 'string') return currentEmail
  const normalizedEmail = normalizeEmail(newEmail)
  if (normalizedEmail === currentEmail) return currentEmail

  // Update is org-scoped as a defense-in-depth measure even though the
  // preceding SELECT already gates on org_id.
  const updateResult = await env.DB.prepare(
    `UPDATE users SET email = ? WHERE id = ? AND org_id = ?`
  )
    .bind(normalizedEmail, userId, orgId)
    .run()

  // D1 returns meta.changes for affected row count. If zero, the row
  // disappeared between the SELECT and UPDATE (or somehow slipped org
  // scoping) — fail closed rather than send an invitation we can't trust.
  if (!updateResult.meta || updateResult.meta.changes === 0) {
    return errorResponse(404, 'not_found', 'Client user not found.')
  }

  return normalizedEmail
}

async function handlePost({ request, locals }: APIContext): Promise<Response> {
  // Verify admin session (middleware already checks /admin/* routes,
  // but this is under /api/admin/* so we verify explicitly)
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  try {
    const body: { userId?: unknown; email?: unknown } = await request.json()
    const { userId, email: newEmail } = body

    if (!userId || typeof userId !== 'string') {
      return errorResponse(400, 'validation_failed', 'userId is required.')
    }

    // Look up the client user — scoped to the admin's org to prevent
    // cross-tenant access (issue #172).
    const user = await env.DB.prepare(
      `SELECT * FROM users WHERE id = ? AND org_id = ? AND role = 'client'`
    )
      .bind(userId, session.orgId)
      .first<UserRow>()

    if (!user) {
      return errorResponse(404, 'not_found', 'Client user not found.')
    }

    // If a new email is provided, update the user's email (OQ-010 bounce recovery)
    const emailResult = await maybeUpdateEmail(session.orgId, userId, user.email, newEmail)
    if (emailResult instanceof Response) return emailResult
    const targetEmail = emailResult

    // Create magic link (15-minute TTL for portal invitation).
    const token = await createMagicLink(
      env.DB,
      {
        orgId: user.org_id,
        userId: user.id,
        email: targetEmail,
      },
      MAGIC_LINK_EXPIRY_MS
    )

    // Build verification URL from the canonical PORTAL_BASE_URL.
    // Never derive from request host — see issue #173.
    const baseUrl = requirePortalBaseUrl(env)
    const magicLinkUrl = buildMagicLinkUrl(baseUrl, token)

    // Send invitation email
    const html = portalInvitationEmailHtml(user.name, magicLinkUrl)
    const result = await sendEmail(env.RESEND_API_KEY, {
      to: targetEmail,
      subject: 'You have a proposal from SMD Services',
      html,
    })

    if (!result.success) {
      console.error(`[resend-invitation] Failed to send to ${targetEmail}: ${result.error}`)
      return errorResponse(502, 'unavailable', 'The email could not be sent.')
    }

    return jsonResponse(200, {
      success: true,
      emailId: result.id,
      sentTo: targetEmail,
    })
  } catch (err) {
    console.error('[resend-invitation] Error:', err)
    return errorResponse(500, 'internal_error')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
