/**
 * Stripe webhook handling for Operator retainer subscriptions (#1679).
 *
 * The legacy handler (stripe-handler.ts) matches events to PRE-EXISTING
 * local invoice rows — the one-time deposit/completion/milestone flow where
 * the console created the invoice first. Subscription cycle invoices invert
 * that: STRIPE originates them monthly, so this module mirrors them into the
 * local `invoices` table as `type='retainer'` rows (the CHECK constraint has
 * carried the type since the schema was authored; it goes live here), keyed
 * to the customer via `subscriptions.stripe_subscription_id` (migration
 * 0084).
 *
 * Dispatch contract with the route: an invoice event with a subscription
 * linkage is handled HERE; without one it belongs to the legacy handler.
 * Retainer invoices are never console-originated, so linkage splits the two
 * flows exactly.
 *
 * Payment-failure posture: mark the local row `overdue` and alert
 * team@smd.services. NOTHING here touches the customer's Machine — whether
 * a delinquent seat gets paused or decommissioned is a Captain decision
 * under the offboarding doctrine (#1684), never a webhook side effect.
 *
 * Same two-phase discipline as the legacy handler: Phase 1 D1 writes decide
 * the response; Phase 2 emails are best-effort.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { sendEmail } from '../email/resend'
import { paymentConfirmationEmailHtml } from '../email/templates'
import {
  activateOperatorSubscriptionForBilling,
  attachStripeSubscription,
  getSubscriptionById,
  getSubscriptionByStripeId,
  type SubscriptionBillingRow,
} from '../db/subscriptions'

import {
  ALERT_EMAIL,
  alertTeam,
  entityName,
  ok,
  serverError,
  unixToIso,
} from './stripe-subscription-shared'

/**
 * Whether an invoice belongs to a subscription, and — when it does not read
 * cleanly — the fact that we could not tell.
 *
 * `unlinked` and `unrecognized` were the same value (`null`) until ss#2315,
 * and conflating them is how a Stripe API change becomes a missing invoice
 * instead of an error. The dispatcher routes on this, so `unlinked` sends the
 * event to the legacy one-time flow and `unrecognized` must never do that.
 */
export type StripeSubscriptionLinkage =
  | { kind: 'linked'; subscriptionId: string }
  | { kind: 'unlinked' }
  | { kind: 'unrecognized'; reason: string }

/** A linkage field holds either the id or the expanded object it refers to. */
function readSubscriptionRef(value: unknown): { id: string } | { unreadable: true } | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') return value.length > 0 ? { id: value } : null
  if (typeof value === 'object') {
    const id = (value as Record<string, unknown>)['id']
    if (typeof id === 'string' && id.length > 0) return { id }
  }
  return { unreadable: true }
}

/**
 * Resolve an invoice payload's subscription linkage.
 *
 * Stripe has moved this field once already: older API versions carry a
 * top-level `subscription`, newer ones nest it under
 * `parent.subscription_details.subscription`. Either position may hold a bare
 * id or — because both are expandable — the expanded Subscription object.
 * This client pins no `Stripe-Version` header, so every one of those must
 * parse (parse, never cast).
 *
 * The point of the third state is the move we have not seen yet. Detection is
 * deliberately conservative: it fires only on a POSITIVE signal that this is a
 * subscription invoice, so a genuine one-time console-originated invoice is
 * never mistaken for a broken one. `billing_reason` is the signal that
 * survives a field move — whatever Stripe does to the linkage next, a cycle
 * invoice still says why it was billed.
 */
export function resolveStripeSubscriptionLinkage(invoice: unknown): StripeSubscriptionLinkage {
  if (typeof invoice !== 'object' || invoice === null) return { kind: 'unlinked' }
  const obj = invoice as Record<string, unknown>

  const direct = readSubscriptionRef(obj['subscription'])
  if (direct && 'id' in direct) return { kind: 'linked', subscriptionId: direct.id }

  const parentObj = asRecord(obj['parent'])
  const detailsObj = asRecord(parentObj?.['subscription_details'])
  const nested = readSubscriptionRef(detailsObj?.['subscription'])
  if (nested && 'id' in nested) return { kind: 'linked', subscriptionId: nested.id }

  const signal = subscriptionSignal(obj, {
    directPresent: direct !== null,
    detailsPresent: detailsObj !== null,
    parent: parentObj,
  })
  return signal === null ? { kind: 'unlinked' } : { kind: 'unrecognized', reason: signal }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null
}

