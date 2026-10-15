/**
 * Subscription-row helpers for the Operator billing engine (#1679).
 *
 * The `subscriptions` table is the product-access gate the portal reads
 * (src/lib/portal/product-access.ts): status provisioning/active/paused/
 * cancelled decides what a client sees. Provisioning owns row CREATION;
 * this module only attaches/detaches Stripe billing to an existing row and
 * mirrors billing-driven status transitions. It never inserts rows —
 * granting portal access remains provisioning's decision, not billing's.
 */

import type { D1Database } from '@cloudflare/workers-types'

export interface SubscriptionBillingRow {
  id: string
  org_id: string
  entity_id: string
  product_slug: string
  status: string
  stripe_subscription_id: string | null
  settings_json: string | null
  /** The seat's instance (= customer_slug) for multi-instance products; null otherwise. */
  instance_slug: string | null
  /** The commercial line this seat is priced by (migration 0126); null when unlinked. */
  service_id: string | null
}

const BILLING_COLUMNS =
  'id, org_id, entity_id, product_slug, status, stripe_subscription_id, settings_json, instance_slug, service_id'

/**
 * Billing was asked about a client with several seats of a product and no
 * seat was named. The caller must name one: acting on "the" subscription of
 * a client that has two would pause or cancel whichever row came back first.
 */
export class AmbiguousSubscriptionError extends Error {
  constructor(entityId: string, productSlug: string, count: number) {
    super(`${count} ${productSlug} subscriptions on entity ${entityId}; name the instance`)
    this.name = 'AmbiguousSubscriptionError'
  }
}

/**
 * The subscription row billing acts on, or null.
 *
 * With `instanceSlug`, exactly that seat of (entity, product). Without it, the
 * entity's only row for the product; when the entity holds more than one
 * (an Operator client with two seats, migration 0089), this REFUSES with
 * {@link AmbiguousSubscriptionError} rather than guess.
 */
export async function getSubscriptionForBilling(
  db: D1Database,
  entityId: string,
  productSlug: string,
  instanceSlug?: string | null
): Promise<SubscriptionBillingRow | null> {
  if (instanceSlug) {
    const row = await db
      .prepare(
        `SELECT ${BILLING_COLUMNS} FROM subscriptions
          WHERE entity_id = ? AND product_slug = ? AND instance_slug = ?`
      )
      .bind(entityId, productSlug, instanceSlug)
      .first<SubscriptionBillingRow>()
    return row ?? null
  }
  const rows = await db
    .prepare(
      `SELECT ${BILLING_COLUMNS} FROM subscriptions WHERE entity_id = ? AND product_slug = ? LIMIT 2`
    )
    .bind(entityId, productSlug)
    .all<SubscriptionBillingRow>()
  const found = rows.results ?? []
  if (found.length > 1) throw new AmbiguousSubscriptionError(entityId, productSlug, found.length)
  return found[0] ?? null
}

/**
 * The Stripe customer of another seat of the same client that already has a
 * Stripe subscription, or null.
 *
 * A client's second Operator seat must be charged on the Stripe customer the
 * firm already pays from, not a new one. The local
 * `settings_json.stripe_customer_id` is a mirror; the caller asks Stripe for
 * the subscription's `customer`, which is the authority. Returns the Stripe
 * subscription id of the oldest live other seat with one (a cancelled seat
 * only when no live one has), so the answer is stable.
 */
export async function findSiblingStripeSubscriptionId(
  db: D1Database,
  entityId: string,
  excludeRowId: string
): Promise<string | null> {
  const row = await db
    .prepare(
      `SELECT stripe_subscription_id FROM subscriptions
        WHERE entity_id = ? AND product_slug = 'operator' AND id != ?
          AND stripe_subscription_id IS NOT NULL
        ORDER BY (status = 'cancelled') ASC, created_at ASC, id ASC
        LIMIT 1`
    )
    .bind(entityId, excludeRowId)
    .first<{ stripe_subscription_id: string }>()
  return row?.stripe_subscription_id ?? null
}

/** Resolve the local row a Stripe subscription event belongs to. Webhooks
 * carry no org context; the stripe id is globally unique (partial unique
 * index, migration 0084). */
