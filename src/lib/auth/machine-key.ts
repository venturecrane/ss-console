/**
 * Auth verifier for per-customer Operator Machine -> control-plane writes
 * (`POST /api/internal/heartbeat`, `/runtime-summary`, `/sentry-probe`).
 *
 * Per-tenant credentials (2026-09-10, the upgrade ADR 0023 §"Cross-cutting
 * calls" #10 gated on customer #2). Each seat holds its own plaintext key in
 * its MACHINE_HEARTBEAT_KEY Fly secret; the console holds only
 * `machine_credentials.key_hash` = HMAC-SHA256(salt, plaintext) with a
 * per-row salt, plus the previous credential until `prev_expires_at` so a
 * rotation never 401s a seat mid-redeploy. A seat's key verifies ONLY for
 * the slug it was minted for, which is the property Wave 1 lacked: with one
 * shared key, `X-Tenant-Slug` was an unbound header and any seat could write
 * another tenant's status rows.
 *
 * Timing uniformity: the verifier always performs exactly one D1 round trip
 * and at least one HMAC. A slug with no customer row is verified against a
 * sentinel salt and can never match, so response timing does not tell a
 * probing caller whether the slug exists. Every failure returns the same
 * `{ ok: false, status: 401 }`.
 *
 * Shared-key fallback (transitional): a seat whose slug has a customer row
 * but no credential row is accepted against the Worker's shared
 * MACHINE_HEARTBEAT_KEY, exactly as Wave 1 did, and the fallback is logged
 * so it is visible in Worker logs. Unsetting that Worker secret retires the
 * fallback; until then a seat still on the shared key can still forge a
 * slug that has no credential row. Minting: operator/bin/lib/machine_credential.py
 * (run by provision-customer.sh and rotate-machine-credential.sh).
 */

import type { D1Database } from '@cloudflare/workers-types'
import { constantTimeEqual } from './constant-time'

export type VerifyResult = { ok: true; entityId: string; slug: string } | { ok: false; status: 401 }

const FAIL: VerifyResult = { ok: false, status: 401 }

/**
 * A slug with no row still costs one HMAC against this salt, so a miss and a
 * wrong key take the same time. The hash it is compared to is never a real
 * credential's hash, so it can never match.
 */
const SENTINEL_SALT_HEX = '00000000000000000000000000000000'
const SENTINEL_HASH = '0'.repeat(64)

interface CredentialRow {
  entity_id: string
  key_hash: string | null
  salt: string | null
  prev_key_hash: string | null
  prev_salt: string | null
  prev_expires_at: string | null
}

/**
 * Verify an inbound Machine request carries a valid bearer for its slug.
 * Returns the resolved entity_id on success, 401 otherwise.
 *
 * `sharedKey` is the Worker's transitional MACHINE_HEARTBEAT_KEY. Pass
 * `undefined` once every seat has a credential row; from then on a slug
 * without a row fails closed.
 */
export async function verifyMachineRequest(
  request: Request,
  sharedKey: string | undefined,
  db: D1Database
): Promise<VerifyResult> {
  const auth = request.headers.get('Authorization') ?? ''
  const slug = request.headers.get('X-Tenant-Slug') ?? ''
  if (!auth.startsWith('Bearer ')) return FAIL
  const provided = auth.slice('Bearer '.length)
  if (provided.length === 0 || slug.length === 0) return FAIL

  const row = await db
    .prepare(
      'SELECT c.entity_id, m.key_hash, m.salt, m.prev_key_hash, m.prev_salt, m.prev_expires_at ' +
        'FROM customer_configs c LEFT JOIN machine_credentials m ON m.customer_slug = c.customer_slug ' +
        'WHERE c.customer_slug = ?'
    )
    .bind(slug)
    .first<CredentialRow>()

  if (!row) {
    // Unknown slug: burn one HMAC so the miss is not faster than a mismatch.
    await hmacMatches(provided, SENTINEL_SALT_HEX, SENTINEL_HASH)
    return FAIL
  }

  if (typeof row.key_hash === 'string' && typeof row.salt === 'string') {
    if (await hmacMatches(provided, row.salt, row.key_hash)) {
      return { ok: true, entityId: row.entity_id, slug }
    }
    if (
      previousStillValid(row) &&
      (await hmacMatches(provided, row.prev_salt, row.prev_key_hash))
    ) {
      return { ok: true, entityId: row.entity_id, slug }
    }
    return FAIL
  }

  // No credential row yet: transitional shared-key path (Wave 1 shape).
  await hmacMatches(provided, SENTINEL_SALT_HEX, SENTINEL_HASH)
  if (!sharedKey) return FAIL
  if (!constantTimeEqual(provided, sharedKey)) return FAIL
  console.warn(
    `[machine-key] shared-key fallback used; no machine_credentials row for slug=${slug}`
  )
  return { ok: true, entityId: row.entity_id, slug }
}

function previousStillValid(
  row: CredentialRow
): row is CredentialRow & { prev_key_hash: string; prev_salt: string; prev_expires_at: string } {
  if (
    typeof row.prev_key_hash !== 'string' ||
    typeof row.prev_salt !== 'string' ||
    typeof row.prev_expires_at !== 'string'
  ) {
    return false
  }
  const expires = Date.parse(row.prev_expires_at.replace(' ', 'T') + 'Z')
  return Number.isFinite(expires) && expires > Date.now()
}

/**
 * HMAC-SHA256(key = salt bytes, message = plaintext) as lowercase hex, compared
 * in constant time against the stored hash. Must agree byte-for-byte with
 * operator/bin/lib/machine_credential.py, which writes the hashes; the
 * cross-language round trip is pinned by tests/machine-credentials.test.ts.
 */
async function hmacMatches(
  plaintext: string,
  saltHex: string,
  expectedHashHex: string
): Promise<boolean> {
  const digest = await hmacHex(plaintext, saltHex)
  return constantTimeEqual(digest, expectedHashHex)
}

async function hmacHex(plaintext: string, saltHex: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    hexToBytes(saltHex),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(plaintext))
  return Array.from(new Uint8Array(signature), (b) => b.toString(16).padStart(2, '0')).join('')
}

function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  const clean = hex.length % 2 === 0 ? hex : `0${hex}`
  const out = new Uint8Array(new ArrayBuffer(clean.length / 2))
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16)
  }
  return out
}