/**
 * Why this invoice looks like a subscription invoice despite carrying no
 * readable linkage, or null when it simply is not one.
 *
 * Ordered most-durable first. `billing_reason` is checked ahead of the linkage
 * fields because it is the signal that survives the field moving again.
 */
function subscriptionSignal(
  obj: Record<string, unknown>,
  found: {
    directPresent: boolean
    detailsPresent: boolean
    parent: Record<string, unknown> | null
  }
): string | null {
  const billingReason = obj['billing_reason']
  if (typeof billingReason === 'string' && billingReason.startsWith('subscription')) {
    return `billing_reason=${billingReason} with no readable id`
  }
  if (found.directPresent) return 'subscription field present but unreadable'
  if (found.detailsPresent) return 'parent.subscription_details present but unreadable'
  if (found.parent?.['type'] === 'subscription_details') {
    return 'parent.type=subscription_details with no details'
  }
  return null
}

/**
 * An invoice signalled a subscription linkage this code cannot read.
 *
 * Loud on three surfaces, because each catches a different reader: an error
 * log (Sentry), an operational alert to the firm, and a non-2xx so the
 * delivery shows as FAILED in Stripe's own dashboard. Acking would leave a
 * retainer invoice unmirrored with nothing anywhere saying so.
 *
 * The 500 makes Stripe retry a request that cannot succeed. That is the
 * intent: a repeatedly failing endpoint is a signal Stripe raises on its own,
 * and the alternative — a quiet 200 — is the defect. Retries stop when the
 * shape is handled and the endpoint is redeployed.
 */
export async function handleUnrecognizedInvoiceLinkage(
  resendApiKey: string | undefined,
  eventType: string,
  invoiceId: string,
  reason: string
): Promise<Response> {
  console.error(
    `[stripe-subscription] UNRECOGNIZED subscription linkage on ${eventType} for invoice ${invoiceId}: ${reason}. Invoice NOT mirrored.`
  )
  try {
    await sendEmail(resendApiKey, {
      to: ALERT_EMAIL,
      subject: `Stripe webhook: unreadable subscription linkage (${invoiceId})`,
      html:
        `<p>A Stripe invoice event carried a subscription linkage this console could not read, so the invoice was <strong>not</strong> mirrored into the local <code>invoices</code> table.</p>` +
        `<ul><li>Event: ${eventType}</li>` +
        `<li>Stripe invoice: ${invoiceId}</li>` +
        `<li>Reason: ${reason}</li></ul>` +
        `<p>Most likely cause: the Stripe API version serving this webhook endpoint moved the linkage field again. Compare the raw event in the Stripe dashboard against <code>resolveStripeSubscriptionLinkage</code> in <code>src/lib/webhooks/stripe-subscription-handler.ts</code>.</p>` +
        `<p>The endpoint returned 500, so Stripe will retry and the delivery will show as failed until the shape is handled.</p>`,
    })
  } catch (err) {
    console.error('[stripe-subscription] unrecognized-linkage alert failed:', err)
  }
  return serverError()
}

/** The subscription-metadata snapshot Stripe stamps on every invoice a
 * subscription generates. Either position, by API version (see
 * {@link readInvoiceSubscriptionMetadata}). */
type InvoiceSubscriptionDetails = { metadata?: Record<string, string> | null } | null

/** The invoice-payload fields the retainer mirror consumes. */
export interface RetainerInvoicePayload {
  id: string
  amount_due: number
  amount_paid: number
  hosted_invoice_url: string | null
  due_date: number | null
  status_transitions: { paid_at: number | null }
  /** The Stripe customer id; recorded on the row by the ordering fallback. */
  customer?: string | null
  /** Pre-basil position of the subscription-metadata snapshot. */
  subscription_details?: InvoiceSubscriptionDetails
  /** 2025-03-31.basil+ position of the same snapshot. */
  parent?: { subscription_details?: InvoiceSubscriptionDetails } | null
}

