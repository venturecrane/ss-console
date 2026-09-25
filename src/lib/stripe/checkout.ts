/**
 * Stripe Checkout operations for the Hosted Agent SKU (ADR 0067).
 *
 * The Hosted Agent sells self-serve: the buyer hits a hosted Stripe Checkout
 * page (card entry happens on Stripe's surface, never ours) in subscription
 * mode. Design choices, mirroring the retainer engine (subscriptions.ts):
 *
 *   * **Raw REST, no SDK** — same as client.ts.
 *   * **Resolve-or-create product + coupon by fixed identity.** No dashboard
 *     prerequisites: the shared "SMD Hosted Agent" product is resolved by a
 *     metadata marker (created on first use), and the founding-seat coupon is
 *     created with a FIXED id (`hosted-agent-founding`) so Stripe enforces
 *     both the 25-redemption cap and the for-life ($30 off forever) discount
 *     atomically. Test and live mode each materialize their own objects.
 *   * **Coupon-exhausted fallback.** Session creation is attempted WITH the
 *     founding discount; when Stripe rejects the coupon (cap reached), the
 *     session is retried at full price. No app-side seat counter exists.
 *   * **charge_automatically** (Checkout default) — a $79 consumer-priced
 *     subscription is card-on-file, unlike the invoiced B2B retainer.
 *   * **Stripe Tax, exclusive.** Sessions run with automatic_tax enabled
 *     and the SaaS tax code on the product; tax is added on top of the
 *     published price in jurisdictions where a registration is active
 *     (AZ TPT at launch — physical nexus). Stripe Tax must be activated
 *     in the dashboard or session creation fails.
 *
 * DEV-MODE PATTERN: when apiKey is undefined, log and return a mock — same
 * as client.ts / resend.ts.
 */

import { captureWarning } from '../observability/sentry'
import { STRIPE_API_BASE, stripeHeaders } from './client'

/** Published launch pricing (ADR 0067). Cents. */
const HOSTED_AGENT_PRICE_CENTS = 7900
const HOSTED_AGENT_FOUNDING_DISCOUNT_CENTS = 3000
const HOSTED_AGENT_FOUNDING_SEATS = 25

/**
 * Stripe Tax code: "Software as a service (SaaS) - personal use"
 * (cloud-delivered, no download). AZ taxes this under the personal
 * property rental TPT classification; Stripe Tax computes per active
 * registration. Tax is EXCLUSIVE — added on top of the published price.
 */
const SAAS_TAX_CODE = 'txcd_10103000'

const PRODUCT_MARKER = { key: 'smd_product', value: 'hosted-agent' } as const
const PRODUCT_NAME = 'SMD Hosted Agent'
/** Fixed coupon id — Stripe treats coupon ids as idempotent identities. */
const FOUNDING_COUPON_ID = 'hosted-agent-founding'

