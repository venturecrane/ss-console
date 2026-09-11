/**
 * The legacy redirect table (src/lib/routing/legacy-redirects.ts), driven
 * as data, plus the two middleware facts no runtime test can observe.
 *
 * Until 2026-09-11 this file matched src/middleware.ts source text for the
 * subdomain rewrites, the auth gates, and the redirect rules. The rewrites
 * and gates are exercised at runtime in tests/middleware-behavior.test.ts
 * (which drives the exported onRequest against a migrated D1) and
 * tests/middleware-cross-site.test.ts; those assertions were deleted here
 * rather than duplicated (review 2026-09-10, Testing 3). What remains: the
 * redirect rules evaluated through the same `firstRedirect` the middleware
 * calls, and two drift guards explained inline.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  firstRedirect,
  POST_REWRITE_REDIRECTS,
  PRE_REWRITE_REDIRECTS,
  type RedirectContext,
} from '../src/lib/routing/legacy-redirects'

function ctx(href: string): RedirectContext {
  const url = new URL(href)
  return { hostname: url.hostname, pathname: url.pathname, url }
}

const post = (href: string) => firstRedirect(POST_REWRITE_REDIRECTS, ctx(href))
const pre = (href: string) => firstRedirect(PRE_REWRITE_REDIRECTS, ctx(href))

describe('legacy redirects: host canonicalization', () => {
  it('apex /admin and /admin/* move to the admin subdomain, keeping path and query', () => {
    expect(post('https://smd.services/admin')).toEqual({
      location: 'https://admin.smd.services/admin',
      status: 301,
    })
    expect(post('https://smd.services/admin/entities?stage=engaged')?.location).toBe(
      'https://admin.smd.services/admin/entities?stage=engaged'
    )
  })

  it('does not loop: the same paths on the admin subdomain match no rule', () => {
    expect(post('https://admin.smd.services/admin/entities')).toBeNull()
    expect(post('https://admin.smd.services/admin')).toBeNull()
  })

  it('a hostname that merely ends with the apex is not the apex', () => {
    expect(post('https://notsmd.services/admin')).toBeNull()
  })

  it('apex /portal/* moves to the portal subdomain the same way', () => {
    expect(post('https://smd.services/portal/billing')?.location).toBe(
      'https://portal.smd.services/portal/billing'
    )
    expect(post('https://portal.smd.services/portal/billing')).toBeNull()
  })
})

describe('legacy redirects: retired paths', () => {
  it('the dual-auth-era sign-in paths land on the unified sign-in and sign-up, query preserved', () => {
    expect(post('https://smd.services/auth/login?status=signed_out')?.location).toBe(
      'https://smd.services/auth/sign-in?status=signed_out'
    )
    expect(post('https://portal.smd.services/auth/portal-sign-in')?.location).toBe(
      'https://portal.smd.services/auth/sign-in'
    )
    expect(post('https://portal.smd.services/auth/portal-sign-up')?.location).toBe(
      'https://portal.smd.services/auth/sign-up'
    )
    expect(post('https://portal.smd.services/auth/portal-login')?.location).toBe(
      'https://portal.smd.services/auth/sign-in'
    )
    expect(post('https://smd.services/auth/sign-in')).toBeNull()
  })

  it('the portal IA rebuild: old list paths land on the new roots, old detail paths map into the subtree', () => {
    expect(post('https://portal.smd.services/portal/quotes')?.location).toBe(
      'https://portal.smd.services/portal/engagement'
    )
    expect(post('https://portal.smd.services/portal/quotes/q-1')?.location).toBe(
      'https://portal.smd.services/portal/engagement/proposals/q-1'
    )
    expect(post('https://portal.smd.services/portal/invoices')?.location).toBe(
      'https://portal.smd.services/portal/billing'
    )
    expect(post('https://portal.smd.services/portal/invoices/i-1')?.location).toBe(
      'https://portal.smd.services/portal/billing/invoices/i-1'
    )
    expect(post('https://portal.smd.services/portal/documents')?.location).toBe(
      'https://portal.smd.services/portal/engagement/documents'
    )
  })

  it('retired marketing surfaces go home; /why goes to the operator comparison; /contact is not retired', () => {
    for (const path of [
      '/scan',
      '/consulting',
      '/ai',
      '/scorecard/x',
      '/outside-view',
      '/consulting/a',
    ]) {
      expect(post(`https://smd.services${path}`)?.location, path).toBe('/')
    }
    expect(post('https://smd.services/why')?.location).toBe('/operator#compare')
    expect(post('https://smd.services/book/thanks')?.location).toBe('/get-started?booked=1')
    expect(post('https://smd.services/contact')).toBeNull()
  })

  it('bare /get-started goes home, but the post-booking form of it stays', () => {
    expect(post('https://smd.services/get-started')?.location).toBe('/')
    expect(post('https://smd.services/get-started?booked=1')).toBeNull()
  })

  it('the product rename runs before the rewrite and covers every surface, first occurrence only', () => {
    expect(pre('https://smd.services/ai-employee')?.location).toBe('/operator')
    expect(pre('https://smd.services/products/ai-employee/pricing')?.location).toBe(
      '/products/operator/pricing'
    )
    expect(pre('https://portal.smd.services/portal/products/ai-employee')?.location).toBe(
      '/portal/products/operator'
    )
    expect(pre('https://smd.services/operator')).toBeNull()
  })

  it('every rule is permanent, and none targets its own source (no self-loop)', () => {
    for (const rule of [...PRE_REWRITE_REDIRECTS, ...POST_REWRITE_REDIRECTS]) {
      expect(rule.status, rule.label).toBe(301)
    }
    for (const href of [
      'https://admin.smd.services/admin/entities',
      'https://smd.services/auth/sign-in',
      'https://portal.smd.services/portal/engagement/proposals/q-1',
      'https://smd.services/operator',
      'https://smd.services/',
    ]) {
      expect(post(href), href).toBeNull()
      expect(pre(href), href).toBeNull()
    }
  })
})

// Two facts a runtime test cannot observe, kept as drift guards with the
// reason each exists.
describe('middleware: facts outside the runtime tests (drift guards)', () => {
  it('404.astro is server-rendered, because a prerendered 404 is served from ASSETS and bypasses the middleware', () => {
    // A static dist/client/404.html would answer every path with no concrete
    // route (admin.smd.services/analytics, say) BEFORE the subdomain rewrite
    // ran, so the admin redirect would never fire for those paths.
    const code = readFileSync(resolve('src/pages/404.astro'), 'utf-8')
    expect(code).toMatch(/export\s+const\s+prerender\s*=\s*false/)
    expect(code).not.toMatch(/export\s+const\s+prerender\s*=\s*true/)
  })

  it('Clerk runs before the SS middleware, so locals.auth() is populated when the gates read it', () => {
    // tests/middleware-behavior.test.ts replaces clerkMiddleware with a
    // pass-through to drive ssMiddleware, so the composition order is the one
    // thing it cannot see.
    const code = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(code).toMatch(/sequence\(\s*clerkMiddleware\(\),\s*ssMiddleware\s*\)/)
  })
})
