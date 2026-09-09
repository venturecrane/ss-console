/**
 * Portal billing helpers (portal IA rebuild, 2026-07-07).
 *
 * The Stripe customer id for a product subscription lives in
 * subscriptions.settings_json (written by the checkout webhook). This is
 * the single parse point for it — the generalized Manage-Billing
 * endpoint and the Billing surface both consume it. Extracted verbatim
 * from the hosted-agent billing endpoint's inline parsing.
 */

import type { D1Database } from '@cloudflare/workers-types'
import type { SubscriptionRow } from './product-access'

export function parseStripeCustomerId(settingsJson: string | null): string | null {
  try {
    const settings: unknown = settingsJson ? JSON.parse(settingsJson) : null
    if (settings && typeof settings === 'object' && 'stripe_customer_id' in settings) {
      const v = (settings as Record<string, unknown>)['stripe_customer_id']
      return typeof v === 'string' && v.startsWith('cus_') ? v : null
    }
  } catch {
    // fall through
  }
  return null
}

export async function getStripeCustomerIdForSubscription(
  db: D1Database,
  entityId: string,
  productSlug: string
): Promise<string | null> {
  const row = await db
    .prepare(
      `SELECT settings_json FROM subscriptions
        WHERE entity_id = ? AND product_slug = ?
          AND status IN ('provisioning', 'active', 'paused')
        ORDER BY created_at DESC LIMIT 1`
    )
    .bind(entityId, productSlug)
    .first<{ settings_json: string | null }>()
  return parseStripeCustomerId(row?.settings_json ?? null)
}

/**
 * The authored monthly price (services.recurring_price, dollars) as integer
 * cents; null when nothing is authored. No price, no start door, nothing
 * invented (docs/style/empty-state-pattern.md).
 */
export function operatorMonthlyPriceCents(recurringPrice: unknown): number | null {
  return typeof recurringPrice === 'number' && Number.isFinite(recurringPrice) && recurringPrice > 0
    ? Math.round(recurringPrice * 100)
    : null
}

/**
 * The ONE start gate for the Operator retainer (Captain, 2026-08-29: the
 * retainer starts only by the client's own click). True when the operator
 * row is still `provisioning`, no Stripe subscription is attached, the row
 * names its instance, and a monthly price is authored. Home, the Operator
 * page and Billing all read this so the door is the same door everywhere;
 * the server route (start-subscription.ts) re-checks the same facts.
 */
export function canStartOperatorSubscription(
  sub: Pick<
    SubscriptionRow,
    'product_slug' | 'status' | 'stripe_subscription_id' | 'instance_slug'
  >,
  priceCents: number | null
): boolean {
  return (
    sub.product_slug === 'operator' &&
    sub.status === 'provisioning' &&
    !sub.stripe_subscription_id &&
    !!sub.instance_slug &&
    priceCents !== null
  )
}

/** The client's start door as a surface renders it, present only when
 *  canStartOperatorSubscription holds (the page decides; components render). */
export interface OperatorStartDoor {
  /** POST target: the start-subscription route for this instance. */
  action: string
  /** Authored monthly price, integer cents. */
  priceCents: number
}

/** Where the start door lives on the Billing surface (deep link target). */
export const BILLING_SUBSCRIPTIONS_HREF = '/portal/billing#subscriptions'

/** Whole-dollar rendering for prose contexts (MoneyDisplay for markup). */
export function formatWholeDollars(amountCents: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(Math.round(amountCents / 100))
}

/** Display names for subscription rows on the Billing surface. */
const PRODUCT_DISPLAY_NAMES: Record<string, string> = {
  operator: 'Operator',
  'hosted-agent': 'Hosted Agent',
}

export function productDisplayName(sub: SubscriptionRow): string {
  return PRODUCT_DISPLAY_NAMES[sub.product_slug] ?? sub.product_slug
}

/**
 * The sentence the Billing surface shows on the `?start=done` return from
 * checkout, built from the Checkout Session's `payment_status` and the
 * client's own subscription row; never from the query string alone. The
 * date is the row's `started_at`, already formatted by the caller. Returns
 * null for every combination the two facts do not settle (render nothing,
 * per docs/style/empty-state-pattern.md).
 */
export function resolveStartDoneMessage(
  paymentStatus: 'paid' | 'unpaid' | 'no_payment_required',
  row: Pick<SubscriptionRow, 'status'>,
  startedOn: string
): string | null {
  if (paymentStatus === 'paid' && row.status === 'active') {
    return startedOn ? `Your subscription started on ${startedOn}.` : null
  }
  if (paymentStatus === 'unpaid' || row.status === 'provisioning') {
    return 'Checkout is complete. This page shows your subscription as active once the payment settles.'
  }
  return null
}
