/**
 * OAuth state nonce data access layer.
 *
 * Single-use state tokens for OAuth flows. Stored in D1 (not KV) so
 * consume-once semantics are enforced atomically. Each state expires
 * after 5 minutes. Consumed states are soft-deleted (consumed_at set)
 * rather than hard-deleted for auditability.
 */

export interface OAuthState {
  state: string
  org_id: string
  provider: string
  initiated_by: string
  expires_at: string
  consumed_at: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/**
 * Generate and insert a new OAuth state nonce. Returns the state string
 * to embed in the OAuth redirect URL.
 */
export async function createOAuthState(
  db: D1Database,
  orgId: string,
  provider: string,
  initiatedBy: string
): Promise<string> {
  const state = crypto.randomUUID()
  const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString()

  await db
    .prepare(
      `INSERT INTO oauth_states (state, org_id, provider, initiated_by, expires_at, created_at)
       VALUES (?, ?, ?, ?, ?, datetime('now'))`
    )
    .bind(state, orgId, provider, initiatedBy, expiresAt)
    .run()

  return state
}

// ---------------------------------------------------------------------------
// Consume (atomic single-use)
// ---------------------------------------------------------------------------

/**
 * Atomically consume an OAuth state nonce. Returns the state row if it
 * was valid and unconsumed, or null if it was already consumed, expired,
 * or does not exist.
 *
 * Uses UPDATE ... WHERE consumed_at IS NULL AND expires_at > now() to
 * guarantee exactly-once consumption under concurrent requests.
 */
export async function consumeOAuthState(db: D1Database, state: string): Promise<OAuthState | null> {
  const now = new Date().toISOString()

  const result = await db
    .prepare(
      `UPDATE oauth_states
       SET consumed_at = ?
       WHERE state = ? AND consumed_at IS NULL AND expires_at > ?`
    )
    .bind(now, state, now)
    .run()

  if (!result.meta.changed_db || (result.meta.changes ?? 0) === 0) {
    return null
  }

  return (
    (await db
      .prepare('SELECT * FROM oauth_states WHERE state = ?')
      .bind(state)
      .first<OAuthState>()) ?? null
  )
}

// ---------------------------------------------------------------------------
// Expiry. No scheduled cleanup exists (a comment here named a cleanup worker
// that was never built). An expired or consumed state row is refused by
// consumeOAuthState's WHERE clause and otherwise stays in the table.
// ---------------------------------------------------------------------------
