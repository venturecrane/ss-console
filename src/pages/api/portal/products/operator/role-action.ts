import type { APIRoute } from 'astro'
import { captureError } from '../../../../../lib/observability/sentry'
import { getPortalClient } from '../../../../../lib/portal/session'
import { getProductSubscription, listProductRoles } from '../../../../../lib/portal/product-access'
import {
  grantProductRole,
  revokeProductRole,
  isOperatorRole,
  type OperatorRole,
} from '../../../../../lib/portal/product-roles-mutations'
import type { PortalUserRow } from '../../../../../lib/auth/clerk-bridge'
import type { Entity } from '../../../../../lib/db/entities'
import {
  buildRoleAuditEvent,
  recordRbacAuditEvent,
} from '../../../../../lib/portal/operator/rbac-audit'
import { recordPortalActionEvent } from '../../../../../lib/portal/operator/action-events'
import { isPeopleAccessOperable } from '../../../../../lib/portal/operator/people-access-gate'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/portal/products/operator/role-action
 *
 * Action endpoint for granting and revoking Operator product roles.
 * Driven by HTML <form method="POST"> submissions from the user-list
 * page; no JSON / fetch logic. Form fields:
 *
 *   action:  'grant' | 'revoke'  — which mutation to run
 *   userId:  TEXT                — target user's local users.id
 *   role:    'principal' | 'staff' | 'compliance'
 *
 * Authorization:
 *   - Clerk session required (middleware enforces)
 *   - The caller must hold the `principal` role on the active entity's
 *     Operator subscription
 *
 * Self-protection: a principal cannot revoke their own last principal
 * role (would lock the customer out of self-service management).
 *
 * Returns a 303 redirect back to the user-list page with a `status`
 * query param so the page can surface a banner.
 *
 * Audit: every successful grant or revoke emits an `audit:rbac_event`
 * log line via `recordRbacAuditEvent`. No-op cases (idempotent re-grant
 * of an active role, idempotent re-revoke of an already-revoked role)
 * are NOT audited — nothing changed. The lock-out self-protection
 * (`cannot_revoke_last_principal`) is also not audited; it never
 * mutated state.
 */

const PRODUCT_SLUG = 'operator'
const OPERATOR_LANDING = '/portal/products/operator'

// The users page is now instance-addressed. Redirect back to the addressed
// instance's users page; fall back to the bare chooser when the instance
// can't be determined from the form.
function usersUrl(instance: string | null): string {
  return instance ? `${OPERATOR_LANDING}/${instance}/settings/users` : OPERATOR_LANDING
}

function redirectToUsersPage(instance: string | null, status: string): Response {
  const target = `${usersUrl(instance)}?status=${encodeURIComponent(status)}`
  return new Response(null, {
    status: 303,
    headers: { Location: target },
  })
}

interface AuthorizedContext {
  user: PortalUserRow
  client: Entity
  orgId: string
  instance: string | null
}

/**
 * Resolve portal context and enforce principal-on-Operator
 * authorization. Returns a Response on failure (for the caller to
 * return directly) or the authorized context on success.
 */
async function authorize(
  locals: App.Locals,
  instance: string | null
): Promise<Response | AuthorizedContext> {
  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData) return errorResponse(401, 'Unauthorized')
  if (!portalData.client) return errorResponse(403, 'Forbidden')

  const { user, client } = portalData
  const callerRoles = await listProductRoles(env.DB, user.id, client.id, PRODUCT_SLUG)
  if (!callerRoles.includes('principal')) return errorResponse(403, 'Forbidden')

  const subscription = await getProductSubscription(env.DB, client.id, PRODUCT_SLUG)
  if (!subscription) return errorResponse(404, 'No active subscription')

  // Layer-1 authority gate (ADR 0041): roles live in the people_access domain.
  // At launch (managed posture) the client org does not operate its own roster
  // — SMD does. Refuse the mutation server-side rather than trust the portal's
  // read-only render. Mirrors the connectors-secret precedent.
  if (!(await isPeopleAccessOperable(env.DB, client.id))) {
    return redirectToUsersPage(instance, 'not_permitted')
  }

  return { user, client, orgId: user.org_id, instance }
}

interface ParsedAction {
  kind: 'grant' | 'revoke'
  targetUserId: string
  role: OperatorRole
}

function parseForm(formData: FormData): ParsedAction | string {
  const action = formData.get('action')
  const targetUserId = formData.get('userId')
  const role = formData.get('role')
  if (action !== 'grant' && action !== 'revoke') return 'invalid_action'
  if (typeof targetUserId !== 'string' || targetUserId === '') return 'invalid_user'
  if (!isOperatorRole(role)) return 'invalid_role'
  return { kind: action, targetUserId, role }
}

