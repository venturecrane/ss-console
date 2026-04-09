/**
 * Manage-token primitives for the booking system.
 *
 * Threat model:
 *   - Anyone with a manage URL can cancel/reschedule the booking it points at.
 *   - The token must therefore be unguessable (high entropy).
 *   - It must NOT be stored in plaintext at rest, in case the database is
 *     leaked or read by an unintended party (e.g. a future support engineer
 *     querying assessment_schedule directly).
 *
 * Approach:
 *   - 32 random bytes from `crypto.getRandomValues`
 *   - URL-safe base64 encoding (no '=' padding, '-'/'_' replacements)
 *   - SHA-256 hashed before being stored in `assessment_schedule.manage_token_hash`
 *   - Lookup is by hash: hash the incoming token, query by hash
 *
 * The raw token is returned to the guest exactly once (in the /reserve
 * response and the confirmation email body) and never written to the DB
 * or logged.
 */

const TOKEN_BYTES = 32

/**
 * Generate a fresh random manage token. Returns the URL-safe base64 string
 * to embed in the manage URL.
 */
export function generateManageToken(): string {
  const buf = new Uint8Array(TOKEN_BYTES)
  crypto.getRandomValues(buf)
  return base64UrlEncode(buf)
}

/**
 * Hash a manage token for storage / lookup. Always use this when comparing
 * a token from a request URL against the database.
 */
export async function hashManageToken(token: string): Promise<string> {
  const enc = new TextEncoder().encode(token)
  const digest = await crypto.subtle.digest('SHA-256', enc)
  return bytesToHex(new Uint8Array(digest))
}

/**
 * Compute the manage_token_expires_at value for a given slot end time.
 * Tokens stay valid for `ttlHours` after the slot's end so a guest can
 * cancel/reschedule for a window after the call. Default 48h.
 */
export function computeManageTokenExpiry(slotEndUtc: string, ttlHours: number): string {
  const slotEnd = new Date(slotEndUtc).getTime()
  return new Date(slotEnd + ttlHours * 60 * 60 * 1000).toISOString()
}

// ---------------------------------------------------------------------------
// Encoding helpers (kept private — exported for tests via the named exports)
// ---------------------------------------------------------------------------

function base64UrlEncode(bytes: Uint8Array): string {
  // Workers don't have Buffer; use btoa over a binary string
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  const b64 = btoa(bin)
  // URL-safe variant: replace +/ with -_, strip padding
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function bytesToHex(bytes: Uint8Array): string {
  let hex = ''
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, '0')
  }
  return hex
}
