/**
 * Magic link authentication for portal access (admin login, client login).
 *
 * Magic links are single-use, time-limited tokens sent via email.
 *
 * Token lifecycle:
 *   1. createMagicLink() - generates a crypto-random token and stores it in D1
 *   2. verifyMagicLink() - atomically consumes the token and returns the bound user
 *
 * Security constraints:
 *   - Tokens are single-use under concurrent requests
 *   - Tokens are bound to a specific org_id + user_id, not just an email
 *   - Tokens are 64-character hex strings (32 bytes of entropy)
 *   - TTL is REQUIRED at every call site. Making TTL required prevents
 *     quietly extending login-link lifetime by adding an option default
 *     in a future change.
 */

/** Default TTL for admin and client login magic links (15 minutes). */
export const MAGIC_LINK_EXPIRY_MS = 15 * 60 * 1000

export interface MagicLinkRow {
  id: string
  org_id: string
  user_id: string
  email: string
  token: string
  expires_at: string
  used_at: string | null
  created_at: string
}

export interface MagicLinkSubject {
  orgId: string
  userId: string
  email: string
}

export interface ConsumedMagicLink {
  id: string
  orgId: string
  userId: string
  email: string
}

/**
 * Generate a cryptographically random token as a hex string.
 * Uses 32 bytes (256 bits) of entropy — more than sufficient for magic links.
 */
function generateToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32))
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Create a new magic link for the given user identity.
 *
 * `ttlMs` is REQUIRED — every call site must specify the lifetime explicitly.
 * Use `MAGIC_LINK_EXPIRY_MS` (15 min) for admin and client login flows.
 */
export async function createMagicLink(
  db: D1Database,
  subject: MagicLinkSubject,
  ttlMs: number
): Promise<string> {
  const token = generateToken()
  const id = crypto.randomUUID()
  const expiresAt = new Date(Date.now() + ttlMs).toISOString()

  await db
    .prepare(
      `INSERT INTO magic_links (id, org_id, user_id, email, token, expires_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind(id, subject.orgId, subject.userId, subject.email.toLowerCase().trim(), token, expiresAt)
    .run()

  return token
}

/**
 * Atomically consume a magic link token.
 *
 * Returns the bound user identity if the token existed, was unconsumed, and
 * had not expired. Otherwise returns null.
 */
export async function verifyMagicLink(
  db: D1Database,
  token: string
): Promise<ConsumedMagicLink | null> {
  const now = new Date().toISOString()

  const result = await db
    .prepare(
      `UPDATE magic_links
       SET used_at = ?
       WHERE token = ? AND used_at IS NULL AND expires_at > ?`
    )
    .bind(now, token, now)
    .run()

  if (!result.meta.changed_db || (result.meta.changes ?? 0) === 0) {
    return null
  }

  const row = await db
    .prepare(`SELECT * FROM magic_links WHERE token = ? LIMIT 1`)
    .bind(token)
    .first<MagicLinkRow>()

  if (!row) {
    return null
  }

  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    email: row.email,
  }
}
