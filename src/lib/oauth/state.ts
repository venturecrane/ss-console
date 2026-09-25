/**
 * OAuth state parameter — signed, stateless, single-use-by-expiry.
 *
 * The state parameter on an OAuth authorize URL must round-trip back to the
 * callback unchanged. We use it for two things:
 *
 *   1. CSRF binding — the value is HMAC-signed with a server-side secret,
 *      so the callback can refuse a state it did not issue.
 *   2. Context carry — we pack `(customer_id, provider, reviewer_id, nonce,
 *      expiry)` into the state itself so the callback knows which customer
 *      and provider this consent was issued for, and which reviewer
 *      initiated the flow.
 *
 * Why not a D1 row keyed by an opaque state? D1 single-use states (the
 * existing `src/lib/db/oauth-states.ts` pattern) serialize on every flow
 * across all customers. For multi-tenant Operator provisioning we expect
 * to scale this beyond what a single shared table comfortably owns, and we
 * already need a server-side secret for downstream token handling. A signed
 * stateless token is the natural shape.
 *
 * Replay protection: the callback caller is responsible for binding the
 * decoded `reviewer_id` to the currently authenticated reviewer (Clerk or
 * magic-link session). A leaked state with a valid signature but for the
 * wrong reviewer must be rejected. Expiry is 10 minutes from issue.
 *
 * Encoding format (the shared codec in src/lib/security/signed-payload.ts):
 *
 *   `<base64url(json-payload)>.<base64url(hmac-sha256)>`
 *
 * Signing key: `OAUTH_STATE_SIGNING_KEY` env var, base64-encoded raw bytes.
 * Generate with `openssl rand -base64 32`. Rotation: bump the key in
 * Cloudflare Workers secrets; any in-flight authorize redirects issued
 * under the old key will fail validation at callback time and the
 * reviewer can simply re-initiate consent. No grace window — short TTL
 * makes rotation safe.
 */

import { env } from 'cloudflare:workers'
import { isRecord } from '../api/helpers'
import { importSigningKey, signPayload, verifySignedPayload } from '../security/signed-payload'

const SCHEMA_VERSION = 1

export const DEFAULT_STATE_TTL_SECONDS = 10 * 60

export interface OAuthStatePayload {
  v: number
  customer_id: string
  provider: string
  reviewer_id: string
  nonce: string
  exp: number
}

export interface IssueOAuthStateInput {
  customer_id: string
  provider: string
  reviewer_id: string
  ttl_seconds?: number
}

export type VerifyOAuthStateResult =
  | { ok: true; payload: OAuthStatePayload }
  | { ok: false; error: 'malformed' | 'bad_signature' | 'expired' | 'unknown_version' }

function stateSigningKey(): Promise<CryptoKey> {
  return importSigningKey(
    env.OAUTH_STATE_SIGNING_KEY,
    'OAUTH_STATE_SIGNING_KEY',
    'issuing OAuth states'
  )
}

export async function issueOAuthState(input: IssueOAuthStateInput): Promise<string> {
  const key = await stateSigningKey()
  const ttl = input.ttl_seconds ?? DEFAULT_STATE_TTL_SECONDS
  const exp = Math.floor(Date.now() / 1000) + ttl

  const payload: OAuthStatePayload = {
    v: SCHEMA_VERSION,
    customer_id: input.customer_id,
    provider: input.provider,
    reviewer_id: input.reviewer_id,
    nonce: crypto.randomUUID(),
    exp,
  }
  return signPayload(key, payload)
}

const nonEmpty = (value: unknown): value is string => typeof value === 'string' && value.length > 0

export async function verifyOAuthState(state: string): Promise<VerifyOAuthStateResult> {
  const verified = await verifySignedPayload(state, stateSigningKey)
  if (!verified.ok) return verified
  const p = verified.payload
  if (!isRecord(p)) return { ok: false, error: 'malformed' }
  if (p.v !== SCHEMA_VERSION) return { ok: false, error: 'unknown_version' }

  const now = Math.floor(Date.now() / 1000)
  if (typeof p.exp !== 'number' || p.exp < now) {
    return { ok: false, error: 'expired' }
  }

  const { customer_id, provider, reviewer_id, nonce } = p
  if (!nonEmpty(customer_id) || !nonEmpty(provider) || !nonEmpty(reviewer_id) || !nonEmpty(nonce))
    return { ok: false, error: 'malformed' }

  return {
    ok: true,
    payload: { v: SCHEMA_VERSION, customer_id, provider, reviewer_id, nonce, exp: p.exp },
  }
}
