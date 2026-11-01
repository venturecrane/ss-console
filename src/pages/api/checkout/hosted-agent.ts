import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { getPortalClient } from '../../../lib/portal/session'
import { getProductSubscription } from '../../../lib/portal/product-access'
import { HOSTED_AGENT_PRODUCT_SLUG } from '../../../lib/portal/hosted-agent-access'
import { createHostedAgentCheckoutSession } from '../../../lib/stripe/checkout'
import { getAppBaseUrl, getPortalBaseUrl } from '../../../lib/config/app-url'

/**
 * GET /api/checkout/hosted-agent — the Hosted Agent buy entry (ADR 0067).
 *
 * A plain anchor on /agent points here (0-JS storefront). Flow:
 *
 *   1. No Clerk session → 303 to sign-up carrying a redirect back here, so
 *      the checkout intent survives account creation.
 *   2. Already subscribed → 303 to the portal product page instead of
 *      double-selling (the webhook pipeline is also existence-guarded, this
 *      is just the polite front).
 *   3. Otherwise → create a Stripe Checkout Session (subscription mode,
 *      founding coupon with full-price fallback) and 303 to Stripe's hosted
 *      payment page. Card entry happens on Stripe's surface, never ours.
 *
 * GET (not POST) is deliberate: the entry is a navigation, it mutates
 * nothing locally, and the session it creates on Stripe is inert until the
 * buyer pays on Stripe's page.
 */
export const GET: APIRoute = async ({ locals }) => {
  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData) {
    return new Response(null, {
      status: 303,
      headers: { Location: '/auth/sign-up?redirect_url=%2Fapi%2Fcheckout%2Fhosted-agent' },
    })
  }

  const { user, client } = portalData
  if (client) {
    const existing = await getProductSubscription(env.DB, client.id, HOSTED_AGENT_PRODUCT_SLUG)
    if (existing) {
      const portalBase = getPortalBaseUrl(env) ?? ''
      return new Response(null, {
        status: 303,
        headers: { Location: `${portalBase}/portal/products/operator/setup` },
      })
    }
  }

  const appBase = getAppBaseUrl(env) ?? ''
  const session = await createHostedAgentCheckoutSession(env.STRIPE_API_KEY, {
    customer_email: user.email,
    clerk_user_id: user.clerk_user_id ?? user.id,
    success_url: `${appBase}/agent/thanks?session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${appBase}/agent?checkout=cancelled`,
  })

  if (session.url === '#dev-mode') {
    // Dev-mode stub (no STRIPE_API_KEY): land back on the page with a marker.
    return new Response(null, { status: 303, headers: { Location: '/agent?checkout=dev' } })
  }
  return new Response(null, { status: 303, headers: { Location: session.url } })
}
