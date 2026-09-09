/**
 * Operator change-request model (ADR 0041 §4.3). The Read + Request half of the
 * dual-mode surface: when a domain is SMD-operated, the client files a change
 * request here instead of editing directly, and the admin change-request inbox
 * reads them.
 *
 * Console-side control-plane store (`operator_change_requests`) — not runtime
 * D1 — so the admin inbox legitimately reads across customers. The `domain` is
 * validated against the switchable authority domains: a request only makes
 * sense for a domain the client cannot currently operate.
 *
 * Parse-and-validate on the way in (never cast a client-supplied domain or
 * status); read helpers return typed rows.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { isSwitchableDomain, type SwitchableAuthorityDomain } from '../../operator/authority'

export type ChangeRequestStatus = 'open' | 'acknowledged' | 'resolved' | 'declined'

export interface ChangeRequestRow {
  id: number
  entity_id: string
  customer_slug: string
  domain: SwitchableAuthorityDomain
  requested_by_user_id: string
  requested_by_email: string
  summary: string
  status: ChangeRequestStatus
  resolved_by_email: string | null
  resolved_at: string | null
  resolution_note: string | null
  created_at: string
}

export interface CreateChangeRequestInput {
  entity_id: string
  customer_slug: string
  domain: string
  requested_by_user_id: string
  requested_by_email: string
  summary: string
}

export type CreateChangeRequestResult =
  { ok: true; id: number } | { ok: false; error: 'invalid_domain' | 'empty_summary' }

/** Max accepted request text — generous for a paragraph, rejects pasted blobs. */
const MAX_SUMMARY_LENGTH = 4000

/**
 * File a client change request. Validates the domain (must be a switchable
 * authority domain) and the summary (non-empty, bounded) without casting.
 */
export async function createChangeRequest(
  db: D1Database,
  input: CreateChangeRequestInput
): Promise<CreateChangeRequestResult> {
  if (!isSwitchableDomain(input.domain)) return { ok: false, error: 'invalid_domain' }
  const summary = input.summary.trim()
  if (summary.length === 0 || summary.length > MAX_SUMMARY_LENGTH) {
    return { ok: false, error: 'empty_summary' }
  }
  const result = await db
    .prepare(
      `INSERT INTO operator_change_requests
         (entity_id, customer_slug, domain, requested_by_user_id, requested_by_email, summary)
       VALUES (?, ?, ?, ?, ?, ?)
       RETURNING id`
    )
    .bind(
      input.entity_id,
      input.customer_slug,
      input.domain,
      input.requested_by_user_id,
      input.requested_by_email,
      summary
    )
    .first<{ id: number }>()
  return { ok: true, id: result?.id ?? 0 }
}

/**
 * The admin inbox: open (unhandled) change requests across all customers, most
 * recent first. Fleet-wide read of a console-side table — not runtime D1.
 */
export async function listOpenChangeRequests(
  db: D1Database,
  limit = 100
): Promise<ChangeRequestRow[]> {
  const { results } = await db
    .prepare(
      `SELECT * FROM operator_change_requests
        WHERE status IN ('open', 'acknowledged') ORDER BY created_at DESC LIMIT ?`
    )
    .bind(limit)
    .all<ChangeRequestRow>()
  return results ?? []
}

export interface ResolveChangeRequestInput {
  id: number
  status: ChangeRequestStatus
  resolved_by_email: string
  resolution_note: string | null
}

/**
 * SMD moves a request through its lifecycle. Stamps resolver + timestamp for
 * the terminal states (resolved/declined); 'acknowledged' records receipt
 * without closing. Returns false when the id does not exist.
 */
export async function updateChangeRequestStatus(
  db: D1Database,
  input: ResolveChangeRequestInput
): Promise<boolean> {
  const terminal = input.status === 'resolved' || input.status === 'declined'
  const resolvedAt = terminal ? new Date().toISOString() : null
  const result = await db
    .prepare(
      `UPDATE operator_change_requests
          SET status = ?,
              resolved_by_email = ?,
              resolved_at = ?,
              resolution_note = ?
        WHERE id = ?`
    )
    .bind(
      input.status,
      terminal ? input.resolved_by_email : null,
      resolvedAt,
      input.resolution_note,
      input.id
    )
    .run()
  return (result.meta.changes ?? 0) > 0
}
