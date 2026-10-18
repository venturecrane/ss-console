import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { clerkClient } from '@clerk/astro/server'
import { requireAdminSession } from '../../../../../../lib/auth/admin-session'
import {
  getCouplesInstance,
  getPersonById,
  markPersonInvited,
  setEntityClerkOrg,
} from '../../../../../../lib/db/couples-onboarding'
import { getPortalBaseUrl } from '../../../../../../lib/config/app-url'
import { captureError } from '../../../../../../lib/observability/sentry'
import { trimString } from '../../../../../../lib/api/helpers'
import { COUPLES_LANDING_PATH } from '../../../../../../lib/portal/couples-access'

/**
 * POST /api/admin/hosted-agent/couples/:id/invite (ADR 0090 decision 4)
 *
 * Invites ONE partner (form field `person_id`) to the couple's portal. A port
 * of the Operator's Clerk Organization invitation flow
 * (src/pages/api/portal/products/operator/invitations.ts), driven by SMD from
 * the admin console instead of by a principal, because a couples instance has
 * no principal: both partners are equals.
 *
 *   1. The couple's entity gets a Clerk Organization the first time anyone is
 *      invited (entities.clerk_org_id), created by the inviting admin.
 *   2. Clerk sends the invitation (role org:member, as the Operator flow).
 *   3. On first sign-in the bridge creates the local user, the org resolves the
 *      entity, and the couples gate claims the partner row by verified email
 *      (src/lib/portal/couples-access.ts). No role is pre-granted here.
 *
 * Sending is an explicit per-partner click, never part of creating the
 * instance. Clerk errors map to a status banner; the invitee's address is not
 * logged.
 */

function back(id: string, st: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `/admin/hosted-agent/couples/${id}?st=${encodeURIComponent(st)}` },
  })
}

async function adminClerkUserId(userId: string): Promise<string | null> {
  const row = await env.DB.prepare('SELECT clerk_user_id FROM users WHERE id = ?')
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

function mapClerkError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err)
  if (/already a member/i.test(message)) return 'already_member'
  if (/already invited|duplicate/i.test(message)) return 'already_invited'
  return 'invite_failed'
}

export const POST: APIRoute = async (context) => {
  const auth = requireAdminSession(context.locals)
  if (!auth.ok) return auth.response

  const id = context.params.id
  if (typeof id !== 'string' || id.length === 0) return back('', 'invalid')
  const instance = await getCouplesInstance(env.DB, id)
  if (!instance) return back(id, 'invalid')

  const personId = trimString((await context.request.formData()).get('person_id'))
  const person = personId ? await getPersonById(env.DB, personId) : null
  if (!person || person.intake_id !== instance.id) return back(id, 'invalid')

  const inviter = await adminClerkUserId(auth.session.userId)
  if (!inviter) return back(id, 'no_clerk_user')

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
  } catch (err) {
    const st = mapClerkError(err)
    if (st === 'invite_failed') {
      console.error('[admin/couples] invitation failed', {
        intake_id: id,
        person: person.person_key,
      })
      captureError(err, 'admin.couples.invite')
    }
    return back(id, st)
  }
  return back(id, 'invited')
}
