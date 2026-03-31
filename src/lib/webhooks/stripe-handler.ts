/**
 * Stripe webhook handler — two-phase pattern.
 *
 * Follows the architecture from docs/spikes/d1-batch-api.md EXACTLY:
 *
 * Phase 1 — Atomic D1 Batch (all-or-nothing):
 *   invoice.paid:
 *     1. Update invoice status to 'paid', set paid_at
 *     2. If deposit invoice: update engagement status to 'active', set start_date
 *   invoice.payment_failed:
 *     1. Log the failure (keep invoice status as sent)
 *
 * Phase 2 — Best-effort side effects:
 *   1. Send payment confirmation email via Resend
 *
 * Returns 200 after Phase 1 succeeds, even if Phase 2 fails.
 */

import type { StripeWebhookEvent } from '../stripe/types'
import { sendEmail } from '../email/resend'
import { paymentConfirmationEmailHtml } from '../email/templates'

/**
 * Look up an invoice by its Stripe invoice ID.
 * Not scoped to org_id because webhooks don't carry org context —
 * the stripe_invoice_id is globally unique.
 */
async function getInvoiceByStripeId(
  db: D1Database,
  stripeInvoiceId: string
): Promise<{
  id: string
  org_id: string
  client_id: string
  engagement_id: string | null
  type: string
  amount: number
  status: string
} | null> {
  const result = await db
    .prepare(
      'SELECT id, org_id, client_id, engagement_id, type, amount, status FROM invoices WHERE stripe_invoice_id = ?'
    )
    .bind(stripeInvoiceId)
    .first<{
      id: string
      org_id: string
      client_id: string
      engagement_id: string | null
      type: string
      amount: number
      status: string
    }>()

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
      'SELECT email FROM contacts WHERE org_id = ? AND client_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, clientId)
    .first<{ email: string }>()

  return contact?.email ?? null
}

/**
 * Look up the business name for a client.
 */
async function getClientBusinessName(
  db: D1Database,
  orgId: string,
  clientId: string
): Promise<string> {
  const client = await db
    .prepare('SELECT business_name FROM clients WHERE id = ? AND org_id = ?')
    .bind(clientId, orgId)
    .first<{ business_name: string }>()

  return client?.business_name ?? 'there'
}

/**
 * Handle the invoice.paid webhook event from Stripe.
 *
 * @param db - D1 database binding
 * @param resendApiKey - Resend API key for sending confirmation emails (optional)
 * @param event - The parsed Stripe webhook event
 * @returns Response to send back to Stripe (200 or 500)
 */
export async function handleInvoicePaid(
  db: D1Database,
  resendApiKey: string | undefined,
  event: StripeWebhookEvent
): Promise<Response> {
  const stripeInvoiceId = event.data.object.id
  const now = new Date().toISOString()

  // --- Pre-batch reads (outside transaction) ---

  // 1. Look up invoice by Stripe invoice ID
  const invoice = await getInvoiceByStripeId(db, stripeInvoiceId)
  if (!invoice) {
    console.log(`[stripe-handler] Unknown Stripe invoice: ${stripeInvoiceId}`)
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // 2. Idempotency guard — if already paid, skip processing
  if (invoice.status === 'paid') {
    console.log(`[stripe-handler] Invoice ${invoice.id} already paid, skipping`)
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Phase 1: Atomic D1 batch ---

  try {
    const batchStatements: D1PreparedStatement[] = [
      // 1. Update invoice status to 'paid'
      db
        .prepare(
          `UPDATE invoices SET status = 'paid', paid_at = ?, payment_method = 'stripe', updated_at = ?
         WHERE id = ? AND org_id = ?`
        )
        .bind(now, now, invoice.id, invoice.org_id),
    ]

    // 2. If this is a deposit invoice, activate the engagement
    if (invoice.type === 'deposit' && invoice.engagement_id) {
      batchStatements.push(
        db
          .prepare(
            `UPDATE engagements SET status = 'active', start_date = ?, updated_at = ?
           WHERE id = ? AND org_id = ? AND status = 'scheduled'`
          )
          .bind(now, now, invoice.engagement_id, invoice.org_id)
      )
    }

    await db.batch(batchStatements)
  } catch (err) {
    // Batch failed — all changes rolled back. Safe to let the webhook retry.
    console.error('[stripe-handler] Phase 1 batch failed:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Phase 2: Side effects (best-effort) ---

  try {
    const clientEmail = await getClientPrimaryEmail(db, invoice.org_id, invoice.client_id)
    if (clientEmail) {
      const clientName = await getClientBusinessName(db, invoice.org_id, invoice.client_id)
      const formattedAmount = `$${invoice.amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

      await sendEmail(resendApiKey, {
        to: clientEmail,
        subject: 'Payment received — thank you',
        html: paymentConfirmationEmailHtml(clientName, formattedAmount),
      })
    }
  } catch (err) {
    console.error('[stripe-handler] Failed to send payment confirmation email:', err)
    // Non-fatal: admin can send manually
  }

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Handle the invoice.payment_failed webhook event from Stripe.
 *
 * We log the failure but do not change invoice status — it remains as 'sent'.
 * The admin dashboard will show the invoice is still outstanding.
 */
export async function handleInvoicePaymentFailed(
  db: D1Database,
  event: StripeWebhookEvent
): Promise<Response> {
  const stripeInvoiceId = event.data.object.id

  const invoice = await getInvoiceByStripeId(db, stripeInvoiceId)
  if (!invoice) {
    console.log(`[stripe-handler] Unknown Stripe invoice (payment_failed): ${stripeInvoiceId}`)
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  console.error(
    `[stripe-handler] Payment failed for invoice ${invoice.id} (Stripe: ${stripeInvoiceId}), amount: $${invoice.amount}`
  )

  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}
