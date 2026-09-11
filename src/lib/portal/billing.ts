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
import type { Tone } from './status'
import { parseCancelAt } from '../db/subscriptions'
import { cardProcessingFeeCents } from '../pricing/card-fee'
import { operatorPaymentMethod } from '../db/services'
import type { OperatorPaymentMethod, Service } from '../db/services'

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
 * What the client is charged each month, on the rail authored for them
 * (services.payment_method). ACH is the price and nothing else. Card adds
 * the 3% processing fee (agreement §3.8; the same rate and line the
 * one-time invoices carry) as its own figure, so every surface can state
 * the fee before the client pays. Null when no price is authored.
 */
export interface OperatorMonthlyCharge {
  paymentMethod: OperatorPaymentMethod
  /** Authored monthly price, integer cents. */
  priceCents: number
  /** The card processing fee, integer cents; 0 on ACH. */
  feeCents: number
  /** priceCents + feeCents: the amount Stripe charges each month. */
  totalCents: number
}

export function operatorMonthlyCharge(
  service: Pick<Service, 'recurring_price' | 'payment_method'> | null | undefined
): OperatorMonthlyCharge | null {
  const priceCents = operatorMonthlyPriceCents(service?.recurring_price)
  if (priceCents === null) return null
  const paymentMethod = operatorPaymentMethod(service)
  const feeCents = paymentMethod === 'card' ? cardProcessingFeeCents(priceCents) : 0
  return { paymentMethod, priceCents, feeCents, totalCents: priceCents + feeCents }
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
 *  canStartOperatorSubscription holds (the page decides; components render).
 *  Home and the Operator hero LINK to the subscription page, where the one
 *  primary action lives (UI-PATTERNS Rule 3: one primary per view); only that
 *  page POSTs to the start route. */
export interface OperatorStartDoor {
  /** The subscription page for this instance (operatorSubscriptionHref). */
  href: string
  /** The monthly charge on the authored rail, integer cents (price plus any card fee). */
  priceCents: number
}

/**
 * The subscription page for an operator instance: the one place the
 * client starts it (Stripe Checkout) and, once started, manages it (the
 * Stripe Billing Portal door). Billing rows, Home's card and the Operator
 * hero all land here. Read-only ledger on Billing; the act on its own page,
 * the same list-to-detail shape invoices already have.
 */
export function operatorSubscriptionHref(instanceSlug: string): string {
  return `/portal/billing/subscriptions/${instanceSlug}`
}

/**
 * The stamp a subscription row carries on the Billing ledger (UI-PATTERNS
 * Rule 1: list rows use the pill). One fact, one rendering: the stamp IS
 * the status, so the row shows no second status line. A startable row
 * reads NOT STARTED, not "being set up": the Operator may well be running;
 * what has not happened is the client's own start.
 */
export function subscriptionStamp(
  sub: Pick<SubscriptionRow, 'status' | 'settings_json'>,
  startable: boolean
): { tone: Tone; label: string } {
  if (startable) return { tone: 'warning', label: 'NOT STARTED' }
  if (parseCancelAt(sub.settings_json)) return { tone: 'neutral', label: 'CANCELS' }
  switch (sub.status) {
    case 'active':
      return { tone: 'success', label: 'ACTIVE' }
    case 'paused':
      return { tone: 'danger', label: 'PAUSED' }
    case 'provisioning':
      return { tone: 'neutral', label: 'BEING SET UP' }
    default:
      return { tone: 'neutral', label: 'ARCHIVED' }
  }
}

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
