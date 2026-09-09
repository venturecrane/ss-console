/**
 * RBAC audit emission for Operator user-management actions.
 *
 * Every state-changing identity event on the Operator Users/Team page
 * emits a structured `audit:rbac_event` log line. The shape mirrors
 * `recordCustomerYamlUpdateAudit` (PR #877) so the same Hermes-side
 * tail-log drain that will persist those to the per-customer audit_log
 * (#821 + #891) will persist these too without a new ingestion path.
 *
 * Per #880 AC: "Audit log captures every user-management event."
 * Today the portal Worker cannot bind to the per-customer Hermes D1
 * (the bridge tracked in #821 has not landed), so the audit row's
 * destination is the Worker tail logs. The line prefix and JSON shape
 * are stable; when the bridge lands, only the destination changes.
 *
 * RBAC action vocabulary mirrored from
 * `operator/adapter/audit_log.py::ACCEPTED_ACTION_TYPES` (the
 * `RBAC_EVENT` class). The viewer-side constant lives in
 * `src/lib/portal/operator/audit.ts`; both reference the same
 * writer-side spec.
 *
 * Token material is NEVER included in audit events. Only the metadata
 * fields below.
 *
 * (The matter-assignment, PTO, and notification-preference subActions
 * were removed per ADR 0052 along with those portal surfaces.)
 */

/**
 * Sub-classes of RBAC_EVENT this writer emits today. The audit_log
 * column is the broader `RBAC_EVENT`; this nested `subAction` is
 * persisted in the row's metadata so a reviewer can tell a grant from
 * a revoke from an invite.
 *
 *   role_granted             — Principal granted a product role to a user
 *   role_revoked             — Principal revoked a product role from a user
 *   invite_sent              — Principal sent a Clerk Organization invitation
 */
export type RbacSubAction = 'role_granted' | 'role_revoked' | 'invite_sent'

/**
 * @public Closed vocabulary. tests/portal-rbac-audit.test.ts imports it and pins the set. No
 * runtime caller, by design.
 */
export const RBAC_SUB_ACTIONS: readonly RbacSubAction[] = [
  'role_granted',
  'role_revoked',
  'invite_sent',
] as const

/**
 * Common header for every RBAC audit event. The viewer's `AuditEntry`
 * shape (src/lib/portal/operator/audit.ts) consumes these as
 * `action='RBAC_EVENT'`, `actor`, `actorRole='principal'`, and
 * `decision='allow'`. The customer_id maps to the per-customer audit_log
 * routing key.
 */
export interface RbacAuditEventBase {
  type: 'audit:rbac_event'
  subAction: RbacSubAction
  customer_id: string
  product_slug: string
  actorUserId: string
  actorClerkUserId: string | null
  actorEmail: string
  timestamp: string
}

export interface RoleGrantedAuditEvent extends RbacAuditEventBase {
  subAction: 'role_granted'
  targetUserId: string
  targetEmail: string
  role: string
}

export interface RoleRevokedAuditEvent extends RbacAuditEventBase {
  subAction: 'role_revoked'
  targetUserId: string
  targetEmail: string
  role: string
}

export interface InviteSentAuditEvent extends RbacAuditEventBase {
  subAction: 'invite_sent'
  inviteeEmail: string
  clerkOrgId: string
  clerkInvitationId: string
}

export type RbacAuditEvent = RoleGrantedAuditEvent | RoleRevokedAuditEvent | InviteSentAuditEvent

export interface BuildRoleEventInput {
  subAction: 'role_granted' | 'role_revoked'
  customer_id: string
  product_slug: string
  actorUserId: string
  actorClerkUserId: string | null
  actorEmail: string
  targetUserId: string
  targetEmail: string
  role: string
  now?: Date
}

export function buildRoleAuditEvent(
  input: BuildRoleEventInput
): RoleGrantedAuditEvent | RoleRevokedAuditEvent {
  const timestamp = (input.now ?? new Date()).toISOString()
  return {
    type: 'audit:rbac_event',
    subAction: input.subAction,
    customer_id: input.customer_id,
    product_slug: input.product_slug,
    actorUserId: input.actorUserId,
    actorClerkUserId: input.actorClerkUserId,
    actorEmail: input.actorEmail,
    targetUserId: input.targetUserId,
    targetEmail: input.targetEmail,
    role: input.role,
    timestamp,
  }
}

export interface BuildInviteEventInput {
  customer_id: string
  product_slug: string
  actorUserId: string
  actorClerkUserId: string | null
  actorEmail: string
  inviteeEmail: string
  clerkOrgId: string
  clerkInvitationId: string
  now?: Date
}

export function buildInviteAuditEvent(input: BuildInviteEventInput): InviteSentAuditEvent {
  const timestamp = (input.now ?? new Date()).toISOString()
  return {
    type: 'audit:rbac_event',
    subAction: 'invite_sent',
    customer_id: input.customer_id,
    product_slug: input.product_slug,
    actorUserId: input.actorUserId,
    actorClerkUserId: input.actorClerkUserId,
    actorEmail: input.actorEmail,
    inviteeEmail: input.inviteeEmail,
    clerkOrgId: input.clerkOrgId,
    clerkInvitationId: input.clerkInvitationId,
    timestamp,
  }
}

/**
 * Emit a single RBAC audit line. Mirrors the
 * `recordCustomerYamlUpdateAudit` contract: single `console.info` with a
 * JSON-serialized payload whose `type` field is stable for the tail-log
 * drain.
 *
 * Async to match the eventual bridge shape so callers stay stable
 * when #821 lands. The Promise.resolve keeps the body sync today.
 */
// eslint-disable-next-line @typescript-eslint/require-await
export async function recordRbacAuditEvent(event: RbacAuditEvent): Promise<void> {
  console.info(JSON.stringify(event))
}
