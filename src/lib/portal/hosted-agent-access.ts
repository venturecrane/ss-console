/**
 * Shared access gate for the Hosted Agent product surfaces under
 * `/portal/products/hosted-agent/*` (ADR 0067).
 *
 * Same four-check shape as the Operator gate (operator-access.ts):
 *
 *   1. Clerk session present                  → otherwise sign-in
 *   2. Local entity bound to the Clerk user   → otherwise sign-in with no_subscription marker
 *   3. Hosted Agent subscription on the entity → otherwise landing page (which renders
 *                                                the no_subscription / provisioning /
 *                                                paused state)
 *   4. Caller holds at least one allowed role → otherwise landing page
 *
 * Deliberately a sibling of resolveOperatorAccess rather than a slug
 * parameter on it: the two products have different landing paths and the
 * Operator gate is load-bearing for a live paid seat. Generalizing both onto
 * one helper is a follow-up refactor, not a launch dependency.
 */

import type { Entity } from '../db/entities'
import type { PortalUserRow } from '../auth/clerk-bridge'
import { getPortalClient } from './session'
import { getProductSubscription, listProductRoles, type SubscriptionRow } from './product-access'

export const HOSTED_AGENT_PRODUCT_SLUG = 'hosted-agent'

const HOSTED_AGENT_LANDING_PATH = '/portal/products/hosted-agent'
const PORTAL_SIGN_IN_PATH = '/auth/sign-in'
const PORTAL_SIGN_IN_NO_SUBSCRIPTION_PATH = `${PORTAL_SIGN_IN_PATH}?status=no_subscription`

export type HostedAgentAccess =
  | { kind: 'redirect'; to: string }
  | {
      kind: 'allowed'
      user: PortalUserRow
      client: Entity
      subscription: SubscriptionRow
      roles: string[]
    }

export interface ResolveHostedAgentAccessOptions {
  /** Roles that grant access. Hosted Agent vocabulary is `principal` only at
   * launch (single-user product); the vocabulary can widen later. */
  allowedRoles: readonly string[]
}

/**
 * Resolve access for a Hosted Agent product surface. Returns a redirect
 * target (caller does `Astro.redirect(access.to)`) or the resolved tuple.
 */
export async function resolveHostedAgentAccess(
  db: D1Database,
  locals: App.Locals,
  options: ResolveHostedAgentAccessOptions
): Promise<HostedAgentAccess> {
  const portalData = await getPortalClient(db, locals)
  if (!portalData) {
    return { kind: 'redirect', to: PORTAL_SIGN_IN_PATH }
  }
  if (!portalData.client) {
    return { kind: 'redirect', to: PORTAL_SIGN_IN_NO_SUBSCRIPTION_PATH }
  }

  const { user, client } = portalData

  const subscription = await getProductSubscription(db, client.id, HOSTED_AGENT_PRODUCT_SLUG)
  if (!subscription) {
    return { kind: 'redirect', to: HOSTED_AGENT_LANDING_PATH }
  }

  const roles = await listProductRoles(db, user.id, client.id, HOSTED_AGENT_PRODUCT_SLUG)
  const hasAllowedRole = options.allowedRoles.some((r) => roles.includes(r))
  if (!hasAllowedRole) {
    return { kind: 'redirect', to: HOSTED_AGENT_LANDING_PATH }
  }

  return { kind: 'allowed', user, client, subscription, roles }
}
