/**
 * SignWell webhook handler — two-phase pattern.
 *
 * Follows the architecture from docs/spikes/d1-batch-api.md EXACTLY:
 *
 * Phase 1 — Atomic D1 Batch (all-or-nothing):
 *   1. Look up quote by signwell_doc_id
 *   2. Idempotency check: if quote.status === 'accepted', return early
 *   3. Generate UUIDs for new engagement and deposit invoice BEFORE the batch
 *   4. db.batch([
 *        updateQuoteStatus to 'accepted' with accepted_at,
 *        updateClientStatus to 'active',
 *        createEngagement from quote data,
 *        createDepositInvoice (draft status, amount = quote.deposit_amount)
 *      ])
 *
 * Phase 2 — Best-effort side effects:
 *   1. Download signed PDF from SignWell API
 *   2. Upload to R2 at {orgId}/quotes/{quoteId}/signed-sow.pdf
 *   3. Update quote.signed_sow_path
 *   4. Send confirmation email to client via Resend
 *
 * Returns 200 after Phase 1 succeeds, even if Phase 2 fails.
 */

import type { SignWellWebhookPayload } from '../signwell/types'
import { getSignedPdf } from '../signwell/client'
import type { Quote } from '../db/quotes'

/**
 * Look up a quote by its SignWell document ID.
 * Not scoped to org_id because webhooks don't carry org context —
 * the signwell_doc_id is globally unique.
 */
async function getQuoteBySignWellDocId(
  db: D1Database,
  signwellDocId: string
): Promise<Quote | null> {
  const result = await db
    .prepare('SELECT * FROM quotes WHERE signwell_doc_id = ?')
    .bind(signwellDocId)
    .first<Quote>()

  return result ?? null
}

/**
 * Look up the primary contact email for a client.
 */
async function getClientPrimaryEmail(
  db: D1Database,
  orgId: string,
  clientId: string
): Promise<string | null> {
  const contact = await db
    .prepare(
      'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, clientId)
    .first<{ email: string }>()

  return contact?.email ?? null
}

/**
 * Handle the document_completed webhook event from SignWell.
 *
 * @param db - D1 database binding
 * @param storage - R2 bucket binding
 * @param apiKey - SignWell API key for downloading signed PDF
 * @param resendApiKey - Resend API key for sending confirmation emails (optional)
 * @param payload - The parsed webhook payload
 * @returns Response to send back to SignWell (200 or 500)
 */
export async function handleDocumentCompleted(
  db: D1Database,
  storage: R2Bucket,
  apiKey: string,
  resendApiKey: string | undefined,
  payload: SignWellWebhookPayload
): Promise<Response> {
  const documentId = payload.data.id
  const now = new Date().toISOString()

  // --- Pre-batch reads (outside transaction) ---

  // 1. Look up quote by SignWell document ID
  const quote = await getQuoteBySignWellDocId(db, documentId)
  if (!quote) {
    console.log(`[signwell-handler] Unknown SignWell document: ${documentId}`)
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // 2. Idempotency guard — if already accepted, skip processing
  if (quote.status === 'accepted') {
    console.log(`[signwell-handler] Quote ${quote.id} already accepted, skipping`)
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Phase 1: Atomic D1 batch ---

  const engagementId = crypto.randomUUID()
  const invoiceId = crypto.randomUUID()

  try {
    await db.batch([
      // 1. Update quote status to 'accepted'
      db
        .prepare(
          `UPDATE quotes SET status = 'accepted', accepted_at = ?, updated_at = ?
         WHERE id = ? AND org_id = ? AND status = 'sent'`
        )
        .bind(now, now, quote.id, quote.org_id),

      // 2. Update client status to 'active'
      db
        .prepare(
          `UPDATE clients SET status = 'active', updated_at = ?
         WHERE id = ? AND org_id = ?`
        )
        .bind(now, quote.entity_id, quote.org_id),

      // 3. Create engagement from quote data
      db
        .prepare(
          `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, estimated_hours, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?)`
        )
        .bind(engagementId, quote.org_id, quote.entity_id, quote.id, quote.total_hours, now, now),

      // 4. Create deposit invoice (draft status)
      db
        .prepare(
          `INSERT INTO invoices (id, org_id, engagement_id, entity_id, type, amount, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'deposit', ?, 'draft', ?, ?)`
        )
        .bind(
          invoiceId,
          quote.org_id,
          engagementId,
          quote.entity_id,
          quote.deposit_amount,
          now,
          now
        ),
    ])
  } catch (err) {
    // Batch failed — all changes rolled back. Safe to let the webhook retry.
    console.error('[signwell-handler] Phase 1 batch failed:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Phase 2: Side effects (best-effort) ---
  // These run after the batch succeeds. If any fail, the database state
  // is correct but side effects are incomplete.

  // 2a. Download signed PDF from SignWell and upload to R2
  const signedSowPath = `${quote.org_id}/quotes/${quote.id}/signed-sow.pdf`

  try {
    const signedPdf = await getSignedPdf(apiKey, documentId)
    await storage.put(signedSowPath, signedPdf, {
      httpMetadata: {
        contentType: 'application/pdf',
      },
      customMetadata: {
        signedAt: now,
        quoteId: quote.id,
        signwellDocId: documentId,
      },
    })

    // 2b. Update quote with signed_sow_path
    await db
      .prepare(`UPDATE quotes SET signed_sow_path = ?, updated_at = ? WHERE id = ? AND org_id = ?`)
      .bind(signedSowPath, now, quote.id, quote.org_id)
      .run()
  } catch (err) {
    console.error('[signwell-handler] Failed to upload signed PDF to R2:', err)
    // Non-fatal: PDF can be re-fetched from SignWell later
  }

  // 2c. Send confirmation email to client
  if (resendApiKey) {
    try {
      const clientEmail = await getClientPrimaryEmail(db, quote.org_id, quote.entity_id)
      if (clientEmail) {
        const client = await db
          .prepare('SELECT business_name FROM clients WHERE id = ? AND org_id = ?')
          .bind(quote.entity_id, quote.org_id)
          .first<{ business_name: string }>()

        await fetch('https://api.resend.com/emails', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${resendApiKey}`,
          },
          body: JSON.stringify({
            from: 'SMD Services <noreply@smd.services>',
            to: clientEmail,
            subject: 'SOW Signed — Next Steps',
            html: signatureConfirmationEmailHtml(client?.business_name ?? 'there'),
          }),
        })
      }
    } catch (err) {
      console.error('[signwell-handler] Failed to send confirmation email:', err)
      // Non-fatal: admin can trigger manually
    }
  }

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Simple confirmation email HTML sent after SOW is signed.
 */
function signatureConfirmationEmailHtml(businessName: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi ${businessName},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your Statement of Work has been signed successfully. We're excited to get started working together.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Our team will be in touch shortly with next steps, including the deposit invoice and scheduling details.
      </p>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        If you have any questions, reply directly to this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}
