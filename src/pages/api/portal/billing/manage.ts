import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { getPortalClient } from '../../../../lib/portal/session'
import { resolveProductAccess } from '../../../../lib/portal/product-access'
import { getStripeCustomerIdForSubscription } from '../../../../lib/portal/billing'
import { createBillingPortalSession } from '../../../../lib/stripe/checkout'
import { getPortalBaseUrl } from '../../../../lib/config/app-url'

/**
 * POST /api/portal/billing/manage (portal IA rebuild, 2026-07-07)
 *
 * The one Manage-Billing door for every subscription product: creates a
 * Stripe Billing Portal session for the entity's Stripe customer on the
 * posted product and 303s to it. Keeps the /agent/terms promise ("cancel
 * at any time from your portal") for Operator and Hosted Agent alike.
 * Principal-gated per product; the Stripe customer id comes from
 * subscriptions.settings_json, never from user input.
 *
 * Return targets are per-product portal pages until the unified
 * /portal/billing destination ships (cutover PR), then both move there.
 */

const ALLOWED: Record<string, { returnPath: string }> = {
  operator: { returnPath: '/portal/products/operator/account' },
  'hosted-agent': { returnPath: '/portal/products/operator/setup' },
}

function back(path: string, cs: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${path}?cs=${cs}` } })
}

export const POST: APIRoute = async ({ locals, request }) => {
  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData?.client) {
    return new Response(null, { status: 303, headers: { Location: '/auth/sign-in' } })
  }
  const { user, client } = portalData

  const form = await request.formData()
  const slugRaw = form.get('product_slug')
  const slug = typeof slugRaw === 'string' ? slugRaw : ''
  const target = ALLOWED[slug]
  if (!target) return back('/portal', 'billing_invalid')

  // Operator is instance-addressed: return to the addressed instance's
  // Settings (the act surface holding Plan & billing) when the form carries an
  // instance; else the bare product path.
  let returnPath = target.returnPath
  if (slug === 'operator') {
    const instanceRaw = form.get('instance')
    const instance = typeof instanceRaw === 'string' && instanceRaw !== '' ? instanceRaw : null
    if (instance) returnPath = `/portal/products/operator/${instance}/settings`
  }

  const access = await resolveProductAccess(env.DB, user.id, client.id, slug)
  if (!access || !access.roles.includes('principal')) {
    return back(returnPath, 'billing_forbidden')
  }

  const stripeCustomerId = await getStripeCustomerIdForSubscription(env.DB, client.id, slug)
  if (!stripeCustomerId) return back(returnPath, 'billing_unavailable')

  try {
    const portalBase = getPortalBaseUrl(env) ?? ''
    const url = await createBillingPortalSession(
      env.STRIPE_API_KEY,
      stripeCustomerId,
      `${portalBase}${returnPath}`
    )
    return new Response(null, { status: 303, headers: { Location: url } })
  } catch (err) {
    console.error('[portal/billing] portal session failed:', err)
    return back(returnPath, 'billing_error')
  }
}
