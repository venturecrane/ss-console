import type { APIContext, APIRoute } from 'astro'
import { buildPortalUrl } from '../../../../lib/config/app-url'
import {
  getInvoice,
  invoiceIsCardPayable,
  listLineItemsForInvoice,
  updateInvoice,
  updateInvoiceStatus,
} from '../../../../lib/db/invoices'
import {
  createStripeInvoice,
  finalizeStripeInvoice,
  sendStripeInvoice,
  voidStripeInvoice,
} from '../../../../lib/stripe/client'
import type { StripeCreateInvoiceParams } from '../../../../lib/stripe/types'
import { dueDateToStripeTimestamp } from '../../../../lib/stripe/due-date'
import { sendEmail } from '../../../../lib/email/resend'
import { invoiceSentEmailHtml } from '../../../../lib/email/templates'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../lib/api/helpers'

/**
 * POST /api/admin/invoices/:id
 *
 * Performs actions on an existing invoice:
 * - action=send: Creates invoice in Stripe, sends it (Stripe's hosted-invoice
 *   email + our own notification email), updates local status
 * - action=present: Creates and finalizes the invoice in Stripe with NO email
 *   from anyone. The invoice becomes payable in the portal (Billing lists it,
 *   the detail page shows Pay) and the client reads it when they next sign
 *   in. For the relationship where the client already knows the amount and
 *   will pay when directed, and an unannounced email would be noise. Same
 *   local status ('sent': the portal's visibility predicate) so both paths
 *   reveal the Billing destination identically.
 * - action=reschedule: Changes the due date of a presented or sent invoice.
 *   Stripe refuses due_date changes on a finalized invoice, so the action
 *   issues a replacement Stripe invoice (same lines, same payment method,
 *   the new due date), points the row at it, then voids the original. No
 *   email in any case: the client reads the new date in the portal and on
 *   the Stripe payment page.
 * - action=void: Voids the invoice (Stripe + local)
 * - action=mark_paid: Manual override for offline payments (OQ-008)
 *
 * Protected by auth middleware (requires admin role).
 */

type Redirect = APIContext['redirect']
type Invoice = NonNullable<Awaited<ReturnType<typeof getInvoice>>>
type LineItem = Awaited<ReturnType<typeof listLineItemsForInvoice>>[number]

type IssueMode = 'send' | 'present'

interface IssueArgs {
  redirect: Redirect
  orgId: string
  invoiceId: string
  existing: Invoice
  target: string
  mode: IssueMode
}

interface RescheduleArgs {
  redirect: Redirect
  orgId: string
  invoiceId: string
  existing: Invoice
  target: string
  dueDate: string
}

/**
 * The Stripe invoice is built from the row and its authored lines, never
 * from anything else: the same lines the portal renders, and ACH unless the
 * lines carry the 3% card-fee line (agreement §3.8), in which case card is
 * the only method. `due` is either the default terms (30 days) or an exact
 * instant when the Captain has set a due date.
 */
interface StripeInvoiceSource {
  orgId: string
  existing: Invoice
  lines: LineItem[]
  clientEmail: string
  due: { days_until_due: number } | { due_date: number }
  extraMetadata?: Record<string, string>
}

function stripeInvoiceParams(src: StripeInvoiceSource): StripeCreateInvoiceParams {
  const { orgId, existing, lines, clientEmail, due, extraMetadata } = src
  return {
    customer_email: clientEmail,
    description: existing.description ?? undefined,
    line_items: lines.map((line) => ({
      amount: line.amount_cents,
      currency: 'usd',
      description: line.description,
      quantity: 1,
    })),
    ...due,
    collection_method: 'send_invoice',
    metadata: { invoice_id: existing.id, org_id: orgId, type: existing.type, ...extraMetadata },
    payment_settings: {
      payment_method_types: invoiceIsCardPayable(lines) ? ['card'] : ['ach_debit'],
    },
  }
}

async function billingContactEmail(orgId: string, entityId: string): Promise<string | null> {
  const contact = await env.DB.prepare(
    'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
  )
    .bind(orgId, entityId)
    .first<{ email: string }>()
  return contact?.email ?? null
}