/**
 * The `smd_subscription_id` the checkout stamped on the Stripe subscription
 * (`subscription_data[metadata]`, src/lib/stripe/subscriptions.ts), as it
 * rides on the invoice.
 *
 * Stripe snapshots subscription metadata onto each invoice the subscription
 * generates. Pre-basil the snapshot is `invoice.subscription_details.metadata`;
 * from 2025-03-31.basil it is `invoice.parent.subscription_details.metadata`
 * (docs.stripe.com/api/invoices/object, "parent.subscription_details.metadata:
 * Set of key-value pairs defined as subscription metadata when an invoice is
 * created"). The prod endpoint pins 2026-03-25.dahlia, so the live payload
 * carries the nested form; both are read because the client itself pins no
 * version and the linkage resolver above already reads both positions.
 */
export function readInvoiceSubscriptionMetadata(
  invoice: Pick<RetainerInvoicePayload, 'subscription_details' | 'parent'>
): Record<string, string> | null {
  return (
    invoice.parent?.subscription_details?.metadata ?? invoice.subscription_details?.metadata ?? null
  )
}

async function getLocalRetainerInvoice(
  db: D1Database,
  stripeInvoiceId: string
): Promise<{ id: string; org_id: string; status: string } | null> {
  const row = await db
    .prepare('SELECT id, org_id, status FROM invoices WHERE stripe_invoice_id = ?')
    .bind(stripeInvoiceId)
    .first<{ id: string; org_id: string; status: string }>()
  return row ?? null
}

/**
 * Mirror a finalized cycle invoice as a local `retainer` row (status
 * `sent` — Stripe emails the hosted invoice itself on finalization). Runs
 * before payment, so the portal shows the open invoice the client received.
 * Idempotent: an existing mirror row is refreshed, never duplicated.
 */
