import { describe, it, expect } from 'vitest'
import {
  generateManageToken,
  hashManageToken,
  computeManageTokenExpiry,
} from '../../src/lib/booking/tokens'

describe('generateManageToken', () => {
  it('returns a non-empty URL-safe base64 string', () => {
    const token = generateManageToken()
    expect(token.length).toBeGreaterThan(20)
    // URL-safe base64: only A-Z a-z 0-9 - _
    expect(token).toMatch(/^[A-Za-z0-9_-]+$/)
    // No padding
    expect(token).not.toContain('=')
  })

  it('produces a different token on each call (high entropy)', () => {
    const tokens = new Set(Array.from({ length: 100 }, () => generateManageToken()))
    expect(tokens.size).toBe(100)
  })

  it('encodes 32 bytes (~43 chars in url-safe base64)', () => {
    const token = generateManageToken()
    // 32 bytes = 256 bits → ceil(256/6) = 43 base64 chars without padding
    expect(token.length).toBe(43)
  })
})

describe('hashManageToken', () => {
  it('returns a stable SHA-256 hex string for the same input', async () => {
    const token = 'test-token-123'
    const a = await hashManageToken(token)
    const b = await hashManageToken(token)
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
  })

  it('returns different hashes for different inputs', async () => {
    const a = await hashManageToken('token-a')
    const b = await hashManageToken('token-b')
    expect(a).not.toBe(b)
  })

  it('matches a known SHA-256 vector', async () => {
    // sha256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    const hash = await hashManageToken('hello')
    expect(hash).toBe('2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824')
  })

  it('round trip: generate → hash → lookup-by-hash works', async () => {
    const raw = generateManageToken()
    const hash = await hashManageToken(raw)
    // Simulate lookup by hashing the incoming token and comparing
    const incoming = raw
    const incomingHash = await hashManageToken(incoming)
    expect(incomingHash).toBe(hash)
  })
})

describe('computeManageTokenExpiry', () => {
  it('returns slot_end_utc + ttlHours as ISO 8601', () => {
    const slotEnd = '2026-04-13T16:30:00.000Z'
    const expiry = computeManageTokenExpiry(slotEnd, 48)
    // 48 hours after 2026-04-13T16:30 = 2026-04-15T16:30
    expect(expiry).toBe('2026-04-15T16:30:00.000Z')
  })

  it('respects custom TTL hours', () => {
    const slotEnd = '2026-04-13T16:30:00.000Z'
    expect(computeManageTokenExpiry(slotEnd, 1)).toBe('2026-04-13T17:30:00.000Z')
    expect(computeManageTokenExpiry(slotEnd, 24)).toBe('2026-04-14T16:30:00.000Z')
  })
})
