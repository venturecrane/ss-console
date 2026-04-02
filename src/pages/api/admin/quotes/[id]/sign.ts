import type { APIRoute } from 'astro'
import { getQuote } from '../../../../../lib/db/quotes'
import { getClient } from '../../../../../lib/db/clients'
import { listContacts } from '../../../../../lib/db/contacts'
import { getPdf } from '../../../../../lib/storage/r2'
import { createSignatureRequest } from '../../../../../lib/signwell/client'
import type { SignWellCreateDocumentRequest } from '../../../../../lib/signwell/types'

/**
 * POST /api/admin/quotes/:id/sign
 *
 * Sends a quote's SOW PDF to SignWell for e-signature collection.
 *
 * Preconditions:
 * - Quote status must be 'draft' or 'sent'
 * - sow_path must exist (SOW PDF already generated)
 * - No signwell_doc_id yet (not already sent for signature)
 *
 * Process:
 * 1. Retrieve SOW PDF from R2
 * 2. Get client's primary contact for signer details
 * 3. Create signature request in SignWell
 * 4. Store signwell_doc_id on the quote
 * 5. Transition quote to 'sent' if currently 'draft'
 * 6. Redirect back to quote detail page
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ locals, redirect, params, url }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const quoteId = params.id
  if (!quoteId) {
    return new Response(JSON.stringify({ error: 'Quote ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  // Verify SignWell API key is configured
  const apiKey = env.SIGNWELL_API_KEY
  if (!apiKey) {
    console.error('[api/admin/quotes/[id]/sign] SIGNWELL_API_KEY not configured')
    return redirect(`/admin/clients?error=server`, 302)
  }

  try {
    // 1. Get quote and verify preconditions
    const quote = await getQuote(env.DB, session.orgId, quoteId)
    if (!quote) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    if (quote.status !== 'draft' && quote.status !== 'sent') {
      return redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=invalid_transition`,
        302
      )
    }

    if (!quote.sow_path) {
      return redirect(`/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=no_sow`, 302)
    }

    if (quote.signwell_doc_id) {
      return redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=already_sent`,
        302
      )
    }

    // 2. Get SOW PDF from R2
    const pdfObject = await getPdf(env.STORAGE, quote.sow_path)
    if (!pdfObject) {
      console.error(`[api/admin/quotes/[id]/sign] SOW PDF not found in R2: ${quote.sow_path}`)
      return redirect(`/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=server`, 302)
    }

    const pdfBuffer = await pdfObject.arrayBuffer()
    const pdfBase64 = btoa(String.fromCharCode(...new Uint8Array(pdfBuffer)))

    // 3. Get client and primary contact for signer details
    const client = await getClient(env.DB, session.orgId, quote.entity_id)
    if (!client) {
      return redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=client_not_found`,
        302
      )
    }

    const contacts = await listContacts(env.DB, session.orgId, quote.entity_id)
    const primaryContact = contacts.find((c) => c.email) ?? contacts[0]

    if (!primaryContact?.email) {
      return redirect(
        `/admin/entities/${quote.entity_id}/quotes/${quoteId}?error=no_contact_email`,
        302
      )
    }

    // 4. Build the webhook callback URL
    const callbackUrl = new URL('/api/webhooks/signwell', url.origin).toString()

    // 5. Create signature request in SignWell
    const signerId = crypto.randomUUID()

    const signRequest: SignWellCreateDocumentRequest = {
      name: `SOW — ${client.business_name}`,
      file_base64: pdfBase64,
      original_filename: 'sow.pdf',
      signers: [
        {
          id: signerId,
          name: primaryContact.name,
          email: primaryContact.email,
        },
      ],
      callback_url: callbackUrl,
      fields: [
        {
          type: 'signature',
          required: true,
          page: 1,
          x: 72,
          y: 680,
          width: 200,
          height: 40,
          signer_id: signerId,
          api_id: 'client_signature',
        },
        {
          type: 'date',
          required: true,
          page: 1,
          x: 72,
          y: 730,
          width: 120,
          height: 20,
          signer_id: signerId,
          api_id: 'client_date',
        },
      ],
      draft: false,
      custom_requester_name: 'SMD Services',
      subject: `SOW for Signature — ${client.business_name}`,
      message: `Hi ${primaryContact.name}, please review and sign the attached Statement of Work. If you have any questions, reply directly to this email.`,
    }

    const signwellDoc = await createSignatureRequest(apiKey, signRequest)

    // 6. Update quote with signwell_doc_id
    const now = new Date().toISOString()
    const updates: string[] = ['signwell_doc_id = ?', 'updated_at = ?']
    const updateParams: (string | null)[] = [signwellDoc.id, now]

    // 7. Transition to 'sent' if currently 'draft'
    if (quote.status === 'draft') {
      const sentAt = new Date()
      const expiresAt = new Date(sentAt.getTime() + 5 * 24 * 60 * 60 * 1000)

      updates.push("status = 'sent'")
      updates.push('sent_at = ?')
      updateParams.push(sentAt.toISOString())
      updates.push('expires_at = ?')
      updateParams.push(expiresAt.toISOString())
    }

    const sql = `UPDATE quotes SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
    updateParams.push(quoteId, session.orgId)

    await env.DB.prepare(sql)
      .bind(...updateParams)
      .run()

    return redirect(`/admin/entities/${quote.entity_id}/quotes/${quoteId}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/quotes/[id]/sign] Error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
