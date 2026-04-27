/**
 * Tests for the magic-link token utilities (#598).
 *
 * The tokens are the proof-of-intent gate before we burn an Anthropic
 * call. Three things must hold:
 *   1. Tokens are unguessable (256 bits of entropy, base64url).
 *   2. The DB stores only the SHA-256 hash, never the raw token —
 *      verified by checking hash() output is deterministic but never
 *      the input itself.
 *   3. The 24h TTL is enforced server-side from `created_at`.
 */

import { describe, it, expect } from 'vitest'
import {
  generateScanToken,
  hashScanToken,
  isScanTokenFresh,
  buildScanVerifyUrl,
} from '../src/lib/scan/tokens'

describe('generateScanToken', () => {
  it('returns a token + matching hash', async () => {
    const { token, hash } = await generateScanToken()
    expect(token).toBeTruthy()
    expect(hash).toBeTruthy()
    expect(token).not.toBe(hash)
    const recomputed = await hashScanToken(token)
    expect(recomputed).toBe(hash)
  })

  it('produces a URL-safe token (base64url)', async () => {
    for (let i = 0; i < 5; i++) {
      const { token } = await generateScanToken()
      expect(token).toMatch(/^[A-Za-z0-9_-]+$/)
    }
  })

  it('produces unique tokens (entropy sanity check)', async () => {
    const tokens = new Set<string>()
    for (let i = 0; i < 50; i++) {
      const { token } = await generateScanToken()
      tokens.add(token)
    }
    expect(tokens.size).toBe(50)
  })
})

describe('hashScanToken', () => {
  it('is deterministic for the same input', async () => {
    const a = await hashScanToken('hello-world')
    const b = await hashScanToken('hello-world')
    expect(a).toBe(b)
  })

  it('produces different hashes for different inputs', async () => {
    const a = await hashScanToken('one')
    const b = await hashScanToken('two')
    expect(a).not.toBe(b)
  })

  it('does not echo the input', async () => {
    const a = await hashScanToken('SECRET-token')
    expect(a).not.toContain('SECRET-token')
  })
})

describe('isScanTokenFresh', () => {
  it('returns true for a row created seconds ago', () => {
    const created = new Date(Date.now() - 5_000).toISOString()
    expect(isScanTokenFresh(created)).toBe(true)
  })

  it('returns false for a row created >24h ago', () => {
    const created = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString()
    expect(isScanTokenFresh(created)).toBe(false)
  })

  it('returns true at the boundary just under 24h', () => {
    const created = new Date(Date.now() - 23 * 60 * 60 * 1000).toISOString()
    expect(isScanTokenFresh(created)).toBe(true)
  })

  it('returns false for malformed input', () => {
    expect(isScanTokenFresh('not-iso8601')).toBe(false)
  })
})

describe('buildScanVerifyUrl', () => {
  it('produces /scan/verify/<token> path on the given base', () => {
    const url = buildScanVerifyUrl('https://smd.services', 'abc123')
    expect(url).toBe('https://smd.services/scan/verify/abc123')
  })

  it('URL-encodes the token', () => {
    const url = buildScanVerifyUrl('https://smd.services', 'a/b+c')
    expect(url).toContain('a%2Fb%2Bc')
  })
})
