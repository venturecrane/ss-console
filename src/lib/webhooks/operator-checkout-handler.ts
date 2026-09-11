/**
 * checkout.session.* handling for the Operator retainer.
 *
 * The client's own act starts the retainer (Captain, 2026-08-29): a portal
 * principal clicked Start, paid the first month on Stripe's page, and Stripe
 * created the subscription. This handler binds that subscription to the
 * local row the session named in metadata (`smd_subscription_id`), records
 * the Stripe customer so the Manage Billing door renders, and promotes the
 * row from `provisioning` to `active` — the go-live flip that reveals the
 * full portal.
 *
 * Three events, one payment_status gate (A2, claims-2026-09-04). The
 * checkout offers ACH as well as card, and ACH settles days after
 * `checkout.session.completed` fires with `payment_status: 'unpaid'`
 * (docs.stripe.com/api/checkout/sessions/object: paid | unpaid |
 * no_payment_required — "unpaid: the payment funds are not yet available in
 * your account"). So:
 *
 *   * `completed`               — attach always (so later events bind);
 *                                 promote only when the session is not
 *                                 `unpaid`.
 *   * `async_payment_succeeded` — the ACH settled; same handler, promote.
 *   * `async_payment_failed`    — the ACH bounced; cancel the Stripe
 *                                 subscription, detach it from the row so the
 *                                 client can start again, alert team@.
 *
 * Promotion has one other path: `invoice.paid` in
 * stripe-subscription-handler.ts, which binds from the invoice's subscription
 * metadata when the first invoice lands before `completed` does, and promotes
 * an attached-but-provisioning row when the ACH first payment settles. The
 * two settlement signals converge; neither has to arrive first.
 *
 * Unlike the Hosted Agent handler it creates nothing: the operator's
 * subscription row exists from provisioning (the seat was stood up before
 * the client ever saw Billing), so a session that names a row this org
 * does not have is recorded as failed and left for a human, never invented.
 *
 * Idempotency: the `stripe_checkout_orders` ledger keyed by session id, the
 * same discipline as the Hosted Agent handler. Phase 1 D1 writes decide the
 * response (500 → Stripe retries); there is no client email here — the
 * portal is the surface, and what Stripe emails is an account setting.
 *
 * One status value, two causes. `failed` on the ledger is written by two
 * paths with opposite contracts: the UNBOUND session ("a human reconciles",
 * migration 0087 — a replay after the binding is fixed must re-run the
 * pipeline) and the BOUNCED payment (the subscription is cancelled and must
 * never be re-attached). They are told apart by the ledger row's
 * `stripe_subscription_id`: the bounced path records the cancelled
 * subscription there, the unbound path clears it (nothing was bound). No
 * migration; the column already exists.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  activateOperatorSubscriptionForBilling,
  attachStripeSubscription,
  detachStripeSubscription,
} from '../db/subscriptions'
import { OPERATOR_CHECKOUT_PRODUCT_SLUG, cancelOperatorSubscription } from '../stripe/subscriptions'
import { alertTeam } from './stripe-subscription-handler'

function ok(): Response {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function serverError(): Response {
  return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
    status: 500,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Stripe's Checkout Session `payment_status` vocabulary. */
export type CheckoutPaymentStatus = 'paid' | 'unpaid' | 'no_payment_required'

/** The session-payload fields this handler consumes. */
export interface OperatorCheckoutSessionPayload {
  id: string
  client_reference_id: string | null
  customer: string | null
  subscription: string | null
  amount_total: number | null
  customer_details: { email: string | null; name: string | null } | null
  metadata: Record<string, string>
  payment_status: CheckoutPaymentStatus
}

/** The ledger columns the two handlers decide on. */
interface OrderRow {
  status: string
  stripe_subscription_id: string | null
}

/** The session's ledger row, inserted `received` when this is its first event. */
async function recordOrder(
  db: D1Database,
  payload: OperatorCheckoutSessionPayload
): Promise<OrderRow> {
  const existing = await db
    .prepare(
      'SELECT status, stripe_subscription_id FROM stripe_checkout_orders WHERE session_id = ?'
    )
    .bind(payload.id)
    .first<OrderRow>()
  if (existing) return existing
  await db
    .prepare(
      `INSERT OR IGNORE INTO stripe_checkout_orders
         (session_id, clerk_user_id, email, stripe_customer_id, stripe_subscription_id,
          product_slug, plan, amount_total, status)
       VALUES (?, ?, ?, ?, ?, ?, 'standard', ?, 'received')`
    )
    .bind(
      payload.id,
      payload.client_reference_id,
      payload.customer_details?.email ?? null,
      payload.customer,
      payload.subscription,
      OPERATOR_CHECKOUT_PRODUCT_SLUG,
      payload.amount_total
    )
    .run()
  return { status: 'received', stripe_subscription_id: payload.subscription }
}