async function resolveHostedAgentProductId(apiKey: string): Promise<string> {
  const query = `metadata['${PRODUCT_MARKER.key}']:'${PRODUCT_MARKER.value}'`
  const searchRes = await fetch(
    `${STRIPE_API_BASE}/products/search?query=${encodeURIComponent(query)}`,
    { method: 'GET', headers: { Authorization: `Bearer ${apiKey}` } }
  )
  if (searchRes.ok) {
    const data: { data: Array<{ id: string; tax_code: string | null }> } = await searchRes.json()
    if (data.data.length > 0) {
      const product = data.data[0]
      // Self-heal a product created before tax wiring existed.
      if (product.tax_code !== SAAS_TAX_CODE) {
        const patch = new URLSearchParams()
        patch.append('tax_code', SAAS_TAX_CODE)
        await fetch(`${STRIPE_API_BASE}/products/${product.id}`, {
          method: 'POST',
          headers: stripeHeaders(apiKey),
          body: patch.toString(),
        })
      }
      return product.id
    }
  }
  const body = new URLSearchParams()
  body.append('name', PRODUCT_NAME)
  body.append(`metadata[${PRODUCT_MARKER.key}]`, PRODUCT_MARKER.value)
  body.append('tax_code', SAAS_TAX_CODE)
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

/**
 * Ensure the founding-seat coupon exists (fixed id, forever duration,
 * 25-redemption cap). Returns the coupon id when it is still redeemable,
 * or null when exhausted/invalid — the caller then sells at full price.
 */
async function resolveFoundingCouponId(apiKey: string): Promise<string | null> {
  const getRes = await fetch(`${STRIPE_API_BASE}/coupons/${FOUNDING_COUPON_ID}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (getRes.ok) {
    const coupon: { valid: boolean; times_redeemed: number; max_redemptions: number | null } =
      await getRes.json()
    const capReached =
      coupon.max_redemptions !== null && coupon.times_redeemed >= coupon.max_redemptions
    return coupon.valid && !capReached ? FOUNDING_COUPON_ID : null
  }

  const body = new URLSearchParams()
  body.append('id', FOUNDING_COUPON_ID)
  body.append('amount_off', String(HOSTED_AGENT_FOUNDING_DISCOUNT_CENTS))
  body.append('currency', 'usd')
  body.append('duration', 'forever')
  body.append('max_redemptions', String(HOSTED_AGENT_FOUNDING_SEATS))
  body.append('name', 'Hosted Agent founding seat')
  const createRes = await fetch(`${STRIPE_API_BASE}/coupons`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (createRes.ok) return FOUNDING_COUPON_ID
  // A concurrent create can race us; re-read once rather than failing checkout.
  const retryRes = await fetch(`${STRIPE_API_BASE}/coupons/${FOUNDING_COUPON_ID}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (retryRes.ok) return FOUNDING_COUPON_ID
  return null
}

export interface CreateHostedAgentCheckoutParams {
  /** Buyer email — pre-fills the Checkout page and keys the Stripe customer. */
  customer_email: string
  /** Clerk user id; comes back on the webhook as client_reference_id. */
  clerk_user_id: string
  /** Absolute URL; Stripe substitutes {CHECKOUT_SESSION_ID}. */
  success_url: string
  /** Absolute URL for an abandoned checkout. */
  cancel_url: string
}

export interface HostedAgentCheckoutResult {
  id: string
  url: string
  /** Whether the founding discount was attached to this session. */
  founding: boolean
}

async function createSession(
  apiKey: string,
  productId: string,
  params: CreateHostedAgentCheckoutParams,
  couponId: string | null
): Promise<{ id: string; url: string }> {
  const body = new URLSearchParams()
  body.append('mode', 'subscription')
  body.append('line_items[0][quantity]', '1')
  body.append('line_items[0][price_data][unit_amount]', String(HOSTED_AGENT_PRICE_CENTS))
  body.append('line_items[0][price_data][currency]', 'usd')
  body.append('line_items[0][price_data][product]', productId)
  body.append('line_items[0][price_data][recurring][interval]', 'month')
  body.append('line_items[0][price_data][tax_behavior]', 'exclusive')
  // Requires Stripe Tax activated in the dashboard; sessions fail otherwise.
  body.append('automatic_tax[enabled]', 'true')
  body.append('success_url', params.success_url)
  body.append('cancel_url', params.cancel_url)
  body.append('client_reference_id', params.clerk_user_id)
  body.append('customer_email', params.customer_email)
  body.append('metadata[product_slug]', 'hosted-agent')
  body.append('subscription_data[metadata][product_slug]', 'hosted-agent')
  body.append('subscription_data[metadata][smd_clerk_user_id]', params.clerk_user_id)
  if (couponId) {
    body.append('discounts[0][coupon]', couponId)
  }
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
 * Create a Hosted Agent Checkout Session. Attempts the founding discount
 * first; falls back to full price when the coupon is exhausted or rejected.
 */
export async function createHostedAgentCheckoutSession(
  apiKey: string | undefined,
  params: CreateHostedAgentCheckoutParams
): Promise<HostedAgentCheckoutResult> {
  if (!apiKey) {
    const devId = 'dev_cs_' + crypto.randomUUID()
    console.log('[DEV] Stripe: would create hosted-agent checkout session')
    console.log(`[DEV] Stripe: customer_email=${params.customer_email}`)
    return { id: devId, url: '#dev-mode', founding: true }
  }

  const productId = await resolveHostedAgentProductId(apiKey)
  const couponId = await resolveFoundingCouponId(apiKey)

  if (couponId) {
    try {
      const session = await createSession(apiKey, productId, params, couponId)
      return { ...session, founding: true }
    } catch (err) {
      // Coupon cap can be hit between the read and the create — sell at
      // full price rather than losing the sale.
      console.log('[stripe/checkout] founding coupon rejected, retrying at full price:', err)
      captureWarning(
        'founding coupon rejected; checkout retried at full price',
        'stripe/checkout',
        {
          reason: err instanceof Error ? err.message : String(err),
        }
      )
    }
  }
  const session = await createSession(apiKey, productId, params, null)
  return { ...session, founding: false }
}

/**
 * Create a Stripe Billing Portal session for a hosted-agent subscriber —
 * the portal's "manage billing / cancel" door (the product terms promise
 * cancellation from the portal). Uses the account's default portal
 * configuration; card entry and cancellation happen on Stripe's surface.
 */
export async function createBillingPortalSession(
  apiKey: string | undefined,
  stripeCustomerId: string,
  returnUrl: string
): Promise<string> {
  if (!apiKey) {
    console.log('[DEV] Stripe: would create billing portal session')
    return returnUrl
  }
  const body = new URLSearchParams()
  body.append('customer', stripeCustomerId)
  body.append('return_url', returnUrl)
  const res = await fetch(`${STRIPE_API_BASE}/billing_portal/sessions`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe billing portal session failed ${res.status}: ${await res.text()}`)
  }
  const data: { url: string } = await res.json()
  return data.url
}

export interface HostedAgentCheckoutSessionView {
  id: string
  status: string
  payment_status: string
  customer_email: string | null
}

/** Fetch a session for the thanks page. Never returns payment details. */
export async function getHostedAgentCheckoutSession(
  apiKey: string | undefined,
  sessionId: string
): Promise<HostedAgentCheckoutSessionView> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would get checkout session ${sessionId}`)
    return { id: sessionId, status: 'complete', payment_status: 'paid', customer_email: null }
  }
  const res = await fetch(`${STRIPE_API_BASE}/checkout/sessions/${sessionId}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${apiKey}` },
  })
  if (!res.ok) {
    throw new Error(`Stripe checkout session get failed ${res.status}: ${await res.text()}`)
  }
  const data: {
    id: string
    status: string
    payment_status: string
    customer_details: { email: string | null } | null
  } = await res.json()
  return {
    id: data.id,
    status: data.status,
    payment_status: data.payment_status,
    customer_email: data.customer_details?.email ?? null,
  }
}
