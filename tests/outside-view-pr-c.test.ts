/**
 * Outside View Phase 1 PR-C — marketing route + redirects smoke tests.
 *
 * Static-source assertions covering:
 *   1. New /outside-view route exists with correct framing.
 *   2. Middleware 301 redirects from retired surfaces (/scan exact,
 *      /scorecard descendants, /get-started cold-mode).
 *   3. /scan/verify/[token] still resolves (in-flight magic-link
 *      tokens preserved).
 *   4. Legacy pages converted to 301 emitters as middleware fallback.
 *   5. /get-started preserves ?booked=1 prep behavior.
 *   6. Inbound CTAs updated to point at /outside-view.
 *   7. Email copy reference updated from smd.services/scan to
 *      smd.services/outside-view.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('PR-C: /outside-view route', () => {
  const path = 'src/pages/outside-view/index.astro'

  it('page exists', () => {
    expect(existsSync(resolve(path))).toBe(true)
  })

  const src = readFileSync(resolve(path), 'utf-8')

  it('uses Outside View framing (H1 "See what we see")', () => {
    expect(src).toMatch(/<h1[\s\S]*?>\s*See what we see\s*<\/h1>/)
  })

  it('does NOT use the retired "Operational Readiness Scan" framing', () => {
    expect(src).not.toMatch(/Operational Readiness Scan/)
  })

  it('CTA button reads "See what we see"', () => {
    expect(src).toMatch(/See what we see\s*<\/button>/)
  })

  it('form posts to /api/scan/start (internal endpoint name unchanged in v1)', () => {
    expect(src).toMatch(/['"]\/api\/scan\/start['"]/)
  })

  it('preserves the data-source disclosure (anti-fab discipline)', () => {
    expect(src).toMatch(/We look at[\s\S]*We don't look at/)
  })

  it('preserves the honeypot field (abuse defense)', () => {
    expect(src).toMatch(/name="company_url"/)
  })
})

describe('PR-C: middleware redirects', () => {
  const src = readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('/scan exact match redirects to /outside-view (NOT startsWith)', () => {
    // CRITICAL: must be exact equality, not startsWith. /scan/verify/[token]
    // is the magic-link landing for in-flight emails sent before this PR.
    expect(src).toMatch(
      /pathname === '\/scan'\s*\)\s*\{[\s\S]*?context\.redirect\(['"]\/outside-view['"],\s*301\)/
    )
    expect(src).not.toMatch(
      /pathname\.startsWith\(['"]\/scan['"]\)\s*\)\s*\{[\s\S]*?\/outside-view/
    )
  })

  it('/scorecard redirects to /outside-view (descendants too)', () => {
    expect(src).toMatch(
      /pathname === '\/scorecard'\s*\|\|\s*pathname\.startsWith\('\/scorecard\/'\)[\s\S]*?\/outside-view/
    )
  })

  it('/get-started redirects to /outside-view ONLY when no ?booked param', () => {
    expect(src).toMatch(
      /pathname === '\/get-started'\s*&&\s*!context\.url\.searchParams\.has\('booked'\)/
    )
  })

  it('uses 301 (permanent) for all retirement redirects', () => {
    // All Outside View redirects should be 301; matches existing apex
    // redirects' pattern.
    const redirectsBlock = src.slice(src.indexOf('Outside View redirects'))
    const ovChunk = redirectsBlock.slice(0, redirectsBlock.indexOf('Initialize session as null'))
    const matches = ovChunk.match(/context\.redirect\(['"]\/outside-view['"],\s*301\)/g)
    expect(matches).toBeTruthy()
    expect((matches ?? []).length).toBeGreaterThanOrEqual(3)
  })

  it('preserves the /book/thanks → /get-started?booked=1 redirect', () => {
    // The post-booking prep flow still routes through /get-started?booked=1
    // (which itself stays a live page, not a 301 emitter, when ?booked is
    // present). This existing redirect stays untouched in PR-C.
    expect(src).toMatch(/\/book\/thanks[\s\S]*?\/get-started\?booked=1.*?301/)
  })
})

describe('PR-C: legacy pages converted to 301 emitters', () => {
  it('/scan/index.astro emits Astro.redirect to /outside-view', () => {
    const src = readFileSync(resolve('src/pages/scan/index.astro'), 'utf-8')
    expect(src).toMatch(/Astro\.redirect\(['"]\/outside-view['"],\s*301\)/)
  })

  it('/scan/index.astro no longer renders the form (file is redirect-only)', () => {
    const src = readFileSync(resolve('src/pages/scan/index.astro'), 'utf-8')
    expect(src).not.toMatch(/<form/)
    expect(src).not.toMatch(/Operational Readiness Scan/)
  })

  it('/scorecard.astro emits Astro.redirect to /outside-view', () => {
    const src = readFileSync(resolve('src/pages/scorecard.astro'), 'utf-8')
    expect(src).toMatch(/Astro\.redirect\(['"]\/outside-view['"],\s*301\)/)
  })

  it('/scorecard.astro no longer renders the form', () => {
    const src = readFileSync(resolve('src/pages/scorecard.astro'), 'utf-8')
    expect(src).not.toMatch(/<form/)
  })

  it('/scan/verify/[token].astro NOT modified (in-flight tokens preserved)', () => {
    // The verify path is the magic-link landing for tokens issued before
    // PR-C shipped. It must still resolve.
    expect(existsSync(resolve('src/pages/scan/verify/[token].astro'))).toBe(true)
    const src = readFileSync(resolve('src/pages/scan/verify/[token].astro'), 'utf-8')
    // Should NOT have been turned into a redirect
    expect(src).not.toMatch(/Astro\.redirect\(['"]\/outside-view['"],\s*301\)/)
  })
})

describe('PR-C: /get-started ?booked=1 preserved', () => {
  const src = readFileSync(resolve('src/pages/get-started.astro'), 'utf-8')

  it('redirects cold-mode (no ?booked) to /outside-view', () => {
    expect(src).toMatch(/!isPostBooking[\s\S]*Astro\.redirect\(['"]\/outside-view['"],\s*301\)/)
  })

  it('still detects ?booked query param', () => {
    expect(src).toMatch(/searchParams\.has\(['"]booked['"]\)/)
  })

  it('still renders the prep questionnaire (post-booking mode preserved)', () => {
    // The Help-Us-Prepare H1 + form must still render.
    expect(src).toMatch(/Help Us Prepare/)
    expect(src).toMatch(/<form/)
  })
})

describe('PR-C: inbound CTAs updated', () => {
  it('Hero secondary CTA points at /outside-view with "See what we see" label', () => {
    const src = readFileSync(resolve('src/components/Hero.astro'), 'utf-8')
    expect(src).toMatch(/href=['"]\/outside-view['"][\s\S]*See what we see/)
    expect(src).not.toMatch(/href=['"]\/get-started['"]/)
  })

  it('FinalCta points at /outside-view (not /get-started)', () => {
    const src = readFileSync(resolve('src/components/FinalCta.astro'), 'utf-8')
    expect(src).toMatch(/href=['"]\/outside-view['"]/)
    expect(src).not.toMatch(/href=['"]\/get-started['"]/)
  })

  it('Homepage scorecard CTA replaced with /outside-view', () => {
    const src = readFileSync(resolve('src/pages/index.astro'), 'utf-8')
    expect(src).toMatch(/href=['"]\/outside-view['"]/)
    expect(src).not.toMatch(/href=['"]\/scorecard['"]/)
  })

  it('Book page intake escape hatch points at /outside-view (not /get-started)', () => {
    const src = readFileSync(resolve('src/pages/book.astro'), 'utf-8')
    // Only the post-booking redirect target /get-started?booked=1 should
    // remain. The pre-booking escape hatch must have been updated.
    expect(src).toMatch(/href=['"]\/outside-view['"]/)
    // The post-booking redirect target stays:
    expect(src).toMatch(/\/get-started\?booked=1/)
  })
})

describe('PR-C: email copy updated', () => {
  it('thin-footprint email no longer references smd.services/scan', () => {
    const src = readFileSync(resolve('src/lib/email/diagnostic-email.ts'), 'utf-8')
    // Negative lookahead avoids matching smd.services/outside-view (the new
    // reference) and any future smd.services/scan-* paths. Slashes inside
    // a character class don't need escaping.
    expect(src).not.toMatch(/smd\.services\/scan(?![/\w])/)
  })

  it('thin-footprint email references smd.services/outside-view', () => {
    const src = readFileSync(resolve('src/lib/email/diagnostic-email.ts'), 'utf-8')
    expect(src).toMatch(/smd\.services\/outside-view/)
  })
})
