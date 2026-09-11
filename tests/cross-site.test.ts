/**
 * Unit coverage for src/lib/security/cross-site.ts, the CSRF defence-in-depth
 * predicate the middleware applies after auth on state-changing requests to
 * the signed-in surfaces. tests/middleware-cross-site.test.ts drives the same
 * predicate through the real middleware.
 */
import { describe, expect, it } from 'vitest'
import { classifyCrossSite, isSignedInSurface } from '../src/lib/security/cross-site'

function req(url: string, method: string, headers: Record<string, string> = {}): Request {
  return new Request(url, { method, headers })
}

describe('isSignedInSurface', () => {
  it('covers admin and portal pages and APIs, nothing else', () => {
    expect(isSignedInSurface('/admin')).toBe(true)
    expect(isSignedInSurface('/admin/clients/1')).toBe(true)
    expect(isSignedInSurface('/api/admin/clients/1/operator-price')).toBe(true)
    expect(isSignedInSurface('/portal')).toBe(true)
    expect(isSignedInSurface('/api/portal/billing/manage')).toBe(true)
    expect(isSignedInSurface('/api/webhooks/stripe')).toBe(false)
    expect(isSignedInSurface('/api/internal/heartbeat')).toBe(false)
    expect(isSignedInSurface('/book')).toBe(false)
  })
})

describe('classifyCrossSite', () => {
  const URL_ = 'https://admin.smd.services/api/admin/clients/1/operator-price'

  it('never flags a safe method, even from cross-site', () => {
    for (const method of ['GET', 'HEAD', 'OPTIONS']) {
      expect(classifyCrossSite(req(URL_, method, { 'sec-fetch-site': 'cross-site' }))).toEqual({
        crossSite: false,
      })
    }
  })

  it('never flags a mutation outside the signed-in surfaces', () => {
    const webhook = 'https://smd.services/api/webhooks/stripe'
    expect(
      classifyCrossSite(
        req(webhook, 'POST', { 'sec-fetch-site': 'cross-site', origin: 'https://evil.example' })
      )
    ).toEqual({ crossSite: false })
  })

  it('refuses Sec-Fetch-Site: cross-site on a mutation', () => {
    expect(classifyCrossSite(req(URL_, 'POST', { 'sec-fetch-site': 'cross-site' }))).toEqual({
      crossSite: true,
      reason: 'sec-fetch-site',
    })
  })

  it('allows same-origin, same-site, and none fetch sites', () => {
    for (const site of ['same-origin', 'same-site', 'none']) {
      expect(classifyCrossSite(req(URL_, 'POST', { 'sec-fetch-site': site }))).toEqual({
        crossSite: false,
      })
    }
  })

  it('refuses an Origin that differs from the request origin', () => {
    expect(classifyCrossSite(req(URL_, 'POST', { origin: 'https://evil.example' }))).toEqual({
      crossSite: true,
      reason: 'origin-mismatch',
    })
    // The apex is a different origin from the admin host.
    expect(classifyCrossSite(req(URL_, 'POST', { origin: 'https://smd.services' }))).toEqual({
      crossSite: true,
      reason: 'origin-mismatch',
    })
  })

  it('allows a matching Origin and treats an opaque "null" Origin as absent', () => {
    expect(classifyCrossSite(req(URL_, 'POST', { origin: 'https://admin.smd.services' }))).toEqual({
      crossSite: false,
    })
    expect(classifyCrossSite(req(URL_, 'POST', { origin: 'null' }))).toEqual({ crossSite: false })
  })

  it('Sec-Fetch-Site wins over a matching Origin', () => {
    expect(
      classifyCrossSite(
        req(URL_, 'POST', { 'sec-fetch-site': 'cross-site', origin: 'https://admin.smd.services' })
      )
    ).toEqual({ crossSite: true, reason: 'sec-fetch-site' })
  })

  it('allows a request carrying neither header (non-browser client)', () => {
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
      expect(classifyCrossSite(req(URL_, method))).toEqual({ crossSite: false })
    }
  })
})
