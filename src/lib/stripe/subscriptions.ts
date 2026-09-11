/**
 * Stripe subscription operations for the Operator retainer.
 *
 * The Operator is sold as a flat monthly retainer (ADR 0004 shape, ADR 0063
 * price). The retainer STARTS BY THE CLIENT'S OWN ACT (Captain, 2026-08-29):
 * a principal clicks "Start subscription" in the portal, lands on a Stripe
 * Checkout Session in subscription mode, enters a bank account (ACH Direct
 * Debit, instant verification with micro-deposit fallback) or a card, and
 * pays the first month there. Stripe charges that same method every cycle
 * (charge_automatically). The checkout.session.completed webhook
 * (operator-checkout-handler.ts) attaches the subscription to the local row
 * and promotes it to active.
 *
 * Nothing here creates a subscription on the client's behalf. The earlier
 * admin-started `send_invoice` retainer (#1679) let Stripe email the firm a
 * monthly invoice with no act by the client and no approval by the Captain;
 * it was ripped the day it was first exercised on a real client
 * (2026-08-29). A subscription exists only after the client has paid.
 *
 * Design choices that survive:
 *
 *   * **One shared Product, inline per-seat prices.** Stripe's inline
 *     `price_data` requires an existing product id, and every seat's price is
 *     authored per engagement (services.recurring_price) — so we resolve one
 *     "SMD Operator Retainer" product by metadata (create on first use) and
 *     attach a fresh inline monthly price per checkout.
 *   * **Pause = pause_collection[behavior]=void.** A paused seat (audit-only
 *     access, drafts suspended) must not accumulate charges; cycle invoices
 *     generated while paused are voided. Resume clears `pause_collection`.
 *   * **Payment failure never touches the Machine.** The webhook alerts
 *     team@smd.services and marks the local invoice overdue; whether to
 *     pause or decommission is a Captain decision under the offboarding
 *     doctrine (#1684). No imposed default.
 *   * **No Stripe email is load-bearing.** The portal is the surface; what
 *     Stripe emails (receipts, failed-payment notices) is an account setting
 *     the Captain owns, never a dependency of this flow.
 *
 * DEV-MODE PATTERN: when apiKey is undefined, log and return a mock — same
 * as client.ts / resend.ts.
 */

import { CARD_FEE_LINE_DESCRIPTION, cardProcessingFeeCents } from '../pricing/card-fee'
import type { OperatorPaymentMethod } from '../db/services'

const STRIPE_API_BASE = 'https://api.stripe.com/v1'

/** Metadata marker that identifies the shared retainer product. */
const RETAINER_PRODUCT_MARKER = { key: 'smd_product', value: 'operator-retainer' } as const
const RETAINER_PRODUCT_NAME = 'SMD Operator Retainer'
/**
 * The shared product behind the recurring card-fee line (agreement §3.8).
 * Its name is the line the client reads on Checkout and on every monthly
 * invoice: the same text the one-time invoices carry, so a card client
 * sees one phrase for one fee everywhere.
 */
const CARD_FEE_PRODUCT_MARKER = { key: 'smd_product', value: 'operator-card-fee' } as const
const CARD_FEE_PRODUCT_NAME = CARD_FEE_LINE_DESCRIPTION

/** The checkout-session metadata key + value the webhook routes on. */
export const OPERATOR_CHECKOUT_PRODUCT_SLUG = 'operator'

