/**
 * Telegram deep-link tokens for couples onboarding (ADR 0091 s5, plan R4).
 *
 * A partner taps `https://t.me/<bot>?start=<token>`. Telegram delivers
 * `/start <token>` to the seat's bot, and the seat asks the console to redeem
 * it (POST /api/internal/telegram-bind, authenticated by the seat's own
 * Machine credential). The console decides; the seat never trusts the token.
 *
 * Why an opaque token and not a signed one. A signed token would let the seat
 * verify it offline, but the binding has to land in D1 anyway, and single use
 * needs server state. With an opaque random token there are no claims to trust:
 * the only thing the token proves is possession, and the console resolves the
 * person from its own row. Nothing about the person, the instance or the expiry
 * is readable from the token.
 *
 * Properties, each pinned by tests/couples-link-token.test.ts:
 *   - 32 random bytes, base64url (43 characters): inside Telegram's start
 *     payload alphabet [A-Za-z0-9_-] and its 64-character limit.
 *   - stored as SHA-256 hex only; the token is shown once.
 *   - short-lived (TOKEN_TTL_MS) and single-use (consumed by a guarded UPDATE).
 *   - bound to one person, and redeemable only by the seat whose slug the
 *     person's instance carries.
 */

export const TOKEN_TTL_MS = 30 * 60 * 1000

/** Telegram's deep-link payload alphabet, at the exact length we mint. */
export const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/

function base64url(bytes: Uint8Array): string {
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function generateLinkToken(): string {
  const bytes = new Uint8Array(32)
  crypto.getRandomValues(bytes)
  return base64url(bytes)
}

export async function hashLinkToken(token: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** Telegram bot usernames: 5-32 chars, letters, digits, underscore, ending in "bot". */
export const BOT_USERNAME_RE = /^[A-Za-z][A-Za-z0-9_]{1,28}[Bb][Oo][Tt]$/

export function telegramDeepLink(botUsername: string, token: string): string {
  return `https://t.me/${botUsername}?start=${token}`
}

/** D1's datetime('now') shape (UTC, space-separated), for a given instant. */
export function sqliteUtc(ms: number): string {
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19)
}
