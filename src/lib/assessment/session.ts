/**
 * Signed assessment session + server-side per-session ceiling.
 *
 * The public live-assessment endpoint (`/api/assessment/turn`, ADR 0039 node
 * [1]) makes a live LLM call on every turn. Before it takes advertising
 * traffic it needs an abuse control that an IP-only rate limit cannot give:
 *
 *   Threat — IP-rotation budget exhaustion. The IP rate limit (200/hr) caps a
 *   single address, and `MAX_TURNS` caps the turns array of one request, but
 *   neither bounds how many *sessions* a determined caller can start. Rotating
 *   IPs (botnet, proxy pool, CGNAT churn) lets an attacker open unbounded fresh
 *   assessments and burn the Anthropic budget before a real prospect ever
 *   reaches the funnel.
 *
 * Design — a signed session that the server tracks:
 *
 *   1. The opening turn (empty `turns`) mints a session token: an HMAC-SHA256
 *      signed payload carrying a random `sid` and an `exp`. The token is opaque
 *      to the client and returned alongside the opening message.
 *   2. Every subsequent turn must present the token. We verify the signature
 *      and expiry (fail closed on missing / malformed / bad-signature /
 *      expired) and then increment a per-`sid` counter in KV. When the counter
 *      reaches the ceiling the turn is refused.
 *
 *   Because each accepted turn is exactly one LLM call, a per-session turn
 *   ceiling *is* a per-session cost ceiling — the unit we actually care about.
 *   The signature stops a caller from forging or mutating a `sid` to dodge the
 *   counter; rotating IPs no longer helps because the ceiling is bound to the
 *   signed session, not the address.
 *
 *   IP rate-limiting stays as a cheap first layer (it sheds volume before we
 *   touch KV or crypto); the signed-session ceiling is the backstop that
 *   survives IP rotation.
 *
 * Encoding (the shared codec in src/lib/security/signed-payload.ts, as OAuth
 * state and signed booking links use):
 *
 *   `<base64url(json-payload)>.<base64url(hmac-sha256)>`
 *
 * Signing key: `ASSESSMENT_SESSION_SIGNING_KEY` env var, base64-encoded raw
 * bytes (generate with `openssl rand -base64 32`). A dedicated key keeps
 * assessment sessions cryptographically independent of booking links and OAuth
 * state — rotating one must never invalidate the others. Rotation simply
 * invalidates in-flight sessions; the visitor restarts, which is cheap.
 *
 * Counter storage: `BOOKING_CACHE` KV (already bound; the same namespace the
 * booking rate-limiter uses). Keys are `as:turns:<sid>` with a TTL matching the
 * session lifetime so they self-expire. KV is eventually consistent, so the
 * ceiling is approximate at the margin — acceptable, because the goal is to
 * bound spend, not to enforce an exact transaction count.
 */

import { env } from 'cloudflare:workers'
import { isRecord } from '../api/helpers'
import { importSigningKey, signPayload, verifySignedPayload } from '../security/signed-payload'

const SCHEMA_VERSION = 1

/**
 * Maximum operator turns served per session. Each turn is one LLM call, so this
 * is the per-session cost ceiling. A genuine assessment completes well under
 * this; the interviewer emits the completion sentinel long before. Sized to
 * leave generous headroom for a thorough conversation while still bounding a
 * single session's spend.
 */
export const MAX_SESSION_TURNS = 40

/**
 * Session lifetime. Long enough for an unhurried assessment (a visitor may
 * pause mid-conversation), short enough that a leaked token is not useful for
 * long. The KV counter TTL is pinned to this so stale sessions self-evict.
 */
export const SESSION_TTL_SECONDS = 2 * 60 * 60 // 2 hours

export interface AssessmentSessionPayload {
  v: number
  /** Random session id; the KV counter key is derived from this. */
  sid: string
  /** Unix seconds; token invalid at/after this time. */
  exp: number
}

