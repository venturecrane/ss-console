/**
 * Data access for `scan_requests` (#598).
 *
 * The Engine 1 inbound diagnostic flow needs a state machine for "someone
 * submitted the form -> we sent a magic link -> they clicked -> we ran
 * the scan -> we emailed the report." See migrations/0029_create_scan_requests.sql
 * for field semantics and lifecycle.
 *
 * All queries are tenant-scoped only implicitly: the table holds public
 * inbound submissions, not per-org artifacts. The org id used downstream
 * (when a verified scan creates an entity) comes from `ORG_ID` constants,
 * not from the scan_request row itself.
 */

import { CONTROL_CHAR_RE } from '../scan/normalize'

export type ScanStatus =
  | 'pending_verification'
  | 'verified'
  | 'completed'
  | 'thin_footprint'
  | 'failed'

export interface ScanRequest {
  id: string
  email: string
  domain: string
  linkedin_url: string | null
  verification_token_hash: string
  verified_at: string | null
  scan_started_at: string | null
  scan_completed_at: string | null
  scan_status: ScanStatus
  thin_footprint_skipped: number
  entity_id: string | null
  email_sent_at: string | null
  request_ip: string | null
  error_message: string | null
  /**
   * Structured failure-reason. See migrations/0030_scan_status_reason.sql.
   *
   *   - scan_status='thin_footprint': gate reason
   *     ('no_website_no_places' | 'no_website_low_reviews' |
   *      'no_strict_places_match')
   *   - scan_status='failed': '<module>: <truncated-error-message>'
   *   - otherwise: null
   */
  scan_status_reason: string | null
  /**
   * Cloudflare Workflow instance id (#614). Populated by /api/scan/verify
   * after env.SCAN_WORKFLOW.create returns. Lets operators look up a
   * running scan in the Cloudflare dashboard. NULL for rows created on
   * the old ctx.waitUntil path or for the brief window between
   * markScanVerified and the create() call returning.
   */
  workflow_run_id: string | null
  created_at: string
}

export interface CreateScanRequestData {
  email: string
  domain: string
  linkedin_url?: string | null
  verification_token_hash: string
  request_ip?: string | null
}

/**
 * Create a new scan_request row in the `pending_verification` state.
 *
 * The email and domain are stored as supplied (already lowercased + trimmed
 * by the caller — see `src/lib/scan/normalize.ts`). The verification token
 * itself is never stored — only the SHA-256 hash. The raw token is emailed
 * once and forgotten.
 */
export async function createScanRequest(
  db: D1Database,
  data: CreateScanRequestData
): Promise<ScanRequest> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  // Defensive — these should already be normalized but a control char in a
  // request_ip from a header would still poison logs.
  if (CONTROL_CHAR_RE.test(data.email)) {
    throw new Error('email contains control characters')
  }
  if (CONTROL_CHAR_RE.test(data.domain)) {
    throw new Error('domain contains control characters')
  }

  await db
    .prepare(
      `INSERT INTO scan_requests (
        id, email, domain, linkedin_url, verification_token_hash,
        scan_status, request_ip, created_at
      ) VALUES (?, ?, ?, ?, ?, 'pending_verification', ?, ?)`
    )
    .bind(
      id,
      data.email,
      data.domain,
      data.linkedin_url ?? null,
      data.verification_token_hash,
      data.request_ip ?? null,
      now
    )
    .run()

  const row = await getScanRequest(db, id)
  if (!row) throw new Error('Failed to retrieve created scan_request')
  return row
}

export async function getScanRequest(db: D1Database, id: string): Promise<ScanRequest | null> {
  const row = await db
    .prepare('SELECT * FROM scan_requests WHERE id = ?')
    .bind(id)
    .first<ScanRequest>()
  return row ?? null
}

export async function getScanRequestByTokenHash(
  db: D1Database,
  tokenHash: string
): Promise<ScanRequest | null> {
  const row = await db
    .prepare('SELECT * FROM scan_requests WHERE verification_token_hash = ?')
    .bind(tokenHash)
    .first<ScanRequest>()
  return row ?? null
}

/**
 * Mark a row as verified. Returns the updated row. Idempotent — re-clicking
 * the link after verification is a no-op (verified_at stays at first click).
 */
export async function markScanVerified(db: D1Database, id: string): Promise<ScanRequest | null> {
  const now = new Date().toISOString()
  await db
    .prepare(
      `UPDATE scan_requests
       SET verified_at = COALESCE(verified_at, ?),
           scan_status = CASE
             WHEN scan_status = 'pending_verification' THEN 'verified'
             ELSE scan_status
           END
       WHERE id = ?`
    )
    .bind(now, id)
    .run()
  return getScanRequest(db, id)
}

export interface UpdateScanRequestRunData {
  scan_started_at?: string
  scan_completed_at?: string
  scan_status?: ScanStatus
  thin_footprint_skipped?: boolean
  entity_id?: string | null
  email_sent_at?: string | null
  error_message?: string | null
  scan_status_reason?: string | null
  workflow_run_id?: string | null
}

/**
 * Update the run-state fields on a scan_request. Used by the diagnostic
 * orchestrator to record start/completion, thin-footprint refusal, and
 * email-sent timestamps.
 */
