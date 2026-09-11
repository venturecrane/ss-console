import type { APIRoute, APIContext } from 'astro'
import { clerkClient } from '@clerk/astro/server'
import { getPortalClient } from '../../../../../lib/portal/session'
import { getProductSubscription, listProductRoles } from '../../../../../lib/portal/product-access'
import type { PortalUserRow } from '../../../../../lib/auth/clerk-bridge'
import type { Entity } from '../../../../../lib/db/entities'
import {
  buildInviteAuditEvent,
  recordRbacAuditEvent,
} from '../../../../../lib/portal/operator/rbac-audit'
import { isPeopleAccessOperable } from '../../../../../lib/portal/operator/people-access-gate'
import { recordPortalActionEvent } from '../../../../../lib/portal/operator/action-events'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/portal/products/operator/invitations
 *
 * Creates a Clerk Organization invitation. After the invitee accepts
 * and signs in for the first time, the JIT bridge (clerk-bridge.ts)
 * creates their local users row, and the principal returns to the
 * Users page to grant them a product role. Two-step UX intentionally
 * — pre-assigning roles via invitation metadata + webhook is deferred
 * to a future slice.
 *
 * Form fields:
 *   email: TEXT  — invitee email address
 *
 * Authorization (same as role-action):
 *   - Clerk session required (middleware)
 *   - Active Operator subscription on this entity
 *   - Caller holds the 'principal' role on (entity, 'operator')
 *
 * Also requires that the entity has been bound to a Clerk Organization
 * (entities.clerk_org_id IS NOT NULL). If not bound yet, redirects
 * back with status=no_clerk_org so the principal sees a clear message.
 *
 * Clerk's Organization role for the invitation is hardcoded to
 * 'org:member'. Org-admin Clerk role is reserved for SMD-side
 * accounts. Product roles (principal | operator | compliance) are a
 * separate axis managed via role-action.ts after the invitee accepts.
 *
 * Audit: every successful invitation emits an `audit:rbac_event` log
 * line with `subAction: 'invite_sent'` via `recordRbacAuditEvent`.
 * Failed invitations (validation, Clerk rejection, duplicate) are not
 * audited — nothing was sent to the invitee.
 */

const PRODUCT_SLUG = 'operator'
const OPERATOR_LANDING = '/portal/products/operator'

// The users page is now instance-addressed. Redirect back to the addressed
// instance's users page; fall back to the bare chooser when the instance
// can't be determined from the form.
function usersUrl(instance: string | null): string {
  return instance ? `${OPERATOR_LANDING}/${instance}/settings/users` : OPERATOR_LANDING
}

function redirectWithStatus(instance: string | null, status: string): Response {
  const target = `${usersUrl(instance)}?status=${encodeURIComponent(status)}`
  return new Response(null, {
    status: 303,
    headers: { Location: target },
  })
}

function jsonError(status: number, message: string): Response {
  return errorResponse(status, message)
}

interface AuthorizedContext {
  user: PortalUserRow
  client: Entity
  instance: string | null
}

async function authorize(
  locals: App.Locals,
  instance: string | null
): Promise<Response | AuthorizedContext> {
  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData) return jsonError(401, 'Unauthorized')
  if (!portalData.client) return jsonError(403, 'Forbidden')

  const { user, client } = portalData
  const roles = await listProductRoles(env.DB, user.id, client.id, PRODUCT_SLUG)
  if (!roles.includes('principal')) return jsonError(403, 'Forbidden')

  const subscription = await getProductSubscription(env.DB, client.id, PRODUCT_SLUG)
  if (!subscription) return jsonError(404, 'No active subscription')

  // Layer-1 authority gate (ADR 0041): inviting people is the people_access
  // domain. At launch (managed posture) SMD operates the roster; refuse the
  // mutation server-side rather than trust the portal's read-only render.
  if (!(await isPeopleAccessOperable(env.DB, client.id))) {
    return redirectWithStatus(instance, 'not_permitted')
  }

  return { user, client, instance }
}

// RFC 5322-lite email check. Sufficient guard against accidental
// nonsense (the canonical validator is Clerk itself, which will
// reject malformed addresses with a clearer error).
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function parseEmail(formData: FormData): string | null {
  const raw = formData.get('email')
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!EMAIL_RE.test(trimmed)) return null
  return trimmed
}

export const POST: APIRoute = async (context: APIContext) => {
  const formData = await context.request.formData()
  const instanceRaw = formData.get('instance')
  const instance = typeof instanceRaw === 'string' && instanceRaw !== '' ? instanceRaw : null

  const ctxOrResponse = await authorize(context.locals, instance)
  if (ctxOrResponse instanceof Response) return ctxOrResponse
  const ctx = ctxOrResponse

  if (!ctx.client.clerk_org_id) return redirectWithStatus(instance, 'no_clerk_org')
  if (!ctx.user.clerk_user_id) return redirectWithStatus(instance, 'no_clerk_user')

  const email = parseEmail(formData)
  if (!email) return redirectWithStatus(instance, 'invalid_email')

  const redirectUrl = `${new URL(context.request.url).origin}/portal/products/operator`
  let invitationId: string
  try {
    const invitation = await clerkClient(context).organizations.createOrganizationInvitation({
      organizationId: ctx.client.clerk_org_id,
      emailAddress: email,
      role: 'org:member',
      inviterUserId: ctx.user.clerk_user_id,
      redirectUrl,
    })
    invitationId = invitation.id
  } catch (err) {
    // Clerk's typed errors: duplicate, already-member, etc. We don't
    // surface raw Clerk error codes to the principal. Instead map a
    // small set to friendly status values and fall back to a generic
    // 'invite_failed' otherwise.
    const message = err instanceof Error ? err.message : String(err)
    if (/already a member/i.test(message)) return redirectWithStatus(instance, 'already_member')
    if (/already invited|duplicate/i.test(message))
      return redirectWithStatus(instance, 'already_invited')
    // The invitee's address is client PII; the customer id is enough to find
    // the attempt, and the message carries Clerk's reason.
    console.error('Clerk invitation failed', { customer_id: ctx.client.id, message })
    return redirectWithStatus(instance, 'invite_failed')
  }

  await recordRbacAuditEvent(
    buildInviteAuditEvent({
      customer_id: ctx.client.id,
      product_slug: PRODUCT_SLUG,
      actorUserId: ctx.user.id,
      actorClerkUserId: ctx.user.clerk_user_id,
      actorEmail: ctx.user.email,
      inviteeEmail: email,
      clerkOrgId: ctx.client.clerk_org_id,
      clerkInvitationId: invitationId,
    })
  )

  // Durable ledger (0099) — primary record; the tail-log line above is the
  // secondary sink. The invitation already went out, so a ledger failure
  // must not surface as an error to the principal.
  try {
    await recordPortalActionEvent(env.DB, {
      entity_id: ctx.client.id,
      customer_slug: ctx.instance,
      action_type: 'invite_sent',
      actor_user_id: ctx.user.id,
      actor_email: ctx.user.email,
      actor_role: 'principal',
      source: 'portal',
      target: email,
      status: null,
      metadata: { clerk_invitation_id: invitationId, clerk_org_id: ctx.client.clerk_org_id },
    })
  } catch (err) {
    console.error('invitations: failed to record portal_action_events row', err)
  }

  return redirectWithStatus(instance, 'invited')
}