export type IssueSessionResult = {
  /** Opaque token the client must echo on every subsequent turn. */
  token: string
  payload: AssessmentSessionPayload
}

export type VerifySessionResult =
  | { ok: true; payload: AssessmentSessionPayload }
  | { ok: false; error: 'malformed' | 'bad_signature' | 'expired' | 'unknown_version' }

export type ConsumeTurnResult =
  | { ok: true; count: number; limit: number }
  | { ok: false; reason: 'ceiling_reached'; count: number; limit: number }

/**
 * Mint a fresh signed session token. Called when the opening message is served
 * (empty `turns`). The `sid` is a random UUID — unguessable and unique per
 * session so counters never collide.
 */
export async function issueAssessmentSession(
  ttlSeconds: number = SESSION_TTL_SECONDS
): Promise<IssueSessionResult> {
  const key = await sessionSigningKey()
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds
  const payload: AssessmentSessionPayload = {
    v: SCHEMA_VERSION,
    sid: crypto.randomUUID(),
    exp,
  }
  return { token: await signPayload(key, payload), payload }
}

/**
 * Verify a session token. Returns the payload only when the signature is valid,
 * the schema version is known, and the token has not expired. Fails closed on
 * every other input.
 */
export async function verifyAssessmentSession(token: unknown): Promise<VerifySessionResult> {
  const verified = await verifySignedPayload(token, sessionSigningKey)
  if (!verified.ok) return verified
  const p = verified.payload
  if (!isRecord(p)) return { ok: false, error: 'malformed' }

  if (p.v !== SCHEMA_VERSION) {
    return { ok: false, error: 'unknown_version' }
  }
  if (typeof p.sid !== 'string' || p.sid.length === 0) {
    return { ok: false, error: 'malformed' }
  }

  const now = Math.floor(Date.now() / 1000)
  if (typeof p.exp !== 'number' || p.exp < now) {
    return { ok: false, error: 'expired' }
  }

  return { ok: true, payload: { v: SCHEMA_VERSION, sid: p.sid, exp: p.exp } }
}

/**
 * Atomically(-ish) charge one turn against a session's ceiling. Reads the
 * current per-`sid` counter from KV, refuses if it has already reached the
 * limit, otherwise increments and persists with a TTL.
 *
 * KV has no compare-and-swap, so two near-simultaneous turns from the same
 * session could both read the same count and both write `count + 1` — at worst
 * one extra LLM call slips through at the boundary. That is acceptable: the
 * ceiling bounds spend, it is not a financial ledger. The signed `sid` is what
 * makes the counter unforgeable, which is the property that defeats IP
 * rotation.
 *
 * If `kv` is undefined (dev without the binding) the turn is allowed — mirrors
 * the booking rate-limiter's dev-mode behavior. Production always has the
 * binding; the endpoint additionally requires a valid signed token, so an
 * undefined KV never silently disables the *signature* check.
 */
export async function consumeSessionTurn(
  kv: KVNamespace | undefined,
  sid: string,
  limit: number = MAX_SESSION_TURNS,
  ttlSeconds: number = SESSION_TTL_SECONDS
): Promise<ConsumeTurnResult> {
  if (!kv) {
    return { ok: true, count: 0, limit }
  }

  const key = `as:turns:${sid}`
  const currentRaw = await kv.get(key)
  const current = currentRaw ? parseInt(currentRaw, 10) : 0
  const safeCurrent = Number.isFinite(current) && current >= 0 ? current : 0

  if (safeCurrent >= limit) {
    return { ok: false, reason: 'ceiling_reached', count: safeCurrent, limit }
  }

  const next = safeCurrent + 1
  await kv.put(key, String(next), { expirationTtl: ttlSeconds })
  return { ok: true, count: next, limit }
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function sessionSigningKey(): Promise<CryptoKey> {
  return importSigningKey(
    env.ASSESSMENT_SESSION_SIGNING_KEY,
    'ASSESSMENT_SESSION_SIGNING_KEY',
    'issuing assessment sessions'
  )
}
