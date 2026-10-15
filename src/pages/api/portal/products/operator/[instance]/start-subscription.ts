import type { APIRoute } from 'astro'
import { captureError } from '../../../../../../lib/observability/sentry'
import { env } from 'cloudflare:workers'
import { resolveOperatorAccess } from '../../../../../../lib/portal/operator-access'
import { getOperatorServiceForSeat, operatorPaymentMethod } from '../../../../../../lib/db/services'
import { findSiblingStripeSubscriptionId } from '../../../../../../lib/db/subscriptions'
import {
  createOperatorCheckoutSession,
  getStripeSubscriptionCustomerId,
} from '../../../../../../lib/stripe/subscriptions'
import { getPortalBaseUrl } from '../../../../../../lib/config/app-url'

/**
 * POST /api/portal/products/operator/[instance]/start-subscription — the
 * client starts the retainer (Captain, 2026-08-29: the client initiates the
 * subscription in the portal; nothing is billed or sent on their behalf).
 *
 * A principal on this instance clicks Start on Billing; this creates a
 * Stripe Checkout Session (subscription mode, on the rail authored for the
 * seat: ACH, or card with the 3% fee line, first month paid on Stripe's
 * page) and 303s to it. The session is inert until they pay;
 * the checkout.session.completed webhook then binds the subscription and
 * promotes the row to active.
 *
 * Price, rail and label are the SEAT's (migration 0126): read from the
 * service this instance's subscriptions row points at, never from another
 * seat of the same client. When another seat of the client already has a
 * Stripe subscription, the session is created on that subscription's Stripe
 * customer, so the firm pays every seat from one customer.
 *
 * Refused (303 back to Billing with a status) when: the row is not in
 * `provisioning` or already carries a Stripe subscription (no double-sell),
 * or no monthly price has been authored for this seat (never invent a
 * number; a Captain-side gap, surfaced as such).
 */

const BILLING = '/portal/billing'

function back(status: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `${BILLING}?start=${encodeURIComponent(status)}` },
  })
}

export const POST: APIRoute = async ({ params, locals }) => {
  const instance = typeof params.instance === 'string' ? params.instance : ''
  if (instance === '') return new Response('Not found', { status: 404 })

  const access = await resolveOperatorAccess(env.DB, locals, {
    allowedRoles: ['principal'],
    customerSlug: instance,
  })
  if (access.kind === 'redirect') return new Response('Forbidden', { status: 403 })

  const sub = access.subscription
  if (sub.status !== 'provisioning' || sub.stripe_subscription_id) return back('already_started')

  const service = await getOperatorServiceForSeat(env.DB, access.user.org_id, sub)
  const price = service?.recurring_price
  if (!service || typeof price !== 'number' || !Number.isFinite(price) || price <= 0) {
    console.error('[operator/start-subscription] no authored price for seat', sub.id)
    return back('unpriced')
  }

  const portalBase = getPortalBaseUrl(env) ?? ''
  try {
    // One firm, one Stripe customer: a second seat rides the customer the
    // first seat's subscription bills, as Stripe reports it.
    const siblingStripeSubscriptionId = await findSiblingStripeSubscriptionId(
      env.DB,
      access.client.id,
      sub.id
    )
    const stripeCustomerId = siblingStripeSubscriptionId
      ? await getStripeSubscriptionCustomerId(env.STRIPE_API_KEY, siblingStripeSubscriptionId)
      : null

    const session = await createOperatorCheckoutSession(env.STRIPE_API_KEY, {
      customer_email: access.user.email,
      stripe_customer_id: stripeCustomerId,
      monthly_amount_cents: Math.round(price * 100),
      payment_method: operatorPaymentMethod(service),
      label: service.label,
      service_id: service.id,
      entity_id: access.client.id,
      subscription_row_id: sub.id,
      user_id: access.user.id,
      success_url: `${portalBase}${BILLING}?start=done&session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${portalBase}${BILLING}?start=cancelled`,
    })
    if (session.url === '#dev-mode') return back('dev')
    return new Response(null, { status: 303, headers: { Location: session.url } })
  } catch (err) {
    console.error('[operator/start-subscription] checkout creation failed:', err)
    captureError(err, 'portal.start-subscription')
    return back('failed')
  }
}
