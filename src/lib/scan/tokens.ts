/**
 * Magic-link token utilities for the diagnostic scan flow (#598).
 *
 * The token is the proof-of-intent gate for the pruned enrichment pipeline.
 * Without verification, an attacker could submit 100 forms with throwaway
 * emails and burn $14 of Anthropic spend in seconds; with verification,
 * each submission costs zero until a real inbox clicks the link.
 *
 * Storage contract (mirrors the existing booking-token pattern at
 * `src/lib/booking/tokens.ts`):
 *   - The raw token is generated server-side, returned to the caller for
 *     embedding in the magic-link URL, and *immediately discarded*.
 *   - The DB stores only the SHA-256 hash. A leaked DB dump cannot be
 *     replayed to reach a verify endpoint.
 *   - The verify endpoint hashes the inbound token and looks up the row.
 *
 * Expiry: 24 hours from `created_at`. Enforced server-side at verify time
 * (not via DB index TTL) so we can return a clear "link expired" UX
 * instead of silently 404ing.
 */

const TOKEN_BYTES = 32
const TOKEN_TTL_MS = 24 * 60 * 60 * 1000

/**
 * Generate a fresh magic-link token. Returns the raw token (for embedding
 * in the email URL) and its SHA-256 hash (for storing in the DB).
 *
 * The raw token uses base64url for URL-safety. 32 bytes ~ 256 bits of
 * entropy = no realistic guess attack.
 */
export async function generateScanToken(): Promise<{
  token: string
  hash: string
}> {
  const bytes = new Uint8Array(TOKEN_BYTES)
  crypto.getRandomValues(bytes)
  const token = base64urlEncode(bytes)
  const hash = await hashScanToken(token)
  return { token, hash }
}

/**
 * Compute the SHA-256 hash of a scan token. Used both at insert time
 * (storing the hash) and at verify time (looking up the row by hash).
 */
export async function hashScanToken(token: string): Promise<string> {
  const data = new TextEncoder().encode(token)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return base64urlEncode(new Uint8Array(digest))
}

/**
 * Check whether a token is still within its TTL window relative to a
 * `created_at` ISO8601 timestamp. Returns false on parse failure.
 */
export function isScanTokenFresh(createdAtIso: string, nowMs: number = Date.now()): boolean {
  const created = Date.parse(createdAtIso)
  if (Number.isNaN(created)) return false
  return nowMs - created < TOKEN_TTL_MS
}

/**
 * Build the verification URL the prospect clicks in their inbox. The
 * `baseUrl` is `APP_BASE_URL` so the link points at the marketing host
 * (smd.services), not a subdomain.
 */
export function buildScanVerifyUrl(baseUrl: string, token: string): string {
  const url = new URL(`/scan/verify/${encodeURIComponent(token)}`, baseUrl)
  return url.toString()
}

// Standard base64url encoding (no padding).
function base64urlEncode(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