export async function updateScanRequestRun(
  db: D1Database,
  id: string,
  data: UpdateScanRequestRunData
): Promise<void> {
  const sets: string[] = []
  const params: (string | number | null)[] = []
  if (data.scan_started_at !== undefined) {
    sets.push('scan_started_at = ?')
    params.push(data.scan_started_at)
  }
  if (data.scan_completed_at !== undefined) {
    sets.push('scan_completed_at = ?')
    params.push(data.scan_completed_at)
  }
  if (data.scan_status !== undefined) {
    sets.push('scan_status = ?')
    params.push(data.scan_status)
  }
  if (data.thin_footprint_skipped !== undefined) {
    sets.push('thin_footprint_skipped = ?')
    params.push(data.thin_footprint_skipped ? 1 : 0)
  }
  if (data.entity_id !== undefined) {
    sets.push('entity_id = ?')
    params.push(data.entity_id)
  }
  if (data.email_sent_at !== undefined) {
    sets.push('email_sent_at = ?')
    params.push(data.email_sent_at)
  }
  if (data.error_message !== undefined) {
    sets.push('error_message = ?')
    params.push(data.error_message)
  }
  if (data.scan_status_reason !== undefined) {
    sets.push('scan_status_reason = ?')
    params.push(data.scan_status_reason)
  }
  if (data.workflow_run_id !== undefined) {
    sets.push('workflow_run_id = ?')
    params.push(data.workflow_run_id)
  }
  if (sets.length === 0) return
  params.push(id)
  await db
    .prepare(`UPDATE scan_requests SET ${sets.join(', ')} WHERE id = ?`)
    .bind(...params)
    .run()
}

// ---------------------------------------------------------------------------
// Rate-limit support queries
// ---------------------------------------------------------------------------

/**
 * Count scan_requests by IP within a lookback window. The IP-dimension of
 * the 4-dimensional rate limiter (`src/lib/diagnostic/rate-limit.ts`).
 *
 * Counts all rows including pre-verification — a flood of unverified
 * requests still costs us KV writes and email sends.
 */
export async function countScanRequestsByIp(
  db: D1Database,
  ip: string,
  sinceIso: string
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS n FROM scan_requests
       WHERE request_ip = ? AND created_at >= ?`
    )
    .bind(ip, sinceIso)
    .first<{ n: number }>()
  return row?.n ?? 0
}

/**
 * Count scan_requests by requester email domain within a lookback window.
 * The email-domain-dimension of the rate limiter.
 *
 * SQLite doesn't have a built-in last-occurrence search, so we rely on the
 * substr-after-@ pattern: `substr(email, instr(email, '@') + 1)`.
 */
export async function countScanRequestsByEmailDomain(
  db: D1Database,
  emailDomain: string,
  sinceIso: string
): Promise<number> {
  const lower = emailDomain.toLowerCase()
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS n FROM scan_requests
       WHERE LOWER(substr(email, instr(email, '@') + 1)) = ?
         AND created_at >= ?`
    )
    .bind(lower, sinceIso)
    .first<{ n: number }>()
  return row?.n ?? 0
}

/**
 * Count `completed` scan_requests for a domain within a lookback window.
 * The scanned-domain-dimension of the rate limiter — only completed scans
 * count, so a refused thin-footprint or pending row doesn't lock out a
 * legitimate retry from the same business.
 */
export async function countCompletedScansByDomain(
  db: D1Database,
  domain: string,
  sinceIso: string
): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS n FROM scan_requests
       WHERE domain = ? AND scan_status = 'completed' AND created_at >= ?`
    )
    .bind(domain, sinceIso)
    .first<{ n: number }>()
  return row?.n ?? 0
}

/**
 * Count all scan_requests in the last 24h regardless of source. The global
 * dimension of the rate limiter — safety net against viral abuse.
 */
export async function countScanRequestsSince(db: D1Database, sinceIso: string): Promise<number> {
  const row = await db
    .prepare(
      `SELECT COUNT(*) AS n FROM scan_requests
       WHERE created_at >= ?`
    )
    .bind(sinceIso)
    .first<{ n: number }>()
  return row?.n ?? 0
}

/**
 * Look up the most recent scan_request linked to a given entity. Used by
 * the Outside View portal page to recover thin-footprint / failed status
 * when no outside_views row exists yet (ADR 0002 Phase 1 PR-B).
 *
 * Tenancy: scan_requests don't carry org_id (the table is public-inbound),
 * so the org scope is enforced via the entity_id-to-org join through the
 * caller's session-resolved entity.
 */
export async function getScanRequestByEntity(
  db: D1Database,
  _orgId: string,
  entityId: string
): Promise<ScanRequest | null> {
  // _orgId reserved for future when scan_requests gains an org_id column;
  // signature is stable now to avoid churning callers.
  const row = await db
    .prepare(
      `SELECT * FROM scan_requests
       WHERE entity_id = ?
       ORDER BY created_at DESC
       LIMIT 1`
    )
    .bind(entityId)
    .first<ScanRequest>()
  return row ?? null
}
