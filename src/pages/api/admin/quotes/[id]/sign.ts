import type { APIRoute } from 'astro'
import { buildAppUrl } from '../../../../../lib/config/app-url'
import { getQuote } from '../../../../../lib/db/quotes'
import { getEntity } from '../../../../../lib/db/entities'
import { listContacts } from '../../../../../lib/db/contacts'
import { getPdf } from '../../../../../lib/storage/r2'
import { createSignatureRequest } from '../../../../../lib/signwell/client'
import type { SignWellCreateDocumentRequest } from '../../../../../lib/signwell/types'
import { getSowSigningFields } from '../../../../../lib/signwell/field-config' // coordinate fallback

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
export const POST: APIRoute = async ({ locals, redirect, params }) => {
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
    return redirect(`/admin/entities?error=server`, 302)
  }

  try {
    // 1. Get quote and verify preconditions
    const quote = await getQuote(env.DB, session.orgId, quoteId)
    if (!quote) {
      return redirect('/admin/entities?error=not_found', 302)
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
    const entity = await getEntity(env.DB, session.orgId, quote.entity_id)
    if (!entity) {
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

    // 4. Build the webhook callback URL from the canonical APP_BASE_URL.
    // Never derive from request host — see issue #173.
    const callbackUrl = buildAppUrl(env, '/api/webhooks/signwell')

    // 5. Create signature request in SignWell
    const signerId = crypto.randomUUID()

    // Text tags are embedded in the PDF by inject-signing-tags.ts (called during renderSow).
    // SignWell auto-detects fields from these tags — no manual coordinates needed.
    const signRequest: SignWellCreateDocumentRequest = {
      name: `SOW — ${entity.name}`,
      files: [{ file_base64: pdfBase64, name: 'sow.pdf' }],
      recipients: [
        {
          id: signerId,
          name: primaryContact.name,
          email: primaryContact.email,
        },
      ],
      callback_url: callbackUrl,
      text_tags: true,
      draft: false,
      custom_requester_name: 'SMD Services',
      subject: `SOW for Signature — ${entity.name}`,
      message: `Hi ${primaryContact.name}, please review and sign the attached Statement of Work. If you have any questions, reply directly to this email.`,
    }

    const signwellDoc = await createSignatureRequest(apiKey, signRequest)

    // Verify text tags were detected. If not, fall back to coordinate-based placement.
    const detectedFields = (
      (signwellDoc as unknown as { fields?: unknown[][] }).fields || []
    ).flat()
    if (detectedFields.length === 0) {
      console.warn(
        '[api/admin/quotes/[id]/sign] Text tags not detected — falling back to coordinates'
      )
      const fallbackFields = getSowSigningFields()
      // Re-create with explicit field coordinates
      const fallbackRequest: SignWellCreateDocumentRequest = {
        ...signRequest,
        text_tags: undefined,
        fields: [
          [
            {
              ...fallbackFields.signature,
              required: true,
              recipient_id: signerId,
              api_id: 'client_signature',
            },
            {
              ...fallbackFields.date,
              required: true,
              recipient_id: signerId,
              api_id: 'client_date',
            },
          ],
        ],
      }
      // Delete the fieldless document first
      try {
        await fetch(`https://www.signwell.com/api/v1/documents/${signwellDoc.id}`, {
          method: 'DELETE',
          headers: { 'X-Api-Key': apiKey },
        })
      } catch {
        // Best effort cleanup
      }
      const fallbackDoc = await createSignatureRequest(apiKey, fallbackRequest)
      Object.assign(signwellDoc, fallbackDoc)
    }

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
    const msg = err instanceof Error ? err.message : String(err)
    console.error('[api/admin/quotes/[id]/sign] Error:', msg, err instanceof Error ? err.stack : '')
    return redirect(
      `/admin/entities?error=server&detail=${encodeURIComponent(msg.slice(0, 200))}`,
      302
    )
  }
}
