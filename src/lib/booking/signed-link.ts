/**
 * Signed booking links for admin-initiated "Send booking link" flow (#467).
 *
 * An admin creates a pre-seeded meeting row and hands the prospect a URL like
 * `/book?t=<signed-token>`. The token carries the identifiers the public
 * booking page needs to attach the prospect's chosen slot to the pre-created
 * row, and a TTL so stale URLs can't be used indefinitely.
 *
 * Threat model:
 *   - Anyone with the URL can book on behalf of the named entity. That is
 *     the intended behavior — the admin explicitly mailed the URL to that
 *     contact.
 *   - The URL must not be forgeable. We HMAC-SHA256 the JSON payload with
 *     `BOOKING_ENCRYPTION_KEY` (base64, 32 bytes). An attacker without the
 *     key cannot mint a valid token pointing at any entity.
 *   - The URL must expire. `exp` is enforced on verify; default TTL is
 *     14 days.
 *
 * Encoding:
 *   `<base64url(json-payload)>.<base64url(hmac)>`
 *
 * Payload fields:
 *   v          — schema version (currently 1)
 *   entity_id  — pre-existing entity (admin-created prospect)
 *   contact_id — primary contact to attach the booking to (may be null)
 *   assessment_id — pre-created assessment row in `scheduled` status
 *   duration_minutes — admin-chosen meeting duration
 *   meeting_type — admin-chosen meeting type (free-form; optional)
 *   exp        — Unix seconds; token invalid after this time
 */
import { env } from 'cloudflare:workers'

const ALGORITHM: HmacImportParams = { name: 'HMAC', hash: 'SHA-256' }
const ENCODER = new TextEncoder()
const SCHEMA_VERSION = 1

/** 14 days is the product default per issue #467. */
export const DEFAULT_BOOKING_LINK_TTL_DAYS = 14

export interface BookingLinkPayload {
  v: number
  entity_id: string
  contact_id: string | null
  assessment_id: string
  duration_minutes: number
  meeting_type: string | null
  /** Unix seconds. */
  exp: number
}

export interface SignBookingLinkInput {
  entity_id: string
  contact_id: string | null
  assessment_id: string
  duration_minutes: number
  meeting_type?: string | null
  /** Override the default TTL. */
  ttl_days?: number
}

export type VerifyResult =
  | { ok: true; payload: BookingLinkPayload }
  | { ok: false; error: 'malformed' | 'bad_signature' | 'expired' | 'unknown_version' }

/**
 * Sign a booking-link token. Returns the token string (payload.signature).
 */
export async function signBookingLink(input: SignBookingLinkInput): Promise<string> {
  const key = await importSigningKey()
  const ttlDays = input.ttl_days ?? DEFAULT_BOOKING_LINK_TTL_DAYS
  const exp = Math.floor(Date.now() / 1000) + ttlDays * 24 * 60 * 60

  const payload: BookingLinkPayload = {
    v: SCHEMA_VERSION,
    entity_id: input.entity_id,
    contact_id: input.contact_id,
    assessment_id: input.assessment_id,
    duration_minutes: input.duration_minutes,
    meeting_type: input.meeting_type ?? null,
    exp,
  }

  const payloadB64 = base64UrlEncode(ENCODER.encode(JSON.stringify(payload)))
  const sigBuf = await crypto.subtle.sign(ALGORITHM, key, ENCODER.encode(payloadB64))
  const sigB64 = base64UrlEncode(new Uint8Array(sigBuf))
  return `${payloadB64}.${sigB64}`
}

/**
 * Verify a token and return the payload if valid. Uses a constant-time
 * comparison on the signature bytes to avoid timing side-channels.
 */
export async function verifyBookingLink(token: string): Promise<VerifyResult> {
  if (typeof token !== 'string' || token.length === 0) {
    return { ok: false, error: 'malformed' }
  }

  const dot = token.indexOf('.')
  if (dot <= 0 || dot === token.length - 1) {
    return { ok: false, error: 'malformed' }
  }

  const payloadB64 = token.slice(0, dot)
  const sigB64 = token.slice(dot + 1)

  let sigBytes: Uint8Array
  try {
    sigBytes = base64UrlDecode(sigB64)
  } catch {
    return { ok: false, error: 'malformed' }
  }

  const key = await importSigningKey()
  const valid = await crypto.subtle.verify(
    ALGORITHM,
    key,
    sigBytes as unknown as ArrayBuffer,
    ENCODER.encode(payloadB64)
  )
  if (!valid) return { ok: false, error: 'bad_signature' }

  let payload: BookingLinkPayload
  try {
    const json = new TextDecoder().decode(base64UrlDecode(payloadB64))
    payload = JSON.parse(json) as BookingLinkPayload
  } catch {
    return { ok: false, error: 'malformed' }
  }

  if (payload.v !== SCHEMA_VERSION) {
    return { ok: false, error: 'unknown_version' }
  }

  const now = Math.floor(Date.now() / 1000)
  if (typeof payload.exp !== 'number' || payload.exp < now) {
    return { ok: false, error: 'expired' }
  }

  if (!payload.entity_id || !payload.assessment_id) {
    return { ok: false, error: 'malformed' }
  }

  return { ok: true, payload }
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

async function importSigningKey(): Promise<CryptoKey> {
  const raw = env.BOOKING_ENCRYPTION_KEY
  if (!raw || typeof raw !== 'string' || raw.trim().length === 0) {
    throw new Error(
      'BOOKING_ENCRYPTION_KEY is not configured. Set it in wrangler env before issuing signed booking links.'
    )
  }
  const keyBytes = Uint8Array.from(atob(raw), (c) => c.charCodeAt(0))
  return crypto.subtle.importKey('raw', keyBytes, ALGORITHM, false, ['sign', 'verify'])
}

function base64UrlEncode(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  const b64 = btoa(bin)
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function base64UrlDecode(s: string): Uint8Array {
  const padded = s.replace(/-/g, '+').replace(/_/g, '/')
  const padLen = (4 - (padded.length % 4)) % 4
  const b64 = padded + '='.repeat(padLen)
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}
