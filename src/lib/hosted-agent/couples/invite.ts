/**
 * Send ONE couples partner's portal invitation (ADR 0090 decision 4).
 *
 * A port of the Operator's Clerk Organization invitation flow
 * (src/pages/api/portal/products/operator/invitations.ts), driven by SMD
 * rather than by a principal, because a couples instance has no principal:
 * both partners are equals.
 *
 *   1. The couple's entity gets a Clerk Organization the first time anyone is
 *      invited (entities.clerk_org_id), created by the inviting SMD user.
 *   2. Clerk sends the invitation (role org:member, as the Operator flow).
 *   3. On first sign-in the bridge creates the local user, the org resolves the
 *      entity, and the couples gate claims the partner row by verified email
 *      (src/lib/portal/couples-access.ts). No role is pre-granted here.
 *
 * Two callers, one behavior: the admin-console button
 * (/api/admin/hosted-agent/couples/:id/invite) and the SMD agent route
 * (/api/internal/agent/couples/:id/invite). The invitee's address is never
 * logged.
 */

import type { APIContext } from 'astro'
import type { D1Database } from '@cloudflare/workers-types'
import { env } from 'cloudflare:workers'
import { clerkClient } from '@clerk/astro/server'
import {
  getCouplesInstance,
  getPersonById,
  markPersonInvited,
  setEntityClerkOrg,
} from '../../db/couples-onboarding'
import { getPortalBaseUrl } from '../../config/app-url'
import { captureError } from '../../observability/sentry'
import { COUPLES_LANDING_PATH } from '../../portal/couples-access'

export type CouplesInviteResult =
  'invited' | 'already_member' | 'already_invited' | 'invite_failed' | 'invalid'

/** Which surface sent the invitation; tags the error report, never the invitee. */
export type CouplesInviteCaller = 'admin' | 'agent'

const CAPTURE_TAG: Record<CouplesInviteCaller, string> = {
  admin: 'admin.couples.invite',
  agent: 'agent.couples.invite',
}

export interface CouplesInviteOutcome {
  result: CouplesInviteResult
  /** Clerk invitation id, present only when result is 'invited'. */
  invitationId?: string
}

/** The local user's Clerk user id, or null when the row has none. */
export async function clerkUserIdFor(db: D1Database, userId: string): Promise<string | null> {
  const row = await db
    .prepare('SELECT clerk_user_id FROM users WHERE id = ?')
    .bind(userId)
    .first<{ clerk_user_id: string | null }>()
  return row?.clerk_user_id ?? null
}

async function ensureClerkOrg(
  context: APIContext,
  entityId: string,
  existing: string | null,
  name: string,
  createdBy: string
): Promise<string> {
  if (existing) return existing
  const org = await clerkClient(context).organizations.createOrganization({ name, createdBy })
  await setEntityClerkOrg(env.DB, entityId, org.id)
  // A concurrent invite may have bound a different org first; use the bound one.
  const bound = await env.DB.prepare('SELECT clerk_org_id FROM entities WHERE id = ?')
    .bind(entityId)
    .first<{ clerk_org_id: string | null }>()
  return bound?.clerk_org_id ?? org.id
}

function mapClerkError(err: unknown): CouplesInviteResult {
  const message = err instanceof Error ? err.message : String(err)
  if (/already a member/i.test(message)) return 'already_member'
  if (/already invited|duplicate/i.test(message)) return 'already_invited'
  return 'invite_failed'
}

export async function inviteCouplesPartner(
  context: APIContext,
  input: {
    instanceId: string
    personId: string
    inviterClerkUserId: string
    source: CouplesInviteCaller
  }
): Promise<CouplesInviteOutcome> {
  const instance = await getCouplesInstance(env.DB, input.instanceId)
  if (!instance) return { result: 'invalid' }
  const person = await getPersonById(env.DB, input.personId)
  if (!person || person.intake_id !== instance.id) return { result: 'invalid' }

  const inviter = input.inviterClerkUserId
  const portalBase = getPortalBaseUrl(env) ?? 'https://portal.smd.services'
  try {
    const orgId = await ensureClerkOrg(
      context,
      instance.entity_id,
      instance.clerk_org_id,
      instance.entity_name,
      inviter
    )
    const invitation = await clerkClient(context).organizations.createOrganizationInvitation({
      organizationId: orgId,
      emailAddress: person.email,
      role: 'org:member',
      inviterUserId: inviter,
      redirectUrl: `${portalBase}${COUPLES_LANDING_PATH}`,
    })
    await markPersonInvited(env.DB, person.id, invitation.id)
    return { result: 'invited', invitationId: invitation.id }
  } catch (err) {
    const result = mapClerkError(err)
    if (result === 'invite_failed') {
      console.error('[couples/invite] invitation failed', {
        caller: input.source,
        intake_id: instance.id,
        person: person.person_key,
      })
      captureError(err, CAPTURE_TAG[input.source])
    }
    return { result }
  }
}