export async function getSubscriptionByStripeId(
  db: D1Database,
  stripeSubscriptionId: string
): Promise<SubscriptionBillingRow | null> {
  const row = await db
    .prepare(`SELECT ${BILLING_COLUMNS} FROM subscriptions WHERE stripe_subscription_id = ?`)
    .bind(stripeSubscriptionId)
    .first<SubscriptionBillingRow>()
  return row ?? null
}

/** The local row by its own id. The webhook fallback path reads this when a
 * cycle invoice's subscription metadata names a row that Stripe attached
 * before the checkout event that would have bound it arrived (A1). */
export async function getSubscriptionById(
  db: D1Database,
  subscriptionRowId: string
): Promise<SubscriptionBillingRow | null> {
  const row = await db
    .prepare(`SELECT ${BILLING_COLUMNS} FROM subscriptions WHERE id = ?`)
    .bind(subscriptionRowId)
    .first<SubscriptionBillingRow>()
  return row ?? null
}

/**
 * Attach a Stripe subscription (and its customer) to the local row. The
 * customer id lands in settings_json.stripe_customer_id, the single key the
 * portal's Manage Billing door reads (src/lib/portal/billing.ts); the
 * merge keeps any other settings the row carries.
 */
export async function attachStripeSubscription(
  db: D1Database,
  subscriptionRowId: string,
  stripeSubscriptionId: string,
  stripeCustomerId: string | null = null
): Promise<void> {
  await db
    .prepare(
      `UPDATE subscriptions
         SET stripe_subscription_id = ?,
             settings_json = CASE WHEN ? IS NULL THEN settings_json
                                  ELSE json_set(COALESCE(settings_json, '{}'), '$.stripe_customer_id', ?) END,
             updated_at = datetime('now')
       WHERE id = ?`
    )
    .bind(stripeSubscriptionId, stripeCustomerId, stripeCustomerId, subscriptionRowId)
    .run()
}

/**
 * Detach a Stripe subscription whose first payment failed, so the client can
 * start again. Both start gates (the portal's `canStart` and the server-side
 * start-subscription route) require `stripe_subscription_id IS NULL`, so
 * clearing it is what re-opens the door. `settings_json.stripe_customer_id`
 * is left alone: the Stripe customer exists and the record of it is true.
 * The next checkout reuses a customer only through ANOTHER seat's live Stripe
 * subscription (start-subscription asks Stripe for that subscription's
 * customer); otherwise it passes `customer_email` and Stripe resolves or
 * creates one. The attach that follows overwrites the key with whatever
 * Stripe returns.
 *
 * Guarded on the id being detached: a webhook retry that lands after the
 * client has already attached a NEW subscription must not clear that one.
 * Returns true when the row was detached.
 */
export async function detachStripeSubscription(
  db: D1Database,
  subscriptionRowId: string,
  stripeSubscriptionId: string
): Promise<boolean> {
  const res = await db
    .prepare(
      `UPDATE subscriptions SET stripe_subscription_id = NULL, updated_at = datetime('now')
       WHERE id = ? AND stripe_subscription_id = ?`
    )
    .bind(subscriptionRowId, stripeSubscriptionId)
    .run()
  return (res.meta.changes ?? 0) > 0
}

/**
 * Mirror Stripe's SCHEDULED cancellation onto the row.
 *
 * A client who cancels in the Stripe Billing Portal does not end the
 * subscription — the portal is configured `mode: at_period_end`, so Stripe
 * flips `cancel_at_period_end` and keeps billing state `active` until the
 * paid month runs out. Without this mirror the local row is indistinguishable
 * from a healthy one and the client's own cancellation is invisible to both
 * sides until `customer.subscription.deleted` lands weeks later.
 *
 * Stored as `settings_json.cancel_at` (ISO) — set when scheduled, removed
 * when the client reverses it. The status column is untouched: a scheduled
 * cancellation is still an `active`, still-served, still-paid subscription.
 */
