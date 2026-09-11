import type { APIContext, APIRoute } from 'astro'
import { buildPortalUrl } from '../../../../lib/config/app-url'
import { getFollowUp, completeFollowUp, skipFollowUp } from '../../../../lib/db/follow-ups'
import { getFollowUpTemplate } from '../../../../lib/email/follow-up-templates'
import type { FollowUpEmailData } from '../../../../lib/email/follow-up-templates'
import { sendEmail } from '../../../../lib/email/resend'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../lib/api/helpers'

interface EntityRow {
  id: string
  name: string
}

interface ContactRow {
  name: string
  email: string | null
}

/**
 * POST /api/admin/follow-ups/:id
 *
 * Manages follow-up actions: complete, skip, or send email.
 *
 * Protected by auth middleware (requires admin role).
 */

type Redirect = APIContext['redirect']

async function handleSendEmail(
  redirect: Redirect,
  orgId: string,
  followUpId: string,
  entityId: string,
  followUpType: string
): Promise<Response> {
  const client = await env.DB.prepare('SELECT id, name FROM entities WHERE id = ? AND org_id = ?')
    .bind(entityId, orgId)
    .first<EntityRow>()
  if (!client) return redirect('/admin/follow-ups?error=client_not_found', 302)

  const contact = await env.DB.prepare(
    'SELECT name, email FROM contacts WHERE entity_id = ? AND org_id = ? LIMIT 1'
  )
    .bind(entityId, orgId)
    .first<ContactRow>()
  if (!contact?.email) return redirect('/admin/follow-ups?error=no_contact_email', 302)

  const templateFn = getFollowUpTemplate(followUpType)
  if (!templateFn) return redirect('/admin/follow-ups?error=no_template', 302)

  const emailData: FollowUpEmailData = {
    clientName: contact.name,
    businessName: client.name,
    portalUrl: buildPortalUrl(env),
  }
  const { subject, html } = templateFn(emailData)
  const result = await sendEmail(env.RESEND_API_KEY, { to: contact.email, subject, html })

  if (!result.success) {
    console.error(`[follow-ups] Email send failed: ${result.error}`)
    return redirect('/admin/follow-ups?error=email_failed', 302)
  }
  await completeFollowUp(env.DB, orgId, followUpId)
  return redirect('/admin/follow-ups?saved=1', 302)
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const followUpId = params.id
  if (!followUpId) {
    return errorResponse(400, 'validation_failed', 'Follow-up ID required.')
  }

  try {
    const followUp = await getFollowUp(env.DB, session.orgId, followUpId)
    if (!followUp) return redirect('/admin/follow-ups?error=not_found', 302)

    const formData = await request.formData()
    const action = formData.get('action')

    if (action === 'complete') {
      await completeFollowUp(env.DB, session.orgId, followUpId)
      return redirect('/admin/follow-ups?saved=1', 302)
    }
    if (action === 'skip') {
      await skipFollowUp(env.DB, session.orgId, followUpId)
      return redirect('/admin/follow-ups?saved=1', 302)
    }
    if (action === 'send_email') {
      return handleSendEmail(redirect, session.orgId, followUpId, followUp.entity_id, followUp.type)
    }
    return redirect('/admin/follow-ups?error=invalid_action', 302)
  } catch (err) {
    console.error('[api/admin/follow-ups/[id]] Error:', err)
    return redirect('/admin/follow-ups?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
