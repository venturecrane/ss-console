import type { APIRoute } from 'astro'
import { getFollowUp, completeFollowUp, skipFollowUp } from '../../../../lib/db/follow-ups'
import { getFollowUpTemplate } from '../../../../lib/email/follow-up-templates'
import type { FollowUpEmailData } from '../../../../lib/email/follow-up-templates'
import { sendEmail } from '../../../../lib/email/resend'

interface ClientRow {
  id: string
  business_name: string
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
 * Actions:
 * - action=complete: mark follow-up as completed (with optional notes)
 * - action=skip: mark follow-up as skipped (with optional notes)
 * - action=send_email: send the follow-up email via Resend, then mark completed
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect, params, url }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const followUpId = params.id
  if (!followUpId) {
    return new Response(JSON.stringify({ error: 'Follow-up ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const followUp = await getFollowUp(env.DB, session.orgId, followUpId)
    if (!followUp) {
      return redirect('/admin/follow-ups?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')
    const notes = formData.get('notes')
    const notesStr = notes && typeof notes === 'string' ? notes.trim() || null : null

    if (action === 'complete') {
      await completeFollowUp(env.DB, session.orgId, followUpId, notesStr)
      return redirect('/admin/follow-ups?saved=1', 302)
    }

    if (action === 'skip') {
      await skipFollowUp(env.DB, session.orgId, followUpId, notesStr)
      return redirect('/admin/follow-ups?saved=1', 302)
    }

    if (action === 'send_email') {
      // Look up client info
      const client = await env.DB.prepare(
        'SELECT id, business_name FROM clients WHERE id = ? AND org_id = ?'
      )
        .bind(followUp.client_id, session.orgId)
        .first<ClientRow>()

      if (!client) {
        return redirect('/admin/follow-ups?error=client_not_found', 302)
      }

      // Look up primary contact email
      const contact = await env.DB.prepare(
        'SELECT name, email FROM contacts WHERE client_id = ? AND org_id = ? LIMIT 1'
      )
        .bind(followUp.client_id, session.orgId)
        .first<ContactRow>()

      if (!contact?.email) {
        return redirect('/admin/follow-ups?error=no_contact_email', 302)
      }

      // Get the template for this follow-up type
      const templateFn = getFollowUpTemplate(followUp.type)
      if (!templateFn) {
        return redirect('/admin/follow-ups?error=no_template', 302)
      }

      // Build portal URL
      const baseUrl = `${url.protocol}//${url.host}`
      const portalUrl = `${baseUrl}/portal`

      const emailData: FollowUpEmailData = {
        clientName: contact.name,
        businessName: client.business_name,
        portalUrl,
      }

      const { subject, html } = templateFn(emailData)

      const result = await sendEmail(env.RESEND_API_KEY, {
        to: contact.email,
        subject,
        html,
      })

      if (!result.success) {
        console.error(`[follow-ups] Email send failed: ${result.error}`)
        return redirect('/admin/follow-ups?error=email_failed', 302)
      }

      // Mark as completed after successful send
      await completeFollowUp(env.DB, session.orgId, followUpId, `Email sent: ${result.id}`)
      return redirect('/admin/follow-ups?saved=1', 302)
    }

    return redirect('/admin/follow-ups?error=invalid_action', 302)
  } catch (err) {
    console.error('[api/admin/follow-ups/[id]] Error:', err)
    return redirect('/admin/follow-ups?error=server', 302)
  }
}