export async function handleRetainerInvoiceFinalized(
  db: D1Database,
  stripeSubscriptionId: string,
  invoice: RetainerInvoicePayload
): Promise<Response> {
  const sub = await getSubscriptionByStripeId(db, stripeSubscriptionId)
  if (!sub) {
    // A Stripe subscription we did not attach (e.g. a smoke test) — honest skip.
    console.log(
      `[stripe-subscription] No local subscription for ${stripeSubscriptionId}; skipping invoice ${invoice.id}`
    )
    return ok()
  }

  try {
    const existing = await getLocalRetainerInvoice(db, invoice.id)
    const now = new Date().toISOString()
    const amount = invoice.amount_due / 100
    const dueDate = unixToIso(invoice.due_date)

    if (existing) {
      await db
        .prepare(
          `UPDATE invoices SET amount = ?, due_date = ?, stripe_hosted_url = ?, sent_at = COALESCE(sent_at, ?), updated_at = ?
           WHERE id = ? AND status IN ('draft', 'sent')`
        )
        .bind(amount, dueDate, invoice.hosted_invoice_url, now, now, existing.id)
        .run()
      return ok()
    }

    await db
      .prepare(
        `INSERT INTO invoices (id, org_id, entity_id, type, amount, description, status,
                               stripe_invoice_id, stripe_hosted_url, due_date, sent_at, created_at, updated_at)
         VALUES (?, ?, ?, 'retainer', ?, ?, 'sent', ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        crypto.randomUUID(),
        sub.org_id,
        sub.entity_id,
        amount,
        'Operator retainer — monthly',
        invoice.id,
        invoice.hosted_invoice_url,
        dueDate,
        now,
        now,
        now
      )
      .run()
    return ok()
  } catch (err) {
    console.error('[stripe-subscription] finalized mirror failed:', err)
    return serverError() // let Stripe retry
  }
}

/**
 * Event-ordering fallback for the first paid invoice (A1, claims-2026-09-04).
 *
 * Stripe does not order `checkout.session.completed` ahead of the first
 * `invoice.paid`; when the invoice arrives first, no local row carries the
 * Stripe subscription id yet and the paid invoice would be skipped as
 * "unknown subscription" — money landed and the client's portal never went
 * live. The subscription metadata the checkout stamped names the row, so
 * bind it here: attach the Stripe subscription + customer and promote the
 * row, exactly what the checkout handler would have done. Only an operator
 * row still in `provisioning` with `stripe_subscription_id IS NULL`
 * qualifies (the checks are here, not in `attachStripeSubscription`, which
 * stays an unguarded UPDATE for the checkout handler's own retry); anything
 * else is still an honest skip.
 *
 * Runs from `invoice.paid` only: `finalized` precedes ACH collection and
 * `payment_failed` is not a go-live act.
 */
async function bindSubscriptionFromInvoiceMetadata(
  db: D1Database,
  stripeSubscriptionId: string,
  invoice: RetainerInvoicePayload
): Promise<SubscriptionBillingRow | null> {
  const rowId = readInvoiceSubscriptionMetadata(invoice)?.['smd_subscription_id']
  if (!rowId) return null
  const row = await getSubscriptionById(db, rowId)
  if (
    !row ||
    row.product_slug !== 'operator' ||
    row.status !== 'provisioning' ||
    row.stripe_subscription_id !== null
  ) {
    return null
  }
  await attachStripeSubscription(db, row.id, stripeSubscriptionId, invoice.customer ?? null)
  const promoted = await activateOperatorSubscriptionForBilling(db, row.id)
  console.log(
    `[stripe-subscription] invoice.paid arrived before checkout completion; bound ${stripeSubscriptionId} to row ${row.id} from invoice metadata (promoted=${promoted})`
  )
  return {
    ...row,
    status: promoted ? 'active' : row.status,
    stripe_subscription_id: stripeSubscriptionId,
  }
}

/** Phase-1 write for a paid cycle invoice: refresh the existing mirror row
 * (`existingId`) or insert one already `paid`. */
async function mirrorPaidInvoice(
  db: D1Database,
  sub: SubscriptionBillingRow,
  invoice: RetainerInvoicePayload,
  amount: number,
  existingId: string | null
): Promise<void> {
  const paidAt = unixToIso(invoice.status_transitions.paid_at) ?? new Date().toISOString()
  const now = new Date().toISOString()
  if (existingId !== null) {
    await db
      .prepare(
        `UPDATE invoices SET status = 'paid', amount = ?, paid_at = ?, payment_method = 'stripe',
                             stripe_hosted_url = COALESCE(?, stripe_hosted_url), updated_at = ?
         WHERE id = ?`
      )
      .bind(amount, paidAt, invoice.hosted_invoice_url, now, existingId)
      .run()
    return
  }
  await db
    .prepare(
      `INSERT INTO invoices (id, org_id, entity_id, type, amount, description, status,
                             stripe_invoice_id, stripe_hosted_url, due_date, sent_at, paid_at, payment_method,
                             created_at, updated_at)
       VALUES (?, ?, ?, 'retainer', ?, ?, 'paid', ?, ?, ?, ?, ?, 'stripe', ?, ?)`
    )
    .bind(
      crypto.randomUUID(),
      sub.org_id,
      sub.entity_id,
      amount,
      'Operator retainer — monthly',
      invoice.id,
      invoice.hosted_invoice_url,
      unixToIso(invoice.due_date),
      now,
      paidAt,
      now,
      now
    )
    .run()
}

/**
 * Mark a cycle invoice paid. Upserts (a paid event may arrive without the
 * finalized mirror having landed), then sends the same confirmation email
 * the one-time flow sends. Idempotent on the paid state.
 */
export async function handleRetainerInvoicePaid(
  db: D1Database,
  resendApiKey: string | undefined,
  stripeSubscriptionId: string,
  invoice: RetainerInvoicePayload
): Promise<Response> {
  let sub = await getSubscriptionByStripeId(db, stripeSubscriptionId)
  if (!sub) {
    try {
      sub = await bindSubscriptionFromInvoiceMetadata(db, stripeSubscriptionId, invoice)
    } catch (err) {
      console.error('[stripe-subscription] ordering-fallback bind failed:', err)
      return serverError() // let Stripe retry
    }
  }
  if (!sub) {
    console.log(
      `[stripe-subscription] No local subscription for ${stripeSubscriptionId}; skipping paid invoice ${invoice.id}`
    )
    return ok()
  }

  const amount = (invoice.amount_paid > 0 ? invoice.amount_paid : invoice.amount_due) / 100

  try {
    const existing = await getLocalRetainerInvoice(db, invoice.id)
    if (existing?.status === 'paid') return ok() // idempotency guard
    await mirrorPaidInvoice(db, sub, invoice, amount, existing?.id ?? null)
    // A paid invoice on an operator row still in `provisioning` is the ACH
    // first payment settling (the checkout completed `unpaid` and attached
    // without promoting — see operator-checkout-handler.ts). The money is
    // the act, so go live here as well as on async_payment_succeeded: the
    // two signals converge, and neither has to arrive first.
    if (sub.product_slug === 'operator' && sub.status === 'provisioning') {
      await activateOperatorSubscriptionForBilling(db, sub.id)
    }
  } catch (err) {
    console.error('[stripe-subscription] paid mirror failed:', err)
    return serverError() // let Stripe retry
  }

  // Phase 2: emails, best-effort. The client is thanked; team@ is told the
  // money landed, so revenue is observed rather than discovered later in
  // Stripe.
  await sendRetainerConfirmationEmail(db, resendApiKey, sub, amount)
  const formatted = `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const name = await entityName(db, sub)
  await alertTeam(
    resendApiKey,
    `Retainer payment received — ${name}, ${formatted}`,
    `<p>A retainer cycle invoice was paid.</p>` +
      `<ul><li>Customer: ${name}</li><li>Amount: ${formatted}</li>` +
      `<li>Stripe invoice: ${invoice.id}</li>` +
      `<li>Stripe subscription: ${stripeSubscriptionId}</li></ul>`
  )
  return ok()
}

/** Phase-2 side effect for a paid cycle invoice: the same confirmation email
 * the one-time flow sends. Best-effort — never turns a mirrored payment into
 * a webhook failure. */
async function sendRetainerConfirmationEmail(
  db: D1Database,
  resendApiKey: string | undefined,
  sub: SubscriptionBillingRow,
  amount: number
): Promise<void> {
  try {
    const contact = await db
      .prepare(
        'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
      )
      .bind(sub.org_id, sub.entity_id)
      .first<{ email: string }>()
    if (!contact?.email) return
    const entity = await db
      .prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
      .bind(sub.entity_id, sub.org_id)
      .first<{ name: string }>()
    const formatted = `$${amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    await sendEmail(resendApiKey, {
      to: contact.email,
      subject: 'Payment received — thank you',
      html: paymentConfirmationEmailHtml(entity?.name ?? 'there', formatted),
    })
  } catch (err) {
    console.error('[stripe-subscription] confirmation email failed:', err)
  }
}

/**
 * A cycle invoice's payment failed. Mark the local mirror `overdue` and
 * alert the firm. Deliberately does NOT touch the subscription row, the
 * portal gate, or the customer's Machine (#1684 owns that ladder).
 */
export async function handleRetainerInvoicePaymentFailed(
  db: D1Database,
  resendApiKey: string | undefined,
  stripeSubscriptionId: string,
  invoice: RetainerInvoicePayload
): Promise<Response> {
  const sub = await getSubscriptionByStripeId(db, stripeSubscriptionId)
  if (!sub) return ok()

  try {
    await db
      .prepare(
        `UPDATE invoices SET status = 'overdue', updated_at = datetime('now')
         WHERE stripe_invoice_id = ? AND status IN ('sent', 'draft')`
      )
      .bind(invoice.id)
      .run()
  } catch (err) {
    console.error('[stripe-subscription] overdue update failed:', err)
    return serverError()
  }

  try {
    const entity = await db
      .prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
      .bind(sub.entity_id, sub.org_id)
      .first<{ name: string }>()
    const amount = (invoice.amount_due / 100).toFixed(2)
    await sendEmail(resendApiKey, {
      to: ALERT_EMAIL,
      subject: `Retainer payment failed — ${entity?.name ?? sub.entity_id}`,
      html:
        `<p>A retainer cycle invoice payment failed.</p>` +
        `<ul><li>Customer: ${entity?.name ?? sub.entity_id}</li>` +
        `<li>Amount: $${amount}</li>` +
        `<li>Stripe invoice: ${invoice.id}</li>` +
        `<li>Stripe subscription: ${stripeSubscriptionId}</li></ul>` +
        `<p>No automatic action was taken. Pause/decommission is a Captain decision (offboarding doctrine, ss-console#1684).</p>`,
    })
  } catch (err) {
    console.error('[stripe-subscription] failure alert email failed:', err)
  }

  return ok()
}