async function markOrderProcessed(db: D1Database, sessionId: string): Promise<void> {
  await db
    .prepare(
      "UPDATE stripe_checkout_orders SET status = 'processed', updated_at = datetime('now') WHERE session_id = ?"
    )
    .bind(sessionId)
    .run()
}

/**
 * Mark the order `failed` and record WHY on `stripe_subscription_id`: the
 * cancelled subscription (bounced payment) or NULL (nothing was bound).
 * Returns true only when the row actually changed, so a redelivery that
 * finds the same failure already recorded reads as a no-op. `IS` rather than
 * `=` because the recorded value may be NULL.
 */
async function markOrderFailed(
  db: D1Database,
  sessionId: string,
  cancelledSubscription: string | null
): Promise<boolean> {
  const res = await db
    .prepare(
      `UPDATE stripe_checkout_orders
          SET status = 'failed', stripe_subscription_id = ?, updated_at = datetime('now')
        WHERE session_id = ? AND NOT (status = 'failed' AND stripe_subscription_id IS ?)`
    )
    .bind(cancelledSubscription, sessionId, cancelledSubscription)
    .run()
  return (res.meta.changes ?? 0) > 0
}

/**
 * Whether a `completed` / `async_payment_succeeded` delivery has nothing left
 * to do. `processed` always. `failed` only when it is the BOUNCED cause —
 * the ledger names the very subscription this payload carries as cancelled,
 * so re-attaching it would bind the row to a dead subscription. An unbound
 * failure (NULL) is not terminal: after a human fixes the binding, a replay
 * must re-run the pipeline (migration 0087's contract).
 */
function completionIsTerminal(order: OrderRow, payload: OperatorCheckoutSessionPayload): boolean {
  if (order.status === 'processed') return true
  return (
    order.status === 'failed' &&
    order.stripe_subscription_id !== null &&
    order.stripe_subscription_id === payload.subscription
  )
}

/** The operator row the session names, or null when it names none this
 * console holds. */
async function findNamedRow(
  db: D1Database,
  payload: OperatorCheckoutSessionPayload
): Promise<{ id: string } | null> {
  const rowId = payload.metadata['smd_subscription_id']
  if (!rowId) return null
  const row = await db
    .prepare("SELECT id FROM subscriptions WHERE id = ? AND product_slug = 'operator'")
    .bind(rowId)
    .first<{ id: string }>()
  return row ?? null
}

/**
 * `checkout.session.completed` and `checkout.session.async_payment_succeeded`.
 *
 * Attaches on both, so every later Stripe event for the subscription finds
 * the row. Promotes only when the payment is not still pending: a card
 * session completes `paid`; an ACH session completes `unpaid` and is
 * promoted by the later `async_payment_succeeded` (the ledger row stays
 * `received` until then, which is what lets that event through). The order
 * row is marked `processed` only once the row is promoted.
 */
export async function handleOperatorCheckoutCompleted(
  db: D1Database,
  payload: OperatorCheckoutSessionPayload
): Promise<Response> {
  if (payload.metadata['product_slug'] !== OPERATOR_CHECKOUT_PRODUCT_SLUG) return ok()

  try {
    const order = await recordOrder(db, payload)
    if (completionIsTerminal(order, payload)) return ok()

    const row = await findNamedRow(db, payload)
    if (!row || !payload.subscription) {
      // A paid session the console cannot bind: never 500-loop Stripe over
      // it; record it failed so a human reconciles, and say so in the log.
      console.error(
        '[operator-checkout] unbound session',
        payload.id,
        payload.metadata['smd_subscription_id'],
        payload.subscription
      )
      await markOrderFailed(db, payload.id, null)
      return ok()
    }

    await attachStripeSubscription(db, row.id, payload.subscription, payload.customer)
    if (payload.payment_status === 'unpaid') {
      // Delayed payment method (ACH). Bound, not live: the row stays
      // provisioning until async_payment_succeeded says the funds landed.
      console.log('[operator-checkout] session completed unpaid; awaiting settlement', payload.id)
      return ok()
    }
    await activateOperatorSubscriptionForBilling(db, row.id)
    await markOrderProcessed(db, payload.id)
    return ok()
  } catch (err) {
    console.error('[operator-checkout] pipeline failed:', err)
    return serverError() // let Stripe retry
  }
}

