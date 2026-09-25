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
import { isRecord } from '../api/helpers'
import { importSigningKey, signPayload, verifySignedPayload } from '../security/signed-payload'

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

function linkSigningKey(): Promise<CryptoKey> {
  return importSigningKey(
    env.BOOKING_ENCRYPTION_KEY,
    'BOOKING_ENCRYPTION_KEY',
    'issuing signed booking links'
  )
}

/**
 * Sign a booking-link token. Returns the token string (payload.signature).
 */
export async function signBookingLink(input: SignBookingLinkInput): Promise<string> {
  const key = await linkSigningKey()
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
  return signPayload(key, payload)
}

const nullableString = (value: unknown): value is string | null | undefined =>
  value == null || typeof value === 'string'

/**
 * Verify a token and return the payload if valid. The signature is checked in
 * constant time (crypto.subtle.verify) before the payload is read; every field
 * the booking page uses is then checked, not cast.
 */
export async function verifyBookingLink(token: string): Promise<VerifyResult> {
  const verified = await verifySignedPayload(token, linkSigningKey)
  if (!verified.ok) return verified
  const p = verified.payload
  if (!isRecord(p)) return { ok: false, error: 'malformed' }

  if (p.v !== SCHEMA_VERSION) {
    return { ok: false, error: 'unknown_version' }
  }

  const now = Math.floor(Date.now() / 1000)
  if (typeof p.exp !== 'number' || p.exp < now) {
    return { ok: false, error: 'expired' }
  }

  const { entity_id, assessment_id, contact_id, duration_minutes, meeting_type } = p
  if (
    typeof entity_id !== 'string' ||
    !entity_id ||
    typeof assessment_id !== 'string' ||
    !assessment_id ||
    typeof duration_minutes !== 'number' ||
    !nullableString(contact_id) ||
    !nullableString(meeting_type)
  ) {
    return { ok: false, error: 'malformed' }
  }

  return {
    ok: true,
    payload: {
      v: SCHEMA_VERSION,
      entity_id,
      contact_id: contact_id ?? null,
      assessment_id,
      duration_minutes,
      meeting_type: meeting_type ?? null,
      exp: p.exp,
    },
  }
}
