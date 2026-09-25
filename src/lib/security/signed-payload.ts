/**
 * The signed-token codec: `<base64url(json-payload)>.<base64url(hmac-sha256)>`.
 *
 * One home for the encoding, the key import, and sign/verify. Until
 * 2026-09-25 each of OAuth state (`src/lib/oauth/state.ts`), signed booking
 * links (`src/lib/booking/signed-link.ts`) and assessment sessions
 * (`src/lib/assessment/session.ts`) carried a private copy of all three, and
 * the booking manage token (`src/lib/booking/tokens.ts`) a fourth encoder
 * (review 2026-09-25, Code Quality 3). The copies agreed; the hazard was that
 * the next fix (a key-length check, a `kid`) would land in one of them.
 *
 * What stays with each caller: its secret (each token family has its own key,
 * so rotating one never invalidates the others), its schema version, its
 * expiry rule, and the field-by-field check that turns the verified payload,
 * which this module returns as `unknown`, into the caller's type.
 *
 * `no-restricted-syntax` in eslint.config.js refuses a second declaration of
 * these names anywhere under src/.
 */

export const SIGNING_ALGORITHM: HmacImportParams = { name: 'HMAC', hash: 'SHA-256' }

const ENCODER = new TextEncoder()

/** URL-safe base64 without padding. Workers have no Buffer, so btoa over a binary string. */
export function base64UrlEncode(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** The inverse of base64UrlEncode. Throws on input that is not base64. */
export function base64UrlDecode(s: string): Uint8Array {
  const padded = s.replace(/-/g, '+').replace(/_/g, '/')
  const padLen = (4 - (padded.length % 4)) % 4
  const bin = atob(padded + '='.repeat(padLen))
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

/**
 * Import an HMAC-SHA256 key from a secret holding base64-encoded raw bytes
 * (generate with `openssl rand -base64 32`). `name` is the secret's name and
 * `purpose` finishes the sentence "before ...", so a missing key says which
 * secret and what it blocks.
 */
export async function importSigningKey(
  raw: string | undefined,
  name: string,
  purpose: string
): Promise<CryptoKey> {
  if (!raw || typeof raw !== 'string' || raw.trim().length === 0) {
    throw new Error(
      `${name} is not configured. Set it in wrangler env (32 random bytes, base64-encoded) before ${purpose}.`
    )
  }
  let bin: string
  try {
    bin = atob(raw)
  } catch {
    throw new Error(`${name} is not valid base64.`)
  }
  const keyBytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return crypto.subtle.importKey('raw', keyBytes, SIGNING_ALGORITHM, false, ['sign', 'verify'])
}

/** Sign a JSON-serializable payload into a `<payload>.<signature>` token. */
export async function signPayload(key: CryptoKey, payload: unknown): Promise<string> {
  const payloadB64 = base64UrlEncode(ENCODER.encode(JSON.stringify(payload)))
  const sigBuf = await crypto.subtle.sign(SIGNING_ALGORITHM, key, ENCODER.encode(payloadB64))
  return `${payloadB64}.${base64UrlEncode(new Uint8Array(sigBuf))}`
}

export type VerifiedPayload =
  { ok: true; payload: unknown } | { ok: false; error: 'malformed' | 'bad_signature' }

function splitToken(token: unknown): { payloadB64: string; sigB64: string } | null {
  if (typeof token !== 'string' || token.length === 0) return null
  const dot = token.indexOf('.')
  if (dot <= 0 || dot === token.length - 1) return null
  return { payloadB64: token.slice(0, dot), sigB64: token.slice(dot + 1) }
}

/**
 * Verify a token's signature, then decode its payload. The signature is
 * checked with `crypto.subtle.verify` (constant-time) before a byte of the
 * payload is parsed. The payload comes back as `unknown`: the caller checks
 * the version, the expiry and every field it reads.
 *
 * The key is loaded only once the token is structurally a token, so a
 * malformed token is refused as malformed even where the key is not staged.
 */
export async function verifySignedPayload(
  token: unknown,
  loadKey: () => Promise<CryptoKey>
): Promise<VerifiedPayload> {
  const parts = splitToken(token)
  if (!parts) return { ok: false, error: 'malformed' }

  let sigBytes: Uint8Array
  try {
    sigBytes = base64UrlDecode(parts.sigB64)
  } catch {
    return { ok: false, error: 'malformed' }
  }

  const valid = await crypto.subtle.verify(
    SIGNING_ALGORITHM,
    await loadKey(),
    sigBytes as unknown as ArrayBuffer,
    ENCODER.encode(parts.payloadB64)
  )
  if (!valid) return { ok: false, error: 'bad_signature' }

  try {
    const payload: unknown = JSON.parse(new TextDecoder().decode(base64UrlDecode(parts.payloadB64)))
    return { ok: true, payload }
  } catch {
    return { ok: false, error: 'malformed' }
  }
}
