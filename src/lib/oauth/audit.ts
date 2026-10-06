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
 * Each event is INSERTed into the append-only `oauth_callback_audit` table
 * (migration 0121). It used to be console-only behind a deferral note naming #891,
 * which closed on 2026-05-22 with nothing persisted (code review
 * 2026-10-06, I12). A failed insert never breaks the callback (the grant or
 * the rejection still completes for the client), but it is logged and
 * captured to Sentry, never swallowed: a missing audit row must be visible.
 *
 * Token material is NEVER included in audit events. Only the metadata
 * fields below.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { captureError } from '../observability/sentry'

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

export async function emitAuditEvent(db: D1Database, input: EmitOAuthAuditInput): Promise<void> {
  const event: OAuthAuditEvent = {
    skill: 'oauth-callback',
    action: input.action,
    customer_id: input.customer_id,
    provider: input.provider,
    reviewer_id: input.reviewer_id,
    ts: new Date().toISOString(),
  }
  if (input.reason) event.reason = input.reason

  console.log('[oauth/audit]', JSON.stringify(event))
  try {
    await db
      .prepare(
        'INSERT INTO oauth_callback_audit ' +
          '(skill, action, customer_id, provider, reviewer_id, reason, ts) ' +
          'VALUES (?, ?, ?, ?, ?, ?, ?)'
      )
      .bind(
        event.skill,
        event.action,
        event.customer_id,
        event.provider,
        event.reviewer_id,
        event.reason ?? null,
        event.ts
      )
      .run()
  } catch (err) {
    console.error('[oauth/audit] persist failed', JSON.stringify(event), err)
    captureError(err, 'oauth-callback-audit')
  }
}
