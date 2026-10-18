/**
 * Access gate for the couples onboarding surfaces under
 * `/portal/products/hosted-agent/couples` (ADR 0090, ADR 0091 R7).
 *
 * Built on the Hosted Agent gate (hosted-agent-access.ts), widened in two
 * ways the single-person SKU never needed:
 *
 *   1. Both partners get in, each with the `partner` product role. A partner
 *      role is not principal: it grants nothing on the single-person surfaces,
 *      which keep admitting `principal` only.
 *   2. A partner who has never signed in has no role yet. SMD authored their
 *      partner row with an email; the first time they reach this entity (by
 *      the `users.entity_id` binding SMD's invitation pre-creates, see
 *      src/lib/hosted-agent/couples/invite.ts; by Clerk Organization
 *      membership; or, for a session that reaches no entity, by the email
 *      match in resolveCouplesAccess below), a VERIFIED email match claims that row and grants the role.
 *      An unverified address never reaches here (clerk-profile.ts returns
 *      null and the bridge refuses the user).
 *
 * The resolved `person` is the caller's OWN row. Every couples page renders
 * the caller's private answers from it and the other partner only through
 * `partnerPublicView` (R7).
 */

import type { Entity } from '../db/entities'
import type { PortalUserRow } from '../auth/clerk-bridge'
import type { SubscriptionRow } from './product-access'
import { resolveHostedAgentAccess } from './hosted-agent-access'
import { getPortalClient } from './session'
import {
  PARTNER_PRODUCT_ROLE,
  bindUnboundUserToCouplesByEmail,
  claimPartnerByEmail,
  getCouplesInstanceByEntity,
  getPersonForUser,
  type CouplesInstanceRow,
  type CouplesPersonRow,
} from '../db/couples-onboarding'

export const COUPLES_LANDING_PATH = '/portal/products/hosted-agent/couples'
const HOSTED_AGENT_LANDING_PATH = '/portal/products/hosted-agent'
const NO_ACCESS_PATH = '/auth/sign-in?status=no_subscription'

export type CouplesAccess =
  | { kind: 'redirect'; to: string }
  | {
      kind: 'allowed'
      user: PortalUserRow
      client: Entity
      subscription: SubscriptionRow
      instance: CouplesInstanceRow
      person: CouplesPersonRow
    }

/**
 * The couples landing must find the partner's couple WITHOUT relying on
 * Clerk's active Organization (a ticket sign-up may not make an invited org
 * active; SMD's application invitation joins none). When the session reaches
 * no entity at all, bind the user to the one couples entity whose partner row
 * carries their verified email, then resolve again. The claim and the role
 * grant below are unchanged.
 */
export async function resolveCouplesAccess(
  db: D1Database,
  locals: App.Locals
): Promise<CouplesAccess> {
  const first = await resolveCouplesAccessOnce(db, locals)
  if (first.kind !== 'redirect' || first.to !== NO_ACCESS_PATH) return first
  const portal = await getPortalClient(db, locals)
  if (!portal || portal.client) return first
  const bound = await bindUnboundUserToCouplesByEmail(db, portal.user)
  return bound ? resolveCouplesAccessOnce(db, locals) : first
}

async function resolveCouplesAccessOnce(
  db: D1Database,
  locals: App.Locals
): Promise<CouplesAccess> {
  // A holder, not a `let`: TypeScript does not see the closure's assignment
  // and would narrow a `let` to null.
  const found: { instance: CouplesInstanceRow | null } = { instance: null }
  const access = await resolveHostedAgentAccess(db, locals, {
    allowedRoles: [PARTNER_PRODUCT_ROLE],
    beforeRoleCheck: async ({ user, client }) => {
      found.instance = await getCouplesInstanceByEntity(db, client.id)
      if (found.instance) await claimPartnerByEmail(db, found.instance.id, user, client.id)
    },
  })
  const resolved = found.instance
  if (access.kind === 'redirect') {
    // The Hosted Agent landing sends a couples entity back here, so a refused
    // caller on a couples entity must go somewhere else or the two loop.
    return resolved && access.to === HOSTED_AGENT_LANDING_PATH
      ? { kind: 'redirect', to: NO_ACCESS_PATH }
      : access
  }
  if (!resolved) return { kind: 'redirect', to: HOSTED_AGENT_LANDING_PATH }
  const person = await getPersonForUser(db, resolved.id, access.user.id)
  if (!person) return { kind: 'redirect', to: NO_ACCESS_PATH }
  return {
    kind: 'allowed',
    user: access.user,
    client: access.client,
    subscription: access.subscription,
    instance: resolved,
    person,
  }
}