/**
 * `checkout.session.async_payment_failed`: the delayed first payment bounced.
 *
 * Order of operations is the whole design:
 *
 *   1. Detach the subscription from the row and mark the order `failed`
 *      with the cancelled subscription recorded (D1, Phase 1). Detaching is
 *      what lets the client retry — both start gates require
 *      `stripe_subscription_id IS NULL` — and doing it BEFORE the Stripe
 *      cancel means the `customer.subscription.deleted` that the cancel
 *      provokes misses the row (handleSubscriptionLifecycle skips unknown
 *      subscriptions) instead of cancelling a provisioning row the client is
 *      about to reuse.
 *   2. Only if either write actually transitioned: cancel the subscription
 *      at Stripe (`DELETE /v1/subscriptions/{id}` — without it Stripe keeps
 *      a live subscription, and keeps invoicing it, that the console has
 *      just forgotten) and alert team@. Edge-triggered off the stored state,
 *      the same discipline as the cancellation alerts (#2635): Stripe
 *      redelivers, and a redelivery that finds the failure already recorded
 *      is a silent 200 — no second email, no second cancel.
 *
 * A cancel that fails is not retried by redelivery (that would need state
 * this ledger does not hold), so it is alerted ONCE, naming the error and
 * the dashboard action, and acked: a 500 here would only produce silent
 * redeliveries.
 *
 * The row's status is untouched: it was never promoted, so it is still
 * `provisioning`, which with the cleared id is exactly "startable".
 */
export async function handleOperatorCheckoutAsyncPaymentFailed(
  db: D1Database,
  stripeApiKey: string | undefined,
  resendApiKey: string | undefined,
  payload: OperatorCheckoutSessionPayload
): Promise<Response> {
  if (payload.metadata['product_slug'] !== OPERATOR_CHECKOUT_PRODUCT_SLUG) return ok()

  let transition: { detached: boolean; marked: boolean } | null
  try {
    transition = await recordFailedPayment(db, payload)
  } catch (err) {
    console.error('[operator-checkout] async_payment_failed pipeline failed:', err)
    return serverError() // let Stripe retry
  }
  if (transition === null) return ok() // already processed: nothing to unwind
  if (!transition.detached && !transition.marked) {
    console.log('[operator-checkout] async_payment_failed already recorded; redelivery', payload.id)
    return ok()
  }

  let cancelError: string | null = null
  if (payload.subscription) {
    try {
      await cancelOperatorSubscription(stripeApiKey, payload.subscription)
    } catch (err) {
      cancelError = err instanceof Error ? err.message : String(err)
      console.error('[operator-checkout] cancel after failed payment failed:', err)
    }
  }

  await alertTeam(
    resendApiKey,
    `Operator retainer first payment FAILED — session ${payload.id}`,
    failedPaymentAlertHtml(payload, transition.detached, cancelError)
  )
  return ok()
}

/** Phase 1 of the bounced payment: detach the row and record the failure.
 * Returns which of the two writes actually changed something (the edge), or
 * null when the order was already processed and there is nothing to unwind. */
async function recordFailedPayment(
  db: D1Database,
  payload: OperatorCheckoutSessionPayload
): Promise<{ detached: boolean; marked: boolean } | null> {
  const order = await recordOrder(db, payload)
  if (order.status === 'processed') return null
  const row = await findNamedRow(db, payload)
  const detached =
    row && payload.subscription
      ? await detachStripeSubscription(db, row.id, payload.subscription)
      : false
  const marked = await markOrderFailed(db, payload.id, payload.subscription)
  return { detached, marked }
}

function failedPaymentAlertHtml(
  payload: OperatorCheckoutSessionPayload,
  detached: boolean,
  cancelError: string | null
): string {
  return (
    `<p>The delayed first payment on an Operator retainer checkout failed at Stripe.</p>` +
    `<ul><li>Checkout session: ${payload.id}</li>` +
    `<li>Stripe subscription: ${payload.subscription ?? 'none'}</li>` +
    `<li>Local row: ${payload.metadata['smd_subscription_id'] ?? 'not named'}` +
    (detached ? ' (detached; the client can start again from Billing)' : ' (nothing to detach)') +
    `</li>` +
    `<li>Stripe cancel: ${cancelError === null ? 'done' : `FAILED — ${cancelError}`}</li></ul>` +
    (cancelError === null
      ? `<p>No further action is needed unless the client asks.</p>`
      : `<p>The subscription is still live at Stripe and will keep invoicing. This alert is sent once; cancel it in the Stripe dashboard.</p>`)
  )
}
