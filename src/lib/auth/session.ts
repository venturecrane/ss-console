/**
 * Session management for admin authentication.
 *
 * Sessions are stored in D1 (source of truth) with Workers KV as a fast
 * lookup cache. The session token is a cryptographically random UUID stored
 * in an HttpOnly cookie.
 *
 * Session lifecycle:
 *   1. createSession() — writes to D1 + KV, returns token
 *   2. validateSession() — reads from KV (fast path) or D1 (fallback)
 *   3. destroySession() — deletes from D1 + KV
 *
 * Session expiration: 7 days of inactivity (sliding window).
 */

export const SESSION_COOKIE_NAME = 'session_token'
export const ADMIN_SESSION_DURATION_MS = 7 * 24 * 60 * 60 * 1000 // 7 days
export const CLIENT_SESSION_DURATION_MS = 30 * 24 * 60 * 60 * 1000 // 30 days

/** @deprecated Use getSessionDurationMs(role) instead. Kept for backward compat. */
export const SESSION_DURATION_MS = ADMIN_SESSION_DURATION_MS

/**
 * Return session duration based on role.
 *
 * Clients and Outside View prospects (ADR 0002) get 30 days — infrequent
 * portal visitors who shouldn't be re-authed every visit. Admins get 7
 * days. The 24h figure on the prospect path is the magic-link TTL, NOT
 * the session lifetime: once a prospect verifies, their session lasts
 * the same as a client's.
 */
export function getSessionDurationMs(role?: string): number {
  return role === 'client' || role === 'prospect'
    ? CLIENT_SESSION_DURATION_MS
    : ADMIN_SESSION_DURATION_MS
}

export interface SessionData {
  userId: string
  orgId: string
  role: string
  email: string
  expiresAt: string
}

export interface SessionRow {
  id: string
  token: string
  user_id: string
  org_id: string
  role: string
  email: string
  expires_at: string
  created_at: string
}

/**
 * Create a new session for the given user.
 * Writes to both D1 (source of truth) and KV (cache).
 */
export async function createSession(
  db: D1Database,
  kv: KVNamespace,
  user: { id: string; orgId: string; role: string; email: string }
): Promise<string> {
  const token = crypto.randomUUID()
  const sessionId = crypto.randomUUID()
  const durationMs = getSessionDurationMs(user.role)
  const expiresAt = new Date(Date.now() + durationMs).toISOString()

  // Write to D1 (source of truth)
  await db
    .prepare(
      `INSERT INTO sessions (id, token, user_id, org_id, role, email, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(sessionId, token, user.id, user.orgId, user.role, user.email, expiresAt)
    .run()

  // Write to KV (cache) with TTL matching session expiration
  const sessionData: SessionData = {
    userId: user.id,
    orgId: user.orgId,
    role: user.role,
    email: user.email,
    expiresAt,
  }

  const kvTtlSeconds = Math.floor(durationMs / 1000)
  await kv.put(`session:${token}`, JSON.stringify(sessionData), {
    expirationTtl: kvTtlSeconds,
  })

  return token
}

/**
 * Validate a session token. Returns session data if valid, null otherwise.
 *
 * Fast path: check KV cache first.
 * Fallback: check D1 if KV miss (repopulates KV on success).
 */
export async function validateSession(
  db: D1Database,
  kv: KVNamespace,
  token: string
): Promise<SessionData | null> {
  // Fast path: KV lookup
  const cached = await kv.get(`session:${token}`)
  if (cached) {
    const data: SessionData = JSON.parse(cached)
    if (new Date(data.expiresAt) > new Date()) {
      return data
    }
    // Expired in cache — clean up
    await kv.delete(`session:${token}`)
    return null
  }

  // Fallback: D1 lookup
  const row = await db
    .prepare(`SELECT * FROM sessions WHERE token = ? LIMIT 1`)
    .bind(token)
    .first<SessionRow>()

  if (!row) {
    return null
  }

  if (new Date(row.expires_at) <= new Date()) {
    // Expired — clean up
    await db.prepare(`DELETE FROM sessions WHERE id = ?`).bind(row.id).run()
    return null
  }

  // Repopulate KV cache
  const sessionData: SessionData = {
    userId: row.user_id,
    orgId: row.org_id,
    role: row.role,
    email: row.email,
    expiresAt: row.expires_at,
  }

  const remainingMs = new Date(row.expires_at).getTime() - Date.now()
  const kvTtlSeconds = Math.max(60, Math.floor(remainingMs / 1000))
  await kv.put(`session:${token}`, JSON.stringify(sessionData), {
    expirationTtl: kvTtlSeconds,
  })

  return sessionData
}

/**
 * Renew a session's expiration (sliding window).
 * Call this on each authenticated request to extend the session.
 */
export async function renewSession(
  db: D1Database,
  kv: KVNamespace,
  token: string,
  currentData: SessionData
): Promise<void> {
  // Role comes from KV-cached session data and may be stale if changed
  // mid-session by an admin. Self-heals on next KV expiry + D1 fallback.
  const durationMs = getSessionDurationMs(currentData.role)
  const newExpiresAt = new Date(Date.now() + durationMs).toISOString()

  // Update D1
  await db
    .prepare(`UPDATE sessions SET expires_at = ? WHERE token = ?`)
    .bind(newExpiresAt, token)
    .run()

  // Update KV cache
  const updatedData: SessionData = {
    ...currentData,
    expiresAt: newExpiresAt,
  }

  const kvTtlSeconds = Math.floor(durationMs / 1000)
  await kv.put(`session:${token}`, JSON.stringify(updatedData), {
    expirationTtl: kvTtlSeconds,
  })
}

/**
 * Destroy a session (logout).
 * Removes from both D1 and KV.
 */
export async function destroySession(
  db: D1Database,
  kv: KVNamespace,
  token: string
): Promise<void> {
  await Promise.all([
    db.prepare(`DELETE FROM sessions WHERE token = ?`).bind(token).run(),
    kv.delete(`session:${token}`),
  ])
}

/**
 * Build a Set-Cookie header for the session token.
 *
 * No Domain= attribute is set intentionally: admin cookies are scoped to
 * admin.smd.services and portal cookies to portal.smd.services. This
 * isolation prevents cross-domain cookie leakage between admin and client
 * sessions.
 */
export function buildSessionCookie(token: string, role?: string): string {
  const maxAge = Math.floor(getSessionDurationMs(role) / 1000)
  return `${SESSION_COOKIE_NAME}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`
}

/**
 * Build a Set-Cookie header that clears the session cookie.
 */
export function buildClearSessionCookie(): string {
  return `${SESSION_COOKIE_NAME}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`
}

/**
 * Parse the session token from a Cookie header string.
 */
export function parseSessionToken(cookieHeader: string | null): string | null {
  if (!cookieHeader) return null

  const cookies = cookieHeader.split(';')
  for (const cookie of cookies) {
    const [name, ...rest] = cookie.trim().split('=')
    if (name === SESSION_COOKIE_NAME) {
      const value = rest.join('=')
      return value || null
    }
  }

  return null
}
