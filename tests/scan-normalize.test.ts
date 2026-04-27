/**
 * Tests for the public /scan input normalizers (#598).
 *
 * Pure functions — no DB. Catches the "scheme-prefixed domain dodges
 * dedupe" bug + the "disposable-email throwaway" abuse vector that the
 * 4-dimensional rate limiter relies on.
 */

import { describe, it, expect } from 'vitest'
import {
  normalizeEmail,
  normalizeDomain,
  normalizeLinkedinUrl,
  emailDomain,
} from '../src/lib/scan/normalize'

describe('normalizeEmail', () => {
  it('lowercases and trims', () => {
    const r = normalizeEmail('  Owner@Example.COM  ')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value).toBe('owner@example.com')
  })

  it('rejects empty input', () => {
    expect(normalizeEmail('').ok).toBe(false)
    expect(normalizeEmail('   ').ok).toBe(false)
    expect(normalizeEmail(undefined).ok).toBe(false)
    expect(normalizeEmail(123).ok).toBe(false)
  })

  it('rejects malformed addresses', () => {
    expect(normalizeEmail('not-an-email').ok).toBe(false)
    expect(normalizeEmail('@nodomain.com').ok).toBe(false)
    expect(normalizeEmail('nolocal@').ok).toBe(false)
    expect(normalizeEmail('two@@example.com').ok).toBe(false)
    expect(normalizeEmail('no-tld@example').ok).toBe(false)
    expect(normalizeEmail('trailing.dot@example.com.').ok).toBe(false)
  })

  it('rejects control characters', () => {
    expect(normalizeEmail('a\nb@example.com').ok).toBe(false)
    expect(normalizeEmail('a\rb@example.com').ok).toBe(false)
  })

  it('rejects disposable email providers', () => {
    const r1 = normalizeEmail('test@mailinator.com')
    expect(r1.ok).toBe(false)
    if (!r1.ok) expect(r1.reason).toBe('email_disposable')
    const r2 = normalizeEmail('test@guerrillamail.com')
    expect(r2.ok).toBe(false)
  })

  it('accepts free email providers (per-IP gates instead)', () => {
    expect(normalizeEmail('owner@gmail.com').ok).toBe(true)
    expect(normalizeEmail('owner@outlook.com').ok).toBe(true)
    expect(normalizeEmail('owner@yahoo.com').ok).toBe(true)
  })

  it('caps length at 254 chars (RFC limit)', () => {
    const long = 'a'.repeat(250) + '@b.co'
    const r = normalizeEmail(long)
    expect(r.ok).toBe(false)
  })
})

describe('emailDomain', () => {
  it('extracts the domain after @', () => {
    expect(emailDomain('owner@example.com')).toBe('example.com')
    expect(emailDomain('first.last@subdomain.example.co.uk')).toBe('subdomain.example.co.uk')
  })

  it('returns empty string for invalid input', () => {
    expect(emailDomain('no-at-sign')).toBe('')
  })
})

describe('normalizeDomain', () => {
  it('strips https/http protocol', () => {
    expect((normalizeDomain('https://example.com') as { ok: true; value: string }).value).toBe(
      'example.com'
    )
    expect((normalizeDomain('http://example.com') as { ok: true; value: string }).value).toBe(
      'example.com'
    )
  })

  it('strips www. and trailing path', () => {
    const r = normalizeDomain('https://www.example.com/about?x=1')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value).toBe('example.com')
  })

  it('lowercases', () => {
    const r = normalizeDomain('EXAMPLE.com')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value).toBe('example.com')
  })

  it('canonicalizes case-and-protocol variants to the same string', () => {
    // Critical for rate-limit dedupe: an attacker can't bypass the
    // per-domain limit by submitting different scheme/case variants.
    const variants = [
      'example.com',
      'EXAMPLE.COM',
      'https://example.com',
      'http://www.Example.com/',
      'www.example.com',
    ]
    const normalized = variants.map((v) => {
      const r = normalizeDomain(v)
      return r.ok ? r.value : null
    })
    const uniq = new Set(normalized)
    expect(uniq.size).toBe(1)
    expect(uniq.has('example.com')).toBe(true)
  })

  it('rejects empty input', () => {
    expect(normalizeDomain('').ok).toBe(false)
    expect(normalizeDomain(undefined).ok).toBe(false)
  })

  it('rejects malformed input', () => {
    expect(normalizeDomain('not a domain').ok).toBe(false)
    expect(normalizeDomain('nodot').ok).toBe(false)
    expect(normalizeDomain('-leading-hyphen.com').ok).toBe(false)
    expect(normalizeDomain('trailing-hyphen-.com').ok).toBe(false)
  })

  it('rejects local / private / IP', () => {
    expect(normalizeDomain('localhost').ok).toBe(false)
    expect(normalizeDomain('myhost.local').ok).toBe(false)
    expect(normalizeDomain('myhost.internal').ok).toBe(false)
    expect(normalizeDomain('192.168.1.1').ok).toBe(false)
  })

  it('rejects control characters', () => {
    expect(normalizeDomain('exam\nple.com').ok).toBe(false)
  })
})

describe('normalizeLinkedinUrl', () => {
  it('returns null for empty/missing input', () => {
    const r1 = normalizeLinkedinUrl('')
    expect(r1.ok).toBe(true)
    if (r1.ok) expect(r1.value).toBe(null)
    const r2 = normalizeLinkedinUrl(undefined)
    expect(r2.ok).toBe(true)
  })

  it('accepts canonical company URLs', () => {
    const r = normalizeLinkedinUrl('https://linkedin.com/company/example')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value).toContain('linkedin.com')
  })

  it('rejects non-linkedin hosts', () => {
    const r = normalizeLinkedinUrl('https://twitter.com/example')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('linkedin_wrong_host')
  })

  it('rejects malformed URLs', () => {
    expect(normalizeLinkedinUrl('not a url').ok).toBe(false)
  })
})