function stripeHeaders(apiKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${apiKey}`,
    'Content-Type': 'application/x-www-form-urlencoded',
  }
}

export interface StripeSubscriptionResult {
  id: string
  status: string
}

/**
 * Resolve a shared Product by its metadata marker; create it on first use.
 * One product per SKU (the retainer, the card fee) — per-seat prices are
 * inline.
 */
async function resolveProductId(
  apiKey: string,
  marker: { key: string; value: string },
  name: string
): Promise<string> {
  const query = `metadata['${marker.key}']:'${marker.value}'`
  const searchRes = await fetch(
    `${STRIPE_API_BASE}/products/search?query=${encodeURIComponent(query)}`,
    { method: 'GET', headers: { Authorization: `Bearer ${apiKey}` } }
  )
  if (searchRes.ok) {
    const data: { data: Array<{ id: string }> } = await searchRes.json()
    if (data.data.length > 0) return data.data[0].id
  }
  const body = new URLSearchParams()
  body.append('name', name)
  body.append(`metadata[${marker.key}]`, marker.value)
  const res = await fetch(`${STRIPE_API_BASE}/products`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe product creation failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string } = await res.json()
  return data.id
}

export interface CreateOperatorCheckoutParams {
  /** The signed-in principal's email; Stripe pre-fills it and keys the customer on it. */
  customer_email: string
  /** Monthly retainer in cents (services.recurring_price × 100). */
  monthly_amount_cents: number
  /**
   * The authored rail (services.payment_method). `ach` offers a bank account
   * and nothing else; `card` offers a card and adds the recurring 3%
   * processing-fee line (agreement §3.8) so the fee is on the page before
   * the client pays and on every monthly invoice after.
   */
  payment_method: OperatorPaymentMethod
  /** Local entity id, stamped into metadata for cross-reference. */
  entity_id: string
  /** Local subscriptions.id — the row the webhook attaches to and promotes. */
  subscription_row_id: string
  /** Local users.id of the principal who clicked Start (client_reference_id). */
  user_id: string
  /** Absolute URL; Stripe substitutes {CHECKOUT_SESSION_ID}. */
  success_url: string
  /** Absolute URL for an abandoned checkout. */
  cancel_url: string
}

export interface OperatorCheckoutResult {
  id: string
  url: string
}

/**
 * Create the Checkout Session a client uses to START the retainer.
 *
 * Subscription mode, monthly, on the rail authored for the client. ACH
 * (the default) is ACH Direct Debit only, verification automatic: instant
 * via Financial Connections, micro-deposits as fallback. Card (authored on
 * the client hub when a client asks; the first did on 2026-09-10) offers a
 * card only and carries a second recurring line, the 3% processing fee of
 * agreement §3.8, so Checkout shows the fee before payment and every
 * monthly invoice carries it as its own line. One session cannot offer both
 * rails and add the fee only for a card, which is why the rail is authored.
 * The first month is paid on Stripe's page; the subscription is created by
 * Stripe on completion, charge_automatically, and the webhook binds it.
 * No Stripe Tax: the retainer is a managed service, priced as authored.
 */
export async function createOperatorCheckoutSession(
  apiKey: string | undefined,
  params: CreateOperatorCheckoutParams
): Promise<OperatorCheckoutResult> {
  const dollars = (params.monthly_amount_cents / 100).toFixed(2)
  if (!apiKey) {
    const devId = 'dev_cs_' + crypto.randomUUID()
    console.log(`[DEV] Stripe: would create checkout for $${dollars}/mo retainer`)
    return { id: devId, url: '#dev-mode' }
  }
  const productId = await resolveProductId(apiKey, RETAINER_PRODUCT_MARKER, RETAINER_PRODUCT_NAME)

  const body = new URLSearchParams()
  body.append('mode', 'subscription')
  body.append('line_items[0][quantity]', '1')
  body.append('line_items[0][price_data][unit_amount]', String(params.monthly_amount_cents))
  body.append('line_items[0][price_data][currency]', 'usd')
  body.append('line_items[0][price_data][product]', productId)
  body.append('line_items[0][price_data][recurring][interval]', 'month')
  if (params.payment_method === 'card') {
    const feeProductId = await resolveProductId(
      apiKey,
      CARD_FEE_PRODUCT_MARKER,
      CARD_FEE_PRODUCT_NAME
    )
    body.append('line_items[1][quantity]', '1')
    body.append(
      'line_items[1][price_data][unit_amount]',
      String(cardProcessingFeeCents(params.monthly_amount_cents))
    )
    body.append('line_items[1][price_data][currency]', 'usd')
    body.append('line_items[1][price_data][product]', feeProductId)
    body.append('line_items[1][price_data][recurring][interval]', 'month')
    body.append('payment_method_types[]', 'card')
  } else {
    body.append('payment_method_types[]', 'us_bank_account')
    body.append('payment_method_options[us_bank_account][verification_method]', 'automatic')
  }
  body.append('customer_email', params.customer_email)
  body.append('client_reference_id', params.user_id)
  body.append('success_url', params.success_url)
  body.append('cancel_url', params.cancel_url)
  body.append('metadata[product_slug]', OPERATOR_CHECKOUT_PRODUCT_SLUG)
  body.append('metadata[smd_entity_id]', params.entity_id)
  body.append('metadata[smd_subscription_id]', params.subscription_row_id)
  body.append('subscription_data[description]', 'Operator retainer')
  body.append('subscription_data[metadata][product_slug]', OPERATOR_CHECKOUT_PRODUCT_SLUG)
  body.append('subscription_data[metadata][smd_entity_id]', params.entity_id)
  body.append('subscription_data[metadata][smd_subscription_id]', params.subscription_row_id)

  const res = await fetch(`${STRIPE_API_BASE}/checkout/sessions`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe checkout session creation failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string; url: string } = await res.json()
  return { id: data.id, url: data.url }
}

/**
 * Pause collection: cycle invoices generated while paused are voided — a
 * paused seat is never charged. The subscription object stays `active` in
 * Stripe's status vocabulary; `pause_collection` is the pause.
 */
export async function pauseOperatorSubscription(
  apiKey: string | undefined,
  subscriptionId: string
): Promise<StripeSubscriptionResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would pause collection on ${subscriptionId}`)
    return { id: subscriptionId, status: 'active' }
  }
  const body = new URLSearchParams()
  body.append('pause_collection[behavior]', 'void')
  const res = await fetch(`${STRIPE_API_BASE}/subscriptions/${subscriptionId}`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe pause failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string; status: string } = await res.json()
  return { id: data.id, status: data.status }
}