/** Best-effort notification after a SEND (never after a present). */
async function notifyClientInvoiceReady(
  orgId: string,
  existing: Invoice,
  clientEmail: string
): Promise<void> {
  try {
    const entityRow = await env.DB.prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
      .bind(existing.entity_id, orgId)
      .first<{ name: string }>()
    const formattedAmount = `$${existing.amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    const portalUrl = buildPortalUrl(env, '/portal/billing')
    await sendEmail(env.RESEND_API_KEY, {
      to: clientEmail,
      subject: 'Your invoice from SMD Services is ready',
      html: invoiceSentEmailHtml(entityRow?.name ?? 'there', formattedAmount, portalUrl),
    })
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Email send error:', err)
    // Non-fatal
  }
}

/**
 * Create the Stripe invoice from the local row and make it payable.
 *
 * `mode='send'` finalizes + emails (Stripe's hosted-invoice email, then our
 * notification); `mode='present'` finalizes only, with auto_advance off so
 * Stripe's own finalization email does not fire either. A billing contact
 * with an email is required in both modes: Stripe keys the customer by
 * email and a placeholder address would put a fabricated customer on a
 * real invoice. The Stripe invoice carries the same authored lines the
 * portal renders; the send-gate refuses an invoice with none, and this
 * checks first so Stripe never sees a line-less invoice either.
 */
async function handleIssue({
  redirect,
  orgId,
  invoiceId,
  existing,
  target,
  mode,
}: IssueArgs): Promise<Response> {
  if (existing.status !== 'draft') {
    return redirect(`${target}?error=invalid_transition`, 302)
  }
  const clientEmail = await billingContactEmail(orgId, existing.entity_id)
  if (!clientEmail) return redirect(`${target}?error=no_billing_contact`, 302)

  const lines = await listLineItemsForInvoice(env.DB, invoiceId)
  if (lines.length === 0) return redirect(`${target}?error=missing_line_items`, 302)

  try {
    const stripeResult = await createStripeInvoice(
      env.STRIPE_API_KEY,
      stripeInvoiceParams({ orgId, existing, lines, clientEmail, due: { days_until_due: 30 } })
    )
    const issued =
      mode === 'send'
        ? await sendStripeInvoice(env.STRIPE_API_KEY, stripeResult.id)
        : await finalizeStripeInvoice(env.STRIPE_API_KEY, stripeResult.id)
    await updateInvoice(env.DB, orgId, invoiceId, {
      stripe_invoice_id: stripeResult.id,
      stripe_hosted_url: issued.hosted_invoice_url,
    })
    await updateInvoiceStatus(env.DB, orgId, invoiceId, 'sent')
  } catch (err) {
    console.error(`[api/admin/invoices/[id]] Stripe ${mode} error:`, err)
    const message = err instanceof Error ? err.message : 'Stripe error'
    return redirect(`${target}?error=${encodeURIComponent(message)}`, 302)
  }

  if (mode === 'send') await notifyClientInvoiceReady(orgId, existing, clientEmail)
  return redirect(`${target}?saved=1`, 302)
}

/**
 * Change the due date of an invoice the client can already see.
 *
 * Order matters. The replacement is created and finalized first, the row
 * is repointed at it second, and the original is voided last, so at no
 * point does the row reference a voided invoice with nothing to pay. If the
 * final void fails, the client has a correct payable invoice and the
 * Captain is told the old one is still open in Stripe (`stale_stripe_invoice`),
 * which is the one state that needs a hand.
 *
 * A row with no Stripe invoice (never presented, or dev mode) just takes
 * the new date.
 */
async function handleReschedule({
  redirect,
  orgId,
  invoiceId,
  existing,
  target,
  dueDate,
}: RescheduleArgs): Promise<Response> {
  if (existing.status !== 'sent' && existing.status !== 'overdue') {
    return redirect(`${target}?error=invalid_transition`, 302)
  }
  const dueTimestamp = dueDateToStripeTimestamp(dueDate)
  if (dueTimestamp === null) return redirect(`${target}?error=invalid_due_date`, 302)

  const previousStripeId = existing.stripe_invoice_id
  if (!previousStripeId) {
    await updateInvoice(env.DB, orgId, invoiceId, { due_date: dueDate })
    return redirect(`${target}?rescheduled=1`, 302)
  }

  const clientEmail = await billingContactEmail(orgId, existing.entity_id)
  if (!clientEmail) return redirect(`${target}?error=no_billing_contact`, 302)
  const lines = await listLineItemsForInvoice(env.DB, invoiceId)
  if (lines.length === 0) return redirect(`${target}?error=missing_line_items`, 302)

  try {
    const params = stripeInvoiceParams({
      orgId,
      existing,
      lines,
      clientEmail,
      due: { due_date: dueTimestamp },
      extraMetadata: { replaces: previousStripeId },
    })
    const created = await createStripeInvoice(env.STRIPE_API_KEY, params)
    const issued = await finalizeStripeInvoice(env.STRIPE_API_KEY, created.id)
    await updateInvoice(env.DB, orgId, invoiceId, {
      due_date: dueDate,
      stripe_invoice_id: created.id,
      stripe_hosted_url: issued.hosted_invoice_url,
    })
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Stripe reschedule error:', err)
    const message = err instanceof Error ? err.message : 'Stripe error'
    return redirect(`${target}?error=${encodeURIComponent(message)}`, 302)
  }

  try {
    await voidStripeInvoice(env.STRIPE_API_KEY, previousStripeId)
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Stripe void-after-reschedule error:', err)
    return redirect(`${target}?error=stale_stripe_invoice`, 302)
  }
  return redirect(`${target}?rescheduled=1`, 302)
}

async function handleVoid(
  redirect: Redirect,
  orgId: string,
  invoiceId: string,
  existing: Invoice,
  target: string
): Promise<Response> {
  if (existing.status !== 'draft' && existing.status !== 'sent') {
    return redirect(`${target}?error=invalid_transition`, 302)
  }

  if (existing.stripe_invoice_id) {
    try {
      await voidStripeInvoice(env.STRIPE_API_KEY, existing.stripe_invoice_id)
    } catch (err) {
      console.error('[api/admin/invoices/[id]] Stripe void error:', err)
      // Continue voiding locally even if Stripe fails
    }
  }

  try {
    await updateInvoiceStatus(env.DB, orgId, invoiceId, 'void')
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Void status error:', err)
    return redirect(`${target}?error=invalid_transition`, 302)
  }

  return redirect(`${target}?saved=1`, 302)
}

async function handleMarkPaid(
  redirect: Redirect,
  orgId: string,
  invoiceId: string,
  existing: Invoice,
  target: string
): Promise<Response> {
  if (existing.status !== 'sent' && existing.status !== 'overdue') {
    return redirect(`${target}?error=invalid_transition`, 302)
  }

  try {
    await env.DB.prepare(
      `UPDATE invoices SET payment_method = 'manual', updated_at = datetime('now') WHERE id = ? AND org_id = ?`
    )
      .bind(invoiceId, orgId)
      .run()

    await updateInvoiceStatus(env.DB, orgId, invoiceId, 'paid')
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
        .bind(now, now, existing.engagement_id, orgId)
        .run()
    } catch (err) {
      console.error('[api/admin/invoices/[id]] Engagement activation error:', err)
      // Non-fatal: admin can activate manually
    }
  }

  return redirect(`${target}?saved=1`, 302)
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const invoiceId = params.id
  if (!invoiceId) {
    return errorResponse(400, 'Invoice ID required')
  }

  try {
    const existing = await getInvoice(env.DB, session.orgId, invoiceId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')
    const redirectUrl = formData.get('redirect_url')
    const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/entities'

    if (action === 'send' || action === 'present') {
      return handleIssue({
        redirect,
        orgId: session.orgId,
        invoiceId,
        existing,
        target,
        mode: action,
      })
    }

    if (action === 'reschedule') {
      const dueDate = formData.get('due_date')
      return handleReschedule({
        redirect,
        orgId: session.orgId,
        invoiceId,
        existing,
        target,
        dueDate: typeof dueDate === 'string' ? dueDate.trim() : '',
      })
    }

    if (action === 'void') {
      return handleVoid(redirect, session.orgId, invoiceId, existing, target)
    }

    if (action === 'mark_paid') {
      return handleMarkPaid(redirect, session.orgId, invoiceId, existing, target)
    }

    return redirect(`${target}?error=missing`, 302)
  } catch (err) {
    console.error('[api/admin/invoices/[id]] Action error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