interface TargetUserRow {
  id: string
  email: string
}

async function loadTargetUser(userId: string, orgId: string): Promise<TargetUserRow | null> {
  return await env.DB.prepare('SELECT id, email FROM users WHERE id = ? AND org_id = ?')
    .bind(userId, orgId)
    .first<TargetUserRow>()
}

/**
 * Self-protection check: when a principal tries to revoke their own
 * `principal` role, ensure another active principal exists on the same
 * (entity, product) tuple. Returns true if the revoke is safe to
 * proceed, false if it would lock the customer out.
 */
async function isSafeSelfPrincipalRevoke(
  ctx: AuthorizedContext,
  parsed: ParsedAction
): Promise<boolean> {
  if (parsed.role !== 'principal' || parsed.targetUserId !== ctx.user.id) return true
  const row = await env.DB.prepare(
    `SELECT COUNT(*) AS n FROM product_roles
      WHERE entity_id = ? AND product_slug = ? AND role = 'principal'
        AND user_id != ?
        AND revoked_at IS NULL`
  )
    .bind(ctx.client.id, PRODUCT_SLUG, ctx.user.id)
    .first<{ n: number }>()
  return (row?.n ?? 0) > 0
}

async function emitRoleAudit(
  ctx: AuthorizedContext,
  parsed: ParsedAction,
  target: TargetUserRow,
  subAction: 'role_granted' | 'role_revoked'
): Promise<void> {
  await recordRbacAuditEvent(
    buildRoleAuditEvent({
      subAction,
      customer_id: ctx.client.id,
      product_slug: PRODUCT_SLUG,
      actorUserId: ctx.user.id,
      actorClerkUserId: ctx.user.clerk_user_id,
      actorEmail: ctx.user.email,
      targetUserId: target.id,
      targetEmail: target.email,
      role: parsed.role,
    })
  )

  // Durable ledger (0099) — primary record; the tail-log line above is the
  // secondary sink. The role mutation already landed, so a ledger failure
  // must not turn a completed action into an error response.
  try {
    await recordPortalActionEvent(env.DB, {
      entity_id: ctx.client.id,
      customer_slug: ctx.instance,
      action_type: subAction,
      actor_user_id: ctx.user.id,
      actor_email: ctx.user.email,
      actor_role: 'principal',
      source: 'portal',
      target: target.email,
      status: null,
      metadata: { role: parsed.role, target_user_id: target.id },
    })
  } catch (err) {
    console.error('role-action: failed to record portal_action_events row', err)
    captureError(err, 'portal.role-action.ledger')
  }
}

async function handleRevoke(
  ctx: AuthorizedContext,
  parsed: ParsedAction,
  target: TargetUserRow
): Promise<Response> {
  if (!(await isSafeSelfPrincipalRevoke(ctx, parsed))) {
    return redirectToUsersPage(ctx.instance, 'cannot_revoke_last_principal')
  }
  const changed = await revokeProductRole(env.DB, {
    userId: parsed.targetUserId,
    entityId: ctx.client.id,
    productSlug: PRODUCT_SLUG,
    role: parsed.role,
  })
  if (changed) await emitRoleAudit(ctx, parsed, target, 'role_revoked')
  return redirectToUsersPage(ctx.instance, changed ? 'revoked' : 'no_change')
}

async function handleGrant(
  ctx: AuthorizedContext,
  parsed: ParsedAction,
  target: TargetUserRow
): Promise<Response> {
  const changed = await grantProductRole(env.DB, {
    orgId: ctx.orgId,
    userId: parsed.targetUserId,
    entityId: ctx.client.id,
    productSlug: PRODUCT_SLUG,
    role: parsed.role,
    grantedBy: ctx.user.id,
  })
  if (changed) await emitRoleAudit(ctx, parsed, target, 'role_granted')
  return redirectToUsersPage(ctx.instance, changed ? 'granted' : 'no_change')
}

export const POST: APIRoute = async ({ request, locals }) => {
  const formData = await request.formData()
  const instanceRaw = formData.get('instance')
  const instance = typeof instanceRaw === 'string' && instanceRaw !== '' ? instanceRaw : null

  const ctxOrResponse = await authorize(locals, instance)
  if (ctxOrResponse instanceof Response) return ctxOrResponse
  const ctx = ctxOrResponse

  const parsed = parseForm(formData)
  if (typeof parsed === 'string') return redirectToUsersPage(instance, parsed)

  const target = await loadTargetUser(parsed.targetUserId, ctx.orgId)
  if (target === null) {
    return redirectToUsersPage(instance, 'user_not_found')
  }

  return parsed.kind === 'revoke'
    ? handleRevoke(ctx, parsed, target)
    : handleGrant(ctx, parsed, target)
}
