/**
 * Magic link authentication for client portal access.
 *
 * Magic links are single-use, time-limited tokens sent via email.
 * They provide passwordless authentication for client users.
 *
 * Token lifecycle:
 *   1. createMagicLink() — generates crypto-random token, stores in D1 with 15min expiry
 *   2. verifyMagicLink() — validates token, marks as used, returns email
 *
 * Security constraints (OQ-007):
 *   - Tokens expire after 15 minutes
 *   - Tokens are single-use (used_at set on verification)
 *   - Tokens are 64-character hex strings (32 bytes of entropy)
 */

export const MAGIC_LINK_EXPIRY_MS = 15 * 60 * 1000 // 15 minutes

export interface MagicLinkRow {
  id: string
  email: string
  token: string
  expires_at: string
  used_at: string | null
  created_at: string
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
 * Create a new magic link for the given email address.
 *
 * Stores the token in the magic_links table with a 15-minute expiry.
 * Returns the raw token (to be embedded in the magic link URL).
 */
export async function createMagicLink(db: D1Database, email: string): Promise<string> {
  const token = generateToken()
  const id = crypto.randomUUID()
  const expiresAt = new Date(Date.now() + MAGIC_LINK_EXPIRY_MS).toISOString()

  await db
    .prepare(
      `INSERT INTO magic_links (id, email, token, expires_at)
       VALUES (?, ?, ?, ?)`
    )
    .bind(id, email.toLowerCase().trim(), token, expiresAt)
    .run()

  return token
}

/**
 * Verify a magic link token.
 *
 * Checks that the token exists, has not expired, and has not been used.
 * On success, marks the token as used and returns the associated email.
 * On failure, returns null.
 */
export async function verifyMagicLink(db: D1Database, token: string): Promise<string | null> {
  // Look up the token
  const row = await db
    .prepare(`SELECT * FROM magic_links WHERE token = ? LIMIT 1`)
    .bind(token)
    .first<MagicLinkRow>()

  if (!row) {
    return null
  }

  // Check if already used
  if (row.used_at !== null) {
    return null
  }

  // Check if expired
  if (new Date(row.expires_at) <= new Date()) {
    return null
  }

  // Mark as used (single-use enforcement)
  await db
    .prepare(`UPDATE magic_links SET used_at = datetime('now') WHERE id = ?`)
    .bind(row.id)
    .run()

  return row.email
}
