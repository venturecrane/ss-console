import type { APIRoute } from 'astro'
import { buildPortalUrl } from '../../../../lib/config/app-url'
import { getInvoice, updateInvoice, updateInvoiceStatus } from '../../../../lib/db/invoices'
import type { InvoiceStatus } from '../../../../lib/db/invoices'
import {
  createStripeInvoice,
  sendStripeInvoice,
  voidStripeInvoice,
} from '../../../../lib/stripe/client'
import { sendEmail } from '../../../../lib/email/resend'
import { invoiceSentEmailHtml } from '../../../../lib/email/templates'

/**
 * POST /api/admin/invoices/:id
 *
 * Performs actions on an existing invoice:
 * - action=send: Creates invoice in Stripe, sends it, updates local status
 * - action=void: Voids the invoice (Stripe + local)
 * - action=mark_paid: Manual override for offline payments (OQ-008)
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const invoiceId = params.id
  if (!invoiceId) {
    return new Response(JSON.stringify({ error: 'Invoice ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const existing = await getInvoice(env.DB, session.orgId, invoiceId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')
    const redirectUrl = formData.get('redirect_url')
    const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/entities'

    // ----- ACTION: send -----
    if (action === 'send') {
      if (existing.status !== 'draft') {
        return redirect(`${target}?error=invalid_transition`, 302)
      }

      // Look up client email for Stripe
      const contact = await env.DB.prepare(
        'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
      )
        .bind(session.orgId, existing.entity_id)
        .first<{ email: string }>()

      const clientEmail = contact?.email

      // Look up client business name
      const entityRow = await env.DB.prepare(
        'SELECT name FROM entities WHERE id = ? AND org_id = ?'
      )
        .bind(existing.entity_id, session.orgId)
        .first<{ name: string }>()

      const clientName = entityRow?.name ?? 'there'

      try {
        // Create in Stripe (or dev-mode stub)
        const stripeResult = await createStripeInvoice(env.STRIPE_API_KEY, {
          customer_email: clientEmail ?? 'placeholder@example.com',
          description: existing.description ?? `Invoice from SMD Services`,
          line_items: [
            {
              amount: Math.round(existing.amount * 100), // Convert dollars to cents
              currency: 'usd',
              description: existing.description ?? `SMD Services — ${existing.type} invoice`,
              quantity: 1,
            },
          ],
          days_until_due: 15,
          collection_method: 'send_invoice',
          metadata: {
            invoice_id: existing.id,
            org_id: session.orgId,
            type: existing.type,
          },
          payment_settings: {
            payment_method_types: ['ach_debit', 'card'],
          },
        })

        // Send the Stripe invoice (finalize + email)
        const sentResult = await sendStripeInvoice(env.STRIPE_API_KEY, stripeResult.id)

        // Update local invoice with Stripe details and transition to sent
        await updateInvoice(env.DB, session.orgId, invoiceId, {
          stripe_invoice_id: stripeResult.id,
          stripe_hosted_url: sentResult.hosted_invoice_url,
        })
        await updateInvoiceStatus(env.DB, session.orgId, invoiceId, 'sent' as InvoiceStatus)
      } catch (err) {
        console.error('[api/admin/invoices/[id]] Stripe send error:', err)
        // Leave at draft so admin can retry — advancing to sent with no
        // stripe_invoice_id creates an unrecoverable state.
        const message = err instanceof Error ? err.message : 'Stripe error'
        return redirect(`${target}?error=${encodeURIComponent(message)}`, 302)
      }

      // Best-effort: send notification email to client
      if (clientEmail) {
        try {
          const formattedAmount = `$${existing.amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
          // Build portal URL from canonical PORTAL_BASE_URL (falls back to
          // APP_BASE_URL). Never derive from request host — see issue #173.
          const portalUrl = buildPortalUrl(env, '/portal/invoices')

          await sendEmail(env.RESEND_API_KEY, {
            to: clientEmail,
            subject: 'Your invoice from SMD Services is ready',
            html: invoiceSentEmailHtml(clientName, formattedAmount, portalUrl),
          })
        } catch (err) {
          console.error('[api/admin/invoices/[id]] Email send error:', err)
          // Non-fatal
        }
      }

      return redirect(`${target}?saved=1`, 302)
    }

    // ----- ACTION: void -----
    if (action === 'void') {
      if (existing.status !== 'draft' && existing.status !== 'sent') {
        return redirect(`${target}?error=invalid_transition`, 302)
      }

      // Void in Stripe if it exists there
      if (existing.stripe_invoice_id) {
        try {
          await voidStripeInvoice(env.STRIPE_API_KEY, existing.stripe_invoice_id)
        } catch (err) {
          console.error('[api/admin/invoices/[id]] Stripe void error:', err)
          // Continue voiding locally even if Stripe fails
        }
      }

      try {
        await updateInvoiceStatus(env.DB, session.orgId, invoiceId, 'void' as InvoiceStatus)
      } catch (err) {
        console.error('[api/admin/invoices/[id]] Void status error:', err)
        return redirect(`${target}?error=invalid_transition`, 302)
      }

      return redirect(`${target}?saved=1`, 302)
    }

    // ----- ACTION: mark_paid (manual override for offline payments, OQ-008) -----
    if (action === 'mark_paid') {
      if (existing.status !== 'sent' && existing.status !== 'overdue') {
        return redirect(`${target}?error=invalid_transition`, 302)
      }

      try {
        // Update payment method to indicate manual/offline
        await env.DB.prepare(
          `UPDATE invoices SET payment_method = 'manual', updated_at = datetime('now') WHERE id = ? AND org_id = ?`
        )
          .bind(invoiceId, session.orgId)
          .run()

        await updateInvoiceStatus(env.DB, session.orgId, invoiceId, 'paid' as InvoiceStatus)
      } catch (err) {
        console.error('[api/admin/invoices/[id]] Mark paid error:', err)
        return redirect(`${target}?error=invalid_transition`, 302)
      }

      // If this is a deposit, activate the engagement
      if (existing.type === 'deposit' && existing.engagement_id) {
        try {
          const now = new Date().toISOString()
          await env.DB.prepare(
            `UPDATE engagements SET status = 'active', start_date = ?, updated_at = ?
           WHERE id = ? AND org_id = ? AND status = 'scheduled'`
          )
            .bind(now, now, existing.engagement_id, session.orgId)
            .run()
        } catch (err) {
          console.error('[api/admin/invoices/[id]] Engagement activation error:', err)
          // Non-fatal: admin can activate manually
        }
      }

      return redirect(`${target}?saved=1`, 302)
    }

    return redirect(`${target}?error=missing`, 302)
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Action error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
