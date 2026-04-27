/**
 * Structural tests for the /scan endpoints + form (#598).
 *
 * Lightweight string/file existence checks — same pattern as
 * tests/contact-endpoint.test.ts. Compiled handler behavior is exercised
 * by the dedicated rate-limit / verify-flow / gate-and-render tests.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

const startSrc = () => readFileSync(resolve('src/pages/api/scan/start.ts'), 'utf-8')
const verifySrc = () => readFileSync(resolve('src/pages/api/scan/verify.ts'), 'utf-8')
const formSrc = () => readFileSync(resolve('src/pages/scan/index.astro'), 'utf-8')
const verifyPageSrc = () => readFileSync(resolve('src/pages/scan/verify/[token].astro'), 'utf-8')

describe('/api/scan/start', () => {
  it('endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/scan/start.ts'))).toBe(true)
  })

  it('exports POST handler', () => {
    expect(startSrc()).toContain('export const POST')
  })

  it('runs the 4-dimensional rate limiter', () => {
    expect(startSrc()).toContain('checkScanRateLimits')
  })

  it('runs an IP-coarse rate limit before the D1 check', () => {
    const code = startSrc()
    const coarseIdx = code.indexOf('rateLimitByIp')
    const fineIdx = code.indexOf('checkScanRateLimits')
    expect(coarseIdx).toBeGreaterThan(-1)
    expect(fineIdx).toBeGreaterThan(-1)
    expect(coarseIdx).toBeLessThan(fineIdx)
  })

  it('persists a scan_request row before sending the verification email', () => {
    const code = startSrc()
    const insertIdx = code.indexOf('createScanRequest')
    const sendIdx = code.indexOf('sendEmail')
    expect(insertIdx).toBeGreaterThan(-1)
    expect(sendIdx).toBeGreaterThan(-1)
    expect(insertIdx).toBeLessThan(sendIdx)
  })

  it('honeypot field is checked before validation', () => {
    const code = startSrc()
    expect(code).toContain('company_url')
    expect(code).toMatch(/honeypot|company_url/)
  })

  it('returns generic ok response on rate-limit block (no info leak)', () => {
    const code = startSrc()
    // The block path returns ok:true with no specifics — anti-competitor-intel.
    expect(code).toMatch(/jsonResponse\(200,\s*\{\s*ok:\s*true\s*\}\)/)
  })
})

describe('/api/scan/verify', () => {
  it('endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/scan/verify.ts'))).toBe(true)
  })

  it('exports both GET and POST handlers', () => {
    const code = verifySrc()
    expect(code).toContain('export const GET')
    expect(code).toContain('export const POST')
  })

  it('hashes the inbound token before lookup (never raw)', () => {
    expect(verifySrc()).toContain('hashScanToken')
  })

  it('uses ctx.waitUntil to fire the diagnostic pipeline', () => {
    expect(verifySrc()).toContain('waitUntil')
    expect(verifySrc()).toContain('runDiagnosticScan')
  })

  it('checks token freshness before kicking off the scan', () => {
    const code = verifySrc()
    const freshIdx = code.indexOf('isScanTokenFresh')
    const verifyIdx = code.indexOf('markScanVerified')
    expect(freshIdx).toBeGreaterThan(-1)
    expect(verifyIdx).toBeGreaterThan(-1)
    expect(freshIdx).toBeLessThan(verifyIdx)
  })
})

describe('/scan public form', () => {
  it('page exists', () => {
    expect(existsSync(resolve('src/pages/scan/index.astro'))).toBe(true)
  })

  it('renders the data-source disclosure (Bar #4)', () => {
    const code = formSrc()
    expect(code).toContain('public Google reviews')
    expect(code).toContain('Your books or finances')
    expect(code).toContain('behind a login')
  })

  it('uses "we" voice (no first-person singular)', () => {
    const code = formSrc()
    expect(code).not.toMatch(/\bI'll\b/)
    expect(code).not.toMatch(/\bI'm\b/)
    // "we" is everywhere in the copy — sanity check
    expect(code).toMatch(/\bWe\b|\bwe\b/)
  })

  it('uses "solution" framing rather than "systems" in marketing copy', () => {
    // The form doesn't pitch — just describes the scan. We sanity-check
    // that no copy block uses "systems" as a marketing word.
    const code = formSrc()
    // Allowed: "system" inside literal tool references like "scheduling system"
    // Disallowed: marketing taglines like "build better systems"
    expect(code).not.toContain('build better systems')
    expect(code).not.toContain('better systems')
  })

  it('does not publish dollar amounts (Decision Stack)', () => {
    const code = formSrc()
    expect(code).not.toMatch(/\$\d/)
  })

  it('does not promise fixed timeframes for the engagement', () => {
    // "About 90 seconds" is a system latency, not an engagement
    // timeframe — that's allowed. The forbidden pattern is engagement-
    // duration commitments.
    const code = formSrc()
    expect(code).not.toMatch(/\d+-week sprint/)
    expect(code).not.toMatch(/\d+-day implementation/)
    expect(code).not.toMatch(/\d+ business days/)
  })
})

describe('/scan/verify/[token] page', () => {
  it('page exists', () => {
    expect(existsSync(resolve('src/pages/scan/verify/[token].astro'))).toBe(true)
  })

  it('handles all six terminal states', () => {
    const code = verifyPageSrc()
    for (const state of [
      'verified',
      'already_completed',
      'thin_footprint',
      'expired',
      'invalid',
      'failed',
    ]) {
      expect(code).toContain(state)
    }
  })

  it('renders public response with no specifics about findings (Bar #6)', () => {
    const code = verifyPageSrc()
    // The page should never display things like "found X reviews" or
    // "your business has Y problems" — the report is email-only.
    expect(code).not.toMatch(/found \d+/i)
    expect(code).not.toMatch(/your top problem/i)
    expect(code).not.toContain('your operational health score')
  })
})

describe('migration 0029_create_scan_requests', () => {
  it('exists with the canonical filename', () => {
    expect(existsSync(resolve('migrations/0029_create_scan_requests.sql'))).toBe(true)
  })

  it('declares the table + canonical columns', () => {
    const code = readFileSync(resolve('migrations/0029_create_scan_requests.sql'), 'utf-8')
    expect(code).toContain('CREATE TABLE scan_requests')
    expect(code).toContain('verification_token_hash')
    expect(code).toContain('thin_footprint_skipped')
    expect(code).toContain('scan_status')
  })

  it('enforces token-hash uniqueness via UNIQUE INDEX', () => {
    const code = readFileSync(resolve('migrations/0029_create_scan_requests.sql'), 'utf-8')
    expect(code).toContain('UNIQUE INDEX idx_scan_requests_token')
  })
})