/**
 * Resume collection by clearing `pause_collection` (posting the bare key
 * unsets it). The dedicated /resume endpoint applies only to
 * charge_automatically subscriptions, not this send_invoice retainer.
 */
export async function resumeOperatorSubscription(
  apiKey: string | undefined,
  subscriptionId: string
): Promise<StripeSubscriptionResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would resume collection on ${subscriptionId}`)
    return { id: subscriptionId, status: 'active' }
  }
  const body = new URLSearchParams()
  body.append('pause_collection', '')
  const res = await fetch(`${STRIPE_API_BASE}/subscriptions/${subscriptionId}`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe resume failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string; status: string } = await res.json()
  return { id: data.id, status: data.status }
}

/**
 * Cancel immediately. The customer is not charged again; Stripe stops
 * automatic collection of finalized invoices. Used on decommission and by
 * the offboarding runbook (#1684).
 */
export async function cancelOperatorSubscription(
  apiKey: string | undefined,
  subscriptionId: string
): Promise<StripeSubscriptionResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would cancel subscription ${subscriptionId}`)
    return { id: subscriptionId, status: 'canceled' }
  }
  const res = await fetch(`${STRIPE_API_BASE}/subscriptions/${subscriptionId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (!res.ok) {
    throw new Error(`Stripe cancel failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string; status: string } = await res.json()
  return { id: data.id, status: data.status }
}

/** The three values Stripe documents for Checkout Session `payment_status`. */
export type CheckoutPaymentStatus = 'paid' | 'unpaid' | 'no_payment_required'

export interface OperatorCheckoutSessionView {
  id: string
  payment_status: CheckoutPaymentStatus
  /**
   * The console row `createOperatorCheckoutSession` stamped on the session
   * (`metadata[smd_subscription_id]`); null when the session carries none.
   * The caller resolves it against rows the signed-in client owns.
   */
  smd_subscription_id: string | null
}

function isCheckoutPaymentStatus(value: unknown): value is CheckoutPaymentStatus {
  return value === 'paid' || value === 'unpaid' || value === 'no_payment_required'
}

/**
 * Read the Checkout Session the success URL names, for the Billing surface
 * (the `?start=done&session_id=…` return). Parsed, not cast: an unknown
 * `payment_status` is an error, never a display state. Never returns
 * payment details.
 */
export async function getOperatorCheckoutSession(
  apiKey: string | undefined,
  sessionId: string
): Promise<OperatorCheckoutSessionView> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would get checkout session ${sessionId}`)
    return { id: sessionId, payment_status: 'paid', smd_subscription_id: null }
  }
  const res = await fetch(`${STRIPE_API_BASE}/checkout/sessions/${sessionId}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (!res.ok) {
    throw new Error(`Stripe checkout session get failed ${res.status}: ${await res.text()}`)
  }
  const data: unknown = await res.json()
  if (!data || typeof data !== 'object') {
    throw new Error('Stripe checkout session get: response is not an object')
  }
  const obj = data as Record<string, unknown>
  if (typeof obj.id !== 'string' || !isCheckoutPaymentStatus(obj.payment_status)) {
    throw new Error('Stripe checkout session get: unexpected shape')
  }
  const metadata =
    obj.metadata && typeof obj.metadata === 'object'
      ? (obj.metadata as Record<string, unknown>)
      : null
  const rowId = metadata?.smd_subscription_id
  return {
    id: obj.id,
    payment_status: obj.payment_status,
    smd_subscription_id: typeof rowId === 'string' && rowId.length > 0 ? rowId : null,
  }
}
