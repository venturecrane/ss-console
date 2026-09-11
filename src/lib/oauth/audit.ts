/**
 * OAuth audit event emission.
 *
 * Every invocation of the portal OAuth callback
 * (src/pages/portal/products/operator/oauth/[connector]/callback.ts)
 * emits an audit event:
 *
 *   - `oauth-callback.token-issued`  — state validated, token exchanged
 *     and accepted by the store.
 *   - `oauth-callback.token-rejected` — state invalid, provider unknown,
 *     reviewer mismatch, exchange failed, or store rejected the token.
 *
 * v1 ships a console-only writer. The audit log persistence layer is
 * tracked in issue #891 (D1 audit_log table per
 * docs/specs/operator/d1-schema.md). When #891 lands, swap
 * `emitAuditEvent` to write rows there. The shape of the event payload
 * is stable so the swap is a one-line change inside this file.
 *
 * Token material is NEVER included in audit events. Only the metadata
 * fields below.
 */

export type OAuthAuditAction = 'token-issued' | 'token-rejected'

export interface OAuthAuditEvent {
  skill: 'oauth-callback'
  action: OAuthAuditAction
  customer_id: string | null
  provider: string | null
  reviewer_id: string | null
  reason?: string
  ts: string
}

export interface EmitOAuthAuditInput {
  action: OAuthAuditAction
  customer_id: string | null
  provider: string | null
  reviewer_id: string | null
  reason?: string
}

export function emitAuditEvent(input: EmitOAuthAuditInput): Promise<void> {
  const event: OAuthAuditEvent = {
    skill: 'oauth-callback',
    action: input.action,
    customer_id: input.customer_id,
    provider: input.provider,
    reviewer_id: input.reviewer_id,
    ts: new Date().toISOString(),
  }
  if (input.reason) event.reason = input.reason

  // TODO(#891): persist to D1 audit_log table once that issue lands.
  console.log('[oauth/audit]', JSON.stringify(event))
  return Promise.resolve()
}
