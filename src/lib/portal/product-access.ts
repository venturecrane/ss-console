/**
 * Product access helpers for the customer-first portal.
 *
 * Two layers govern whether a portal user can use a given product on a
 * given entity:
 *
 *   1. `subscriptions(entity_id, product_slug)` — does this customer
 *      have an active subscription to the product? Status lifecycle:
 *      provisioning → active → paused → cancelled.
 *
 *   2. `product_roles(user_id, entity_id, product_slug, role)` — is
 *      this specific user granted a role inside the product? AI
 *      Employee vocabulary: `principal | operator | compliance`.
 *      Future products bring their own vocabularies.
 *
 * Both layers must pass for the product surface to render. Anything
 * less degrades to the appropriate empty state per
 * docs/style/empty-state-pattern.md (no fabrication).
 */

export interface SubscriptionRow {
  id: string
  org_id: string
  entity_id: string
  product_slug: string
  /**
   * Per-instance discriminator for multi-instance products (migration 0089).
   * For `operator` it holds the `customer_slug` of the instance; `null` for
   * single-instance products (hosted-agent, engagement). A new operator
   * subscription MUST always be written WITH this set — see
   * {@link getOperatorSubscriptionByInstance}.
   */
  instance_slug: string | null
  status: string
  started_at: string
  ended_at: string | null
  settings_json: string | null
  /** COGS/MRR service linkage (nullable; $0 internal instances leave it null). */
  service_id: string | null
  /** Set by the client's own checkout (operator) or the Hosted Agent checkout; null until started. */
  stripe_subscription_id: string | null
  created_at: string
  updated_at: string
}

export interface ProductAccess {
  subscription: SubscriptionRow
  roles: string[]
}

/**
 * Return the customer's active subscription for a given product slug, or
 * null if no row exists or the row is cancelled. `provisioning`,
 * `active`, and `paused` are all returned — the caller decides which
 * statuses unlock which UI states.
 */
export async function getProductSubscription(
  db: D1Database,
  entityId: string,
  productSlug: string
): Promise<SubscriptionRow | null> {
  return await db
    .prepare(
      `SELECT * FROM subscriptions
        WHERE entity_id = ? AND product_slug = ?
          AND status IN ('provisioning', 'active', 'paused')`
    )
    .bind(entityId, productSlug)
    .first<SubscriptionRow>()
}

/**
 * Return a specific operator INSTANCE's live subscription for an entity,
 * addressed by `instance_slug` (= the instance's `customer_slug`). This is the
 * multi-instance-aware counterpart to `getProductSubscription`: an entity may
 * hold several `operator` subscriptions since migration 0089, so the product
 * slug alone no longer identifies one. Returns null when no live row matches.
 */
export async function getOperatorSubscriptionByInstance(
  db: D1Database,
  entityId: string,
  instanceSlug: string
): Promise<SubscriptionRow | null> {
  return await db
    .prepare(
      `SELECT * FROM subscriptions
        WHERE entity_id = ? AND product_slug = 'operator' AND instance_slug = ?
          AND status IN ('provisioning', 'active', 'paused')`
    )
    .bind(entityId, instanceSlug)
    .first<SubscriptionRow>()
}

/**
 * All live (provisioning/active/paused) subscriptions for an entity in
 * one query. The offerings resolver and the Billing surface consume this
 * instead of per-slug getProductSubscription calls.
 */
export async function listActiveSubscriptionsForEntity(
  db: D1Database,
  entityId: string
): Promise<SubscriptionRow[]> {
  const result = await db
    .prepare(
      `SELECT * FROM subscriptions
        WHERE entity_id = ? AND status IN ('provisioning', 'active', 'paused')
        ORDER BY created_at ASC`
    )
    .bind(entityId)
    .all<SubscriptionRow>()
  return result.results ?? []
}

/**
 * Return the list of active (non-revoked) roles this user holds on this
 * (entity, product) tuple. Empty array means the user has no access
 * inside the product even if the customer has a subscription.
 */
export async function listProductRoles(
  db: D1Database,
  userId: string,
  entityId: string,
  productSlug: string
): Promise<string[]> {
  const result = await db
    .prepare(
      `SELECT role FROM product_roles
        WHERE user_id = ? AND entity_id = ? AND product_slug = ?
          AND revoked_at IS NULL
        ORDER BY granted_at ASC`
    )
    .bind(userId, entityId, productSlug)
    .all<{ role: string }>()
  return (result.results ?? []).map((r) => r.role)
}

/**
 * Combined check: does this user have access to this product on this
 * customer? Returns the subscription + role list when both layers pass.
 * Returns null when either the subscription is missing/cancelled or the
 * user has no roles granted.
 */
export async function resolveProductAccess(
  db: D1Database,
  userId: string,
  entityId: string,
  productSlug: string
): Promise<ProductAccess | null> {
  const subscription = await getProductSubscription(db, entityId, productSlug)
  if (!subscription) return null

  const roles = await listProductRoles(db, userId, entityId, productSlug)
  if (roles.length === 0) return null

  return { subscription, roles }
}