export async function setSubscriptionCancelSchedule(
  db: D1Database,
  subscriptionRowId: string,
  cancelAtIso: string | null
): Promise<void> {
  const mutation =
    cancelAtIso === null
      ? "json_remove(COALESCE(settings_json, '{}'), '$.cancel_at')"
      : "json_set(COALESCE(settings_json, '{}'), '$.cancel_at', ?)"
  const stmt = db.prepare(
    `UPDATE subscriptions SET settings_json = ${mutation}, updated_at = datetime('now') WHERE id = ?`
  )
  await (
    cancelAtIso === null ? stmt.bind(subscriptionRowId) : stmt.bind(cancelAtIso, subscriptionRowId)
  ).run()
}

/**
 * The scheduled cancellation date on a row, or null when none is scheduled.
 * The read counterpart of {@link setSubscriptionCancelSchedule}; consumed by
 * the portal Billing surface and the admin client hub so both sides name the
 * same date.
 */
export function parseCancelAt(settingsJson: string | null): string | null {
  try {
    const settings: unknown = settingsJson ? JSON.parse(settingsJson) : null
    if (settings && typeof settings === 'object' && 'cancel_at' in settings) {
      const v = (settings as Record<string, unknown>)['cancel_at']
      return typeof v === 'string' && !Number.isNaN(new Date(v).getTime()) ? v : null
    }
  } catch {
    // malformed settings_json is not a cancellation
  }
  return null
}

/**
 * Mirror a billing-driven status transition onto the local row. Restricted
 * to the transitions billing legitimately drives — it must never resurrect
 * a cancelled row or skip provisioning:
 *
 *   * `paused`    — collection paused (audit-only access)
 *   * `active`    — collection resumed
 *   * `cancelled` — subscription deleted at Stripe; ended_at is stamped
 *
 * Provisioning guard (ADR 0067): a `provisioning` row is promoted to
 * `active` by the provisioning/activation flow ONLY — for the Hosted Agent
 * a Stripe subscription attaches at checkout, so subscription.updated
 * events arrive while the seat is still being stood up and must not flip
 * it live. Cancellation still applies to a provisioning row (a buyer who
 * cancels before activation is honestly cancelled).
 */
export async function setSubscriptionBillingStatus(
  db: D1Database,
  subscriptionRowId: string,
  status: 'active' | 'paused' | 'cancelled'
): Promise<void> {
  if (status === 'cancelled') {
    await db
      .prepare(
        "UPDATE subscriptions SET status = 'cancelled', ended_at = datetime('now'), updated_at = datetime('now') WHERE id = ?"
      )
      .bind(subscriptionRowId)
      .run()
    return
  }
  await db
    .prepare(
      "UPDATE subscriptions SET status = ?, updated_at = datetime('now') WHERE id = ? AND status NOT IN ('cancelled', 'provisioning')"
    )
    .bind(status, subscriptionRowId)
    .run()
}

/**
 * Promote a `provisioning` Operator row to `active` because the client has
 * started (paid) the retainer. This is the go-live flip the portal reads:
 * `provisioning` keeps a client in the review-and-configure window (Home
 * hidden, Billing hidden, landing on the operator page); `active` reveals
 * the full portal (src/lib/portal/offerings.ts, hasBillingRelationship).
 * Called for the operator product, from `provisioning`, by the two webhook
 * paths that observe the client's own payment: the operator checkout
 * handler (completed / async_payment_succeeded, gated on payment_status)
 * and the retainer invoice.paid handler (ordering fallback, and the ACH
 * settlement on an attached row). The generic subscription-status mirror keeps its provisioning guard
 * (the Hosted Agent's checkout-before-standup flow must not be promoted by
 * billing events). Returns true when a row was promoted.
 */
export async function activateOperatorSubscriptionForBilling(
  db: D1Database,
  subscriptionRowId: string
): Promise<boolean> {
  const res = await db
    .prepare(
      "UPDATE subscriptions SET status = 'active', started_at = COALESCE(started_at, datetime('now')), updated_at = datetime('now') WHERE id = ? AND product_slug = 'operator' AND status = 'provisioning'"
    )
    .bind(subscriptionRowId)
    .run()
  return (res.meta.changes ?? 0) > 0
}
