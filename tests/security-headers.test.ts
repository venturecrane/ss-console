import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  BASE_SECURITY_HEADERS,
  HSTS_HEADER,
  HSTS_VALUE,
  securityHeadersFor,
  applySecurityHeaders,
} from '../src/lib/security/response-headers'

/**
 * Security response headers — the check that keeps A6 closed.
 *
 * THE FINDING. The 2026-08-23 review recorded, as an absence-lane claim, that
 * no security response header was set anywhere in the shipped code. It was
 * true at both layers: `grep -rniE` over `src/ workers/ public/ wrangler.toml`
 * returned 0, and `curl -sSIL https://portal.smd.services/` returned 0 while
 * the same command found `content-type` — the positive control proving the
 * probe could have come back non-zero.
 *
 * WHY A TEST AND NOT A NOTE. An absence closed by a commit reopens the moment
 * someone refactors the middleware wrapper, and nothing would say so. The
 * claim in `docs/reviews/claims-2026-08-25.md` had to be re-derived by hand
 * every review. This file re-derives it on every `npm run verify`.
 *
 * WHAT WOULD MAKE THESE ASSERTIONS FALSE (Law 12). Delete the
 * `applySecurityHeaders` call from `src/middleware.ts` and the wiring test
 * below goes red. Drop a header from `BASE_SECURITY_HEADERS` and the policy
 * test goes red. Return a bare `{}` from `securityHeadersFor` and every test
 * here goes red. Each was confirmed by tampering before this file was trusted.
 */

const MIDDLEWARE_SRC = resolve(__dirname, '../src/middleware.ts')

describe('security response headers — policy', () => {
  it('sets the four transport-independent headers', () => {
    // Named individually rather than snapshotted: a snapshot updated by
    // `-u` silently accepts a deletion, which is the exact regression this
    // guards. Removing any one of these fails here and cannot be blessed away.
    expect(BASE_SECURITY_HEADERS['X-Content-Type-Options']).toBe('nosniff')
    expect(BASE_SECURITY_HEADERS['Referrer-Policy']).toBe('strict-origin-when-cross-origin')
    expect(BASE_SECURITY_HEADERS['X-Frame-Options']).toBe('DENY')
    expect(BASE_SECURITY_HEADERS['Content-Security-Policy']).toBe("frame-ancestors 'none'")
  })

  it('ships HSTS over HTTPS and withholds it over plaintext', () => {
    expect(securityHeadersFor('https://portal.smd.services/')[HSTS_HEADER]).toBe(HSTS_VALUE)
    expect(securityHeadersFor('http://localhost:4321/')[HSTS_HEADER]).toBeUndefined()
    // An unparseable URL must still carry the base set. Failing closed on the
    // parse would mean failing OPEN on the headers.
    expect(securityHeadersFor('not a url')['X-Content-Type-Options']).toBe('nosniff')
  })

  it('does NOT ship a resource-loading CSP', () => {
    // The portal iframes app.signwell.com to sign the SOW, and Clerk loads its
    // own bundle. A `default-src`/`script-src`/`frame-src` policy authored
    // without enumerating those origins turns the signing page into a blank
    // box — the client's most important act, broken by a security header.
    // This assertion is a tripwire, not a preference: it fails the moment
    // someone adds a loading directive, forcing the enumeration to happen in
    // that PR rather than in production.
    const csp = BASE_SECURITY_HEADERS['Content-Security-Policy']
    expect(csp).not.toMatch(/default-src|script-src|frame-src|connect-src|style-src/)
  })
})

describe('security response headers — application', () => {
  it('sets headers on a mutable response', () => {
    const res = applySecurityHeaders(new Response('ok'), 'https://smd.services/')
    expect(res.headers.get('X-Frame-Options')).toBe('DENY')
    expect(res.headers.get(HSTS_HEADER)).toBe(HSTS_VALUE)
  })

  it('does not clobber a header the handler already set', () => {
    const res = applySecurityHeaders(
      new Response('ok', { headers: { 'X-Frame-Options': 'SAMEORIGIN' } }),
      'https://smd.services/'
    )
    expect(res.headers.get('X-Frame-Options')).toBe('SAMEORIGIN')
    // …while still adding the ones it did not set.
    expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff')
  })

  it('rebuilds a response whose headers are immutable rather than dropping them', () => {
    // `Response.redirect` produces immutable headers; `headers.set` throws.
    // Most of the auth surface returns redirects, so this branch carries the
    // portal's sign-in bounce — if it silently failed, the headers would be
    // absent on exactly the responses an unauthenticated visitor sees.
    const redirect = Response.redirect('https://portal.smd.services/auth/sign-in', 302)
    const res = applySecurityHeaders(redirect, 'https://portal.smd.services/')
    expect(res.status).toBe(302)
    expect(res.headers.get('location')).toBe('https://portal.smd.services/auth/sign-in')
    expect(res.headers.get('X-Frame-Options')).toBe('DENY')
  })
})

describe('security response headers — wiring', () => {
  it('middleware applies them around the whole request, not just around next()', () => {
    const code = readFileSync(MIDDLEWARE_SRC, 'utf-8')

    // Built is not wired. The header module can be perfect and unreachable.
    expect(code).toMatch(/applySecurityHeaders/)

    // Placement is the substance of the fix: `handleRequest` returns redirects
    // and 401/403 denials WITHOUT calling `next()`. Applying headers inside
    // `handleRequest` around `next()` would miss all of them. Assert the call
    // wraps the outer handler instead.
    expect(code).toMatch(/applySecurityHeaders\(\s*response\s*,\s*context\.request\.url\s*\)/)
    expect(code).toMatch(/const response = await withSentryRequestHandler\(/)
  })
})
