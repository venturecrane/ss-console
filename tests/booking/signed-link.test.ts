import { describe, it, expect, beforeEach } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'
import {
  signBookingLink,
  verifyBookingLink,
  DEFAULT_BOOKING_LINK_TTL_DAYS,
} from '../../src/lib/booking/signed-link'

// A 32-byte base64 key dedicated to these tests. HMAC keys can be any byte
// length, but we match the production encryption key's 32-byte shape so the
// import path exercised here is identical.
const TEST_KEY_BASE64 = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='

beforeEach(() => {
  for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
  ;(testEnv as unknown as Record<string, unknown>).BOOKING_ENCRYPTION_KEY = TEST_KEY_BASE64
})

describe('signBookingLink', () => {
  it('produces a `<payload>.<sig>` token', async () => {
    const token = await signBookingLink({
      entity_id: 'ent-1',
      contact_id: 'c-1',
      assessment_id: 'a-1',
      duration_minutes: 30,
    })
    const parts = token.split('.')
    expect(parts).toHaveLength(2)
    for (const part of parts) {
      expect(part).toMatch(/^[A-Za-z0-9_-]+$/)
      expect(part.length).toBeGreaterThan(10)
    }
  })

  it('produces stable signatures for the same payload (with fixed time)', async () => {
    const originalNow = Date.now
    try {
      Date.now = () => 1_700_000_000_000
      const a = await signBookingLink({
        entity_id: 'ent-1',
        contact_id: null,
        assessment_id: 'a-1',
        duration_minutes: 30,
      })
      const b = await signBookingLink({
        entity_id: 'ent-1',
        contact_id: null,
        assessment_id: 'a-1',
        duration_minutes: 30,
      })
      expect(a).toBe(b)
    } finally {
      Date.now = originalNow
    }
  })

  it('throws a clear error when BOOKING_ENCRYPTION_KEY is missing', async () => {
    delete (testEnv as unknown as Record<string, unknown>).BOOKING_ENCRYPTION_KEY
    await expect(
      signBookingLink({
        entity_id: 'ent-1',
        contact_id: null,
        assessment_id: 'a-1',
        duration_minutes: 30,
      })
    ).rejects.toThrow(/BOOKING_ENCRYPTION_KEY/)
  })
})

describe('verifyBookingLink', () => {
  it('round-trips a signed token back to its payload', async () => {
    const token = await signBookingLink({
      entity_id: 'ent-123',
      contact_id: 'c-456',
      assessment_id: 'a-789',
      duration_minutes: 45,
      meeting_type: 'discovery',
    })
    const result = await verifyBookingLink(token)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.payload.entity_id).toBe('ent-123')
    expect(result.payload.contact_id).toBe('c-456')
    expect(result.payload.assessment_id).toBe('a-789')
    expect(result.payload.duration_minutes).toBe(45)
    expect(result.payload.meeting_type).toBe('discovery')
    expect(result.payload.v).toBe(1)
  })

  it('defaults TTL to 14 days', async () => {
    const before = Math.floor(Date.now() / 1000)
    const token = await signBookingLink({
      entity_id: 'e',
      contact_id: null,
      assessment_id: 'a',
      duration_minutes: 30,
    })
    const result = await verifyBookingLink(token)
    if (!result.ok) throw new Error('expected ok')
    const expected = before + DEFAULT_BOOKING_LINK_TTL_DAYS * 24 * 60 * 60
    // Allow 5s slop for test execution time
    expect(result.payload.exp).toBeGreaterThanOrEqual(expected - 5)
    expect(result.payload.exp).toBeLessThanOrEqual(expected + 5)
  })

  it('rejects a token with a tampered payload', async () => {
    const token = await signBookingLink({
      entity_id: 'ent-a',
      contact_id: null,
      assessment_id: 'a-1',
      duration_minutes: 30,
    })
    const [payload, sig] = token.split('.')
    // Flip one char in the payload while keeping base64url charset
    const tamperedPayload = payload.slice(0, -1) + (payload.slice(-1) === 'A' ? 'B' : 'A')
    const tampered = `${tamperedPayload}.${sig}`
    const result = await verifyBookingLink(tampered)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(['bad_signature', 'malformed']).toContain(result.error)
  })

  it('rejects a token with a tampered signature', async () => {
    const token = await signBookingLink({
      entity_id: 'ent-a',
      contact_id: null,
      assessment_id: 'a-1',
      duration_minutes: 30,
    })
    const [payload, sig] = token.split('.')
    // Tamper the FIRST char (high-order bits of the signature). Tampering the
    // LAST char of a base64url-encoded signature is not guaranteed to change
    // the decoded byte value because base64 pads out to a 6-bit boundary and
    // the trailing bits may not map to meaningful bytes.
    const tamperedSig = (sig.charAt(0) === 'A' ? 'B' : 'A') + sig.slice(1)
    const result = await verifyBookingLink(`${payload}.${tamperedSig}`)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toBe('bad_signature')
  })

  it('rejects a token signed with a different key', async () => {
    const token = await signBookingLink({
      entity_id: 'ent-a',
      contact_id: null,
      assessment_id: 'a-1',
      duration_minutes: 30,
    })
    // Swap the key and try to verify
    ;(testEnv as unknown as Record<string, unknown>).BOOKING_ENCRYPTION_KEY =
      'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA='
    const result = await verifyBookingLink(token)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toBe('bad_signature')
  })

  it('rejects an expired token', async () => {
    const originalNow = Date.now
    try {
      Date.now = () => 1_700_000_000_000
      const token = await signBookingLink({
        entity_id: 'ent-a',
        contact_id: null,
        assessment_id: 'a-1',
        duration_minutes: 30,
        ttl_days: 1,
      })
      // Advance past the TTL window (2 days later)
      Date.now = () => 1_700_000_000_000 + 2 * 24 * 60 * 60 * 1000
      const result = await verifyBookingLink(token)
      expect(result.ok).toBe(false)
      if (!result.ok) expect(result.error).toBe('expired')
    } finally {
      Date.now = originalNow
    }
  })

  it('rejects a malformed token', async () => {
    for (const bad of ['', 'notoken', '..', 'only-one-part']) {
      const result = await verifyBookingLink(bad)
      expect(result.ok).toBe(false)
      if (!result.ok) expect(['malformed', 'bad_signature']).toContain(result.error)
    }
  })
})
