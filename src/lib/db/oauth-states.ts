/**
 * OAuth state nonce data access layer.
 *
 * Single-use state parameters for the OAuth authorization code flow.
 * Stored in D1 (not KV) to guarantee consume-once semantics atomically.
 *
 * All queries are parameterized to prevent SQL injection.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface OAuthState {
  state: string
  org_id: string
  provider: string
  initiated_by: string
  expires_at: string
  consumed_at: string | null
  created_at: string
}

export interface CreateOAuthStateInput {
  state: string
  orgId: string
  provider: string
  initiatedBy: string
}

export interface ConsumedOAuthState {
  orgId: string
  provider: string
  initiatedBy: string
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

const STATE_TTL_MINUTES = 5

/**
 * Insert a new OAuth state nonce. Expires after 5 minutes.
 */
export async function createOAuthState(
  db: D1Database,
  input: CreateOAuthStateInput
): Promise<void> {
  const expiresAt = new Date(Date.now() + STATE_TTL_MINUTES * 60_000).toISOString()

  await db
    .prepare(
      `INSERT INTO oauth_states (state, org_id, provider, initiated_by, expires_at)
       VALUES (?, ?, ?, ?, ?)`
    )
    .bind(input.state, input.orgId, input.provider, input.initiatedBy, expiresAt)
    .run()
}

// ---------------------------------------------------------------------------
// Consume (atomic)
// ---------------------------------------------------------------------------

/**
 * Atomically consume an OAuth state nonce. Returns the associated data if the
 * state was valid and unconsumed, or null if it was already consumed, expired,
 * or doesn't exist.
 *
 * Uses db.batch() with an UPDATE-WHERE guard so two racing callbacks cannot
 * both consume the same nonce. The SELECT in the batch reads the row AFTER
 * the UPDATE, so if the UPDATE matched zero rows (race loser), the SELECT
 * returns consumed_at already set and we return null.
 */
export async function consumeOAuthState(
  db: D1Database,
  state: string
): Promise<ConsumedOAuthState | null> {
  const now = new Date().toISOString()

  // Batch: UPDATE then SELECT. The UPDATE's WHERE clause ensures only one
  // caller can successfully set consumed_at on an unconsumed, non-expired row.
  const [updateResult, selectResult] = await db.batch<OAuthState>([
    db
      .prepare(
        `UPDATE oauth_states
         SET consumed_at = ?
         WHERE state = ?
           AND consumed_at IS NULL
           AND expires_at > ?`
      )
      .bind(now, state, now),
    db.prepare('SELECT * FROM oauth_states WHERE state = ?').bind(state),
  ])

  // If the UPDATE changed zero rows, either the state doesn't exist,
  // was already consumed, or has expired.
  const changes = updateResult.meta?.changes ?? 0
  if (changes === 0) return null

  const row = selectResult.results?.[0]
  if (!row) return null

  return {
    orgId: row.org_id,
    provider: row.provider,
    initiatedBy: row.initiated_by,
  }
}
