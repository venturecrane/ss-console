import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import {
  getSubscriptionForBilling,
  setSubscriptionBillingStatus,
} from '../../../../../lib/db/subscriptions'
import {
  cancelOperatorSubscription,
  pauseOperatorSubscription,
  resumeOperatorSubscription,
} from '../../../../../lib/stripe/subscriptions'

/**
 * POST /api/admin/clients/[id]/subscription-billing
 *
 * Drives an ALREADY-STARTED Operator retainer's Stripe billing from the
 * client hub. Form field `action` ∈ pause | resume | cancel.
 *
 *   pause  — pause_collection[behavior]=void at Stripe + local row `paused`.
 *   resume — clears pause_collection + local row `active`.
 *   cancel — cancels at Stripe immediately + local row `cancelled`.
 *
 * There is no `start`. The retainer starts by the client's own act in the
 * portal (Billing → Start subscription → Stripe Checkout), never from here:
 * the admin-started send_invoice retainer that used to live in this route
 * would have had Stripe email the firm a monthly invoice with no act by the
 * client and no approval by the Captain, and was ripped 2026-08-29.
 *
 * Local status is ALSO mirrored by the customer.subscription.* webhooks;
 * writing it here too keeps the console truthful between webhook deliveries.
 * Payment failure handling lives in the webhook (alert, never auto-act) —
 * see stripe-subscription-handler.ts and the offboarding doctrine (#1684).
 */

const ACTIONS = ['pause', 'resume', 'cancel'] as const
type BillingAction = (typeof ACTIONS)[number]

function parseAction(raw: FormDataEntryValue | null): BillingAction | null {
  return typeof raw === 'string' && (ACTIONS as readonly string[]).includes(raw)
    ? (raw as BillingAction)
    : null
}

/** pause / resume / cancel: drive Stripe, mirror the local row. */
async function handleAttachedAction(
  action: 'pause' | 'resume' | 'cancel',
  subRowId: string,
  stripeSubscriptionId: string
): Promise<string> {
  if (action === 'pause') {
    await pauseOperatorSubscription(env.STRIPE_API_KEY, stripeSubscriptionId)
    await setSubscriptionBillingStatus(env.DB, subRowId, 'paused')
    return 'billing=paused'
  }
  if (action === 'resume') {
    await resumeOperatorSubscription(env.STRIPE_API_KEY, stripeSubscriptionId)
    await setSubscriptionBillingStatus(env.DB, subRowId, 'active')
    return 'billing=resumed'
  }
  await cancelOperatorSubscription(env.STRIPE_API_KEY, stripeSubscriptionId)
  await setSubscriptionBillingStatus(env.DB, subRowId, 'cancelled')
  return 'billing=cancelled'
}

async function handlePost({ request, locals, params, redirect }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const entityId = params.id
  if (!entityId) return redirect('/admin/clients?error=missing', 302)
  const back = (q: string) => redirect(`/admin/clients/${entityId}?${q}`, 302)

  let action: BillingAction | null
  try {
    action = parseAction((await request.formData()).get('action'))
  } catch {
    action = null
  }
  if (!action) return back('error=bad_billing_action')

  try {
    const sub = await getSubscriptionForBilling(env.DB, entityId, 'operator')
    if (!sub) return back('error=no_subscription_row')

    if (!sub.stripe_subscription_id) return back('error=no_billing_attached')
    return back(await handleAttachedAction(action, sub.id, sub.stripe_subscription_id))
  } catch (err) {
    console.error('[api/admin/clients/subscription-billing] error:', err)
    return back('error=billing_server')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
