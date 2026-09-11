/**
 * SOW outbox jobs: the three side effects a signed SOW schedules (signed-copy
 * email, deposit invoice, portal invitation) and the loop that runs them for a
 * signature request, marking each row processing / completed / failed.
 *
 * Split out of service-finalize.ts on 2026-09-11 (review 2026-09-10,
 * Architecture 3). The finalize path enqueues these rows inside its atomic
 * batch and then calls processOutboxJobsForSignatureRequest; nothing here
 * writes the signature request itself.
 */

import { listOutboxJobsForSignatureRequest, type OutboxJob, type SignatureRequest } from './store'
import { sendEmail } from '../email/resend'
import { portalWelcomeEmailHtml, signatureConfirmationEmailHtml } from '../email/templates'
import { createStripeInvoice, sendStripeInvoice } from '../stripe/client'

// prettier-ignore
export function okResponse(): Response {
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

export function unknownDocumentResponse(documentId: string): Response {
  console.log(`[signwell-handler] Unknown SignWell document: ${documentId}`)
  return okResponse()
}

// prettier-ignore
export async function handleSignedEmailJob(db: D1Database, orgId: string, resendApiKey: string | undefined, job: OutboxJob): Promise<void> {
  const payload = JSON.parse(job.payload_json) as { entity_id: string }
  const contact = await db
    .prepare(
      'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, payload.entity_id)
    .first<{ email: string }>()

  if (!contact?.email) return

  const entity = await db
    .prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
    .bind(payload.entity_id, orgId)
    .first<{ name: string }>()

  const result = await sendEmail(resendApiKey, {
    to: contact.email,
    subject: 'SOW Signed - Next Steps',
    html: signatureConfirmationEmailHtml(entity?.name ?? 'there'),
  })
  // Never swallow a Resend rejection: a discarded {success:false} here means a
  // client signs and never learns next steps, with zero visibility. Throw so the
  // outbox marks the job failed (last_error recorded) and re-attempts — instead
  // of marking it completed as if the email sent.
  if (!result.success) {
    throw new Error(`SOW-signed email send failed: ${result.error ?? 'unknown'}`)
  }
}

// prettier-ignore
export async function handlePortalInvitationJob(_db: D1Database, _orgId: string, resendApiKey: string | undefined, appBaseUrl: string | undefined, job: OutboxJob): Promise<void> {
  const payload = JSON.parse(job.payload_json) as {
    user_email: string
    user_name: string
  }

  if (!resendApiKey) {
    console.log('[sow/outbox] Portal invitation skipped (no RESEND_API_KEY)')
    return
  }

  const portalLoginUrl = appBaseUrl
    ? `${appBaseUrl.replace('://', '://portal.')}`
    : 'https://portal.smd.services'
  const loginUrlWithEmail = `${portalLoginUrl}?email=${encodeURIComponent(payload.user_email)}`

  const result = await sendEmail(resendApiKey, {
    to: payload.user_email,
    subject: 'Your SMD Services portal is ready',
    html: portalWelcomeEmailHtml(payload.user_name, loginUrlWithEmail),
  })
  // Never swallow a Resend rejection: a discarded {success:false} here means a
  // client never gets portal access, with zero visibility. Throw so the outbox
  // marks the job failed (last_error recorded) and re-attempts.
  if (!result.success) {
    throw new Error(`Portal-invitation email send failed: ${result.error ?? 'unknown'}`)
  }
}

export async function handleDepositInvoiceJob(
  db: D1Database,
  orgId: string,
  stripeApiKey: string | undefined,
  job: OutboxJob
): Promise<void> {
  const payload = JSON.parse(job.payload_json) as {
    entity_id: string
    quote_id: string
    engagement_id: string
    invoice_id: string
    amount: number
  }

  if (!stripeApiKey) return

  const contact = await db
    .prepare(
      'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, payload.entity_id)
    .first<{ email: string }>()

  if (!contact?.email) return

  // Invoice description must come from AUTHORED content, never a synthesized
  // scope phrase. The prior hardcoded deposit scope label was a Pattern-B
  // fabrication (CLAUDE.md P0; see docs/reviews/code-review-2026-06-30.md).
  // Source the quote's authored engagement_overview (quote_id is already carried
  // on the outbox payload); fall back to a neutral, non-scope label when it has
  // not been authored yet.
  const quoteRow = await db
    .prepare('SELECT engagement_overview FROM quotes WHERE id = ? AND org_id = ?')
    .bind(payload.quote_id, orgId)
    .first<{ engagement_overview: string | null }>()
  const authoredOverview = quoteRow?.engagement_overview?.trim()
  const invoiceDescription =
    authoredOverview && authoredOverview.length > 0 ? authoredOverview : 'Deposit invoice'

  const amountCents = Math.round((payload.amount ?? 0) * 100)
  const stripeResult = await createStripeInvoice(stripeApiKey, {
    customer_email: contact.email,
    description: invoiceDescription,
    line_items: [
      {
        amount: amountCents,
        currency: 'usd',
        description: 'Deposit (50% of project price)',
        quantity: 1,
      },
    ],
    days_until_due: 3,
    metadata: {
      invoice_id: payload.invoice_id,
      engagement_id: payload.engagement_id,
    },
  })

  const sentResult = await sendStripeInvoice(stripeApiKey, stripeResult.id)
  await db
    .prepare(
      `UPDATE invoices
       SET stripe_invoice_id = ?, stripe_hosted_url = ?, status = 'sent', sent_at = ?, updated_at = ?
       WHERE id = ? AND org_id = ?`
    )
    .bind(
      sentResult.id,
      sentResult.hosted_invoice_url,
      new Date().toISOString(),
      new Date().toISOString(),
      payload.invoice_id,
      orgId
    )
    .run()
}

// prettier-ignore
export async function processOutboxJobsForSignatureRequest(db: D1Database, request: SignatureRequest, resendApiKey: string | undefined, stripeApiKey: string | undefined, appBaseUrl: string | undefined): Promise<void> {
  const jobs = await listOutboxJobsForSignatureRequest(db, request.org_id, request.id)
  for (const job of jobs) {
    if (job.status === 'completed') continue

    try {
      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'processing', attempt_count = attempt_count + 1, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(new Date().toISOString(), job.id, request.org_id)
        .run()

      if (job.type === 'send_sow_signed_email') {
        await handleSignedEmailJob(db, request.org_id, resendApiKey, job)
      } else if (job.type === 'send_deposit_invoice') {
        await handleDepositInvoiceJob(db, request.org_id, stripeApiKey, job)
      } else if (job.type === 'send_portal_invitation') {
        await handlePortalInvitationJob(db, request.org_id, resendApiKey, appBaseUrl, job)
      }

      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'completed', last_error = NULL, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(new Date().toISOString(), job.id, request.org_id)
        .run()
    } catch (err) {
      console.error('[sow/outbox] Job failed:', job.type, err)
      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'failed', last_error = ?, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(
          err instanceof Error ? err.message : String(err),
          new Date().toISOString(),
          job.id,
          request.org_id
        )
        .run()
    }
  }
}
