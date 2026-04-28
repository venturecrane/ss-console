import type { APIRoute } from 'astro'
import { createMagicLink, MAGIC_LINK_EXPIRY_MS } from '../../../lib/auth/magic-link'
import { requirePortalBaseUrl } from '../../../lib/config/app-url'
import { sendEmail } from '../../../lib/email/resend'
import { buildMagicLinkUrl, portalInvitationEmailHtml } from '../../../lib/email/templates'
import { env } from 'cloudflare:workers'

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
export const POST: APIRoute = async ({ request, locals }) => {
  // Verify admin session (middleware already checks /admin/* routes,
  // but this is under /api/admin/* so we verify explicitly)
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const body = (await request.json()) as { userId?: string; email?: string }
    const { userId, email: newEmail } = body

    if (!userId || typeof userId !== 'string') {
      return new Response(JSON.stringify({ error: 'userId is required' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // Look up the client user — scoped to the admin's org to prevent
    // cross-tenant access (issue #172).
    const user = await env.DB.prepare(
      `SELECT * FROM users WHERE id = ? AND org_id = ? AND role = 'client'`
    )
      .bind(userId, session.orgId)
      .first<UserRow>()

    if (!user) {
      return new Response(JSON.stringify({ error: 'Client user not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    // If a new email is provided, update the user's email (OQ-010 bounce recovery)
    let targetEmail = user.email
    if (newEmail && typeof newEmail === 'string') {
      const normalizedEmail = newEmail.toLowerCase().trim()
      if (normalizedEmail !== user.email) {
        // Update is org-scoped as a defense-in-depth measure even though the
        // preceding SELECT already gates on org_id.
        const updateResult = await env.DB.prepare(
          `UPDATE users SET email = ? WHERE id = ? AND org_id = ?`
        )
          .bind(normalizedEmail, userId, session.orgId)
          .run()

        // D1 returns meta.changes for affected row count. If zero, the row
        // disappeared between the SELECT and UPDATE (or somehow slipped org
        // scoping) — fail closed rather than send an invitation we can't trust.
        if (!updateResult.meta || updateResult.meta.changes === 0) {
          return new Response(JSON.stringify({ error: 'Client user not found' }), {
            status: 404,
            headers: { 'Content-Type': 'application/json' },
          })
        }

        targetEmail = normalizedEmail
      }
    }

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
      return new Response(JSON.stringify({ error: 'Failed to send email' }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return new Response(
      JSON.stringify({
        success: true,
        emailId: result.id,
        sentTo: targetEmail,
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }
    )
  } catch (err) {
    console.error('[resend-invitation] Error:', err)
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
