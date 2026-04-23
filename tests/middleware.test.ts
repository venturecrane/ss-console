import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Source-level guards for src/middleware.ts.
 *
 * The middleware runs against D1/KV bindings at request time. Integration
 * tests would need a full runtime harness. These tests enforce the
 * architectural invariants at the source level: the three-subdomain
 * routing, strict hostname equality on the legacy redirect, and the
 * cookie-refresh guard that keeps admin cookies off the apex.
 */
describe('middleware: admin subdomain rewrite', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('detects admin subdomain with startsWith("admin.")', () => {
    expect(source()).toContain("hostname.startsWith('admin.')")
  })

  it('admin rewrite exempts paths already under /admin', () => {
    const code = source()
    expect(code).toMatch(/isAdminSubdomain[\s\S]*!pathname\.startsWith\('\/admin'\)/)
  })

  it('admin rewrite exempts /api/admin', () => {
    const code = source()
    expect(code).toMatch(/isAdminSubdomain[\s\S]*!pathname\.startsWith\('\/api\/admin'\)/)
  })

  it('admin rewrite exempts /auth and /api/auth', () => {
    const code = source()
    expect(code).toMatch(/isAdminSubdomain[\s\S]*!pathname\.startsWith\('\/auth'\)/)
    expect(code).toMatch(/isAdminSubdomain[\s\S]*!pathname\.startsWith\('\/api\/auth'\)/)
  })

  it('admin rewrite prepends /admin to non-admin paths', () => {
    const code = source()
    expect(code).toMatch(
      /adminPath\s*=\s*pathname\s*===\s*'\/'\s*\?\s*'\/admin'\s*:\s*`\/admin\$\{pathname\}`/
    )
  })

  it('admin rewrite uses context.rewrite, not redirect', () => {
    const code = source()
    // Rewrite is transparent — user stays on admin.smd.services in the URL bar.
    const adminBlock = code.slice(code.indexOf('isAdminSubdomain'))
    expect(adminBlock).toContain('context.rewrite(')
  })
})

describe('middleware: legacy apex redirects', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('uses strict hostname equality for the apex admin redirect', () => {
    // CRITICAL: startsWith/endsWith would also match admin.smd.services and loop.
    const code = source()
    expect(code).toContain("hostname === 'smd.services'")
  })

  it('redirects apex /admin/* to admin subdomain', () => {
    const code = source()
    expect(code).toMatch(
      /hostname\s*===\s*'smd\.services'[\s\S]*?pathname\.startsWith\('\/admin\/'\)/
    )
    expect(code).toContain("newUrl.hostname = 'admin.smd.services'")
  })

  it('redirects apex /auth/login to admin subdomain', () => {
    const code = source()
    expect(code).toMatch(
      /hostname\s*===\s*'smd\.services'[\s\S]*?pathname\.startsWith\('\/auth\/login'\)/
    )
  })

  it('uses 301 for backwards-compat redirects', () => {
    const code = source()
    // Both legacy redirects should be 301 permanent
    expect(code).toMatch(/context\.redirect\(newUrl\.toString\(\),\s*301\)/)
  })

  it('does NOT redirect admin.smd.services to itself (no loop)', () => {
    // The guard hostname === 'smd.services' is strict equality, not endsWith.
    // admin.smd.services.endsWith('smd.services') is true, which would loop.
    const code = source()
    expect(code).not.toContain("hostname.endsWith('smd.services')")
  })
})

describe('middleware: cookie refresh guard', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('checks both admin and portal host patterns', () => {
    const code = source()
    expect(code).toContain("hostname.startsWith('portal.')")
    expect(code).toContain("hostname.startsWith('admin.')")
  })

  it('refreshes cookie only when role matches host', () => {
    const code = source()
    expect(code).toMatch(
      /hostMatches\s*=\s*\(isClientSession\s*&&\s*isPortalHost\)\s*\|\|\s*\(isAdminSession\s*&&\s*isAdminHost\)/
    )
  })

  it('clears stale admin cookie on apex', () => {
    // Pre-migration admin cookies on smd.services should be proactively cleared.
    const code = source()
    expect(code).toContain('buildClearSessionCookie')
    expect(code).toMatch(/hostname\s*===\s*'smd\.services'\s*&&\s*isAdminSession/)
  })

  it('imports buildClearSessionCookie', () => {
    expect(source()).toContain('buildClearSessionCookie')
  })
})

describe('middleware: portal rewrite preserved (regression)', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('still detects portal subdomain', () => {
    expect(source()).toContain("hostname.startsWith('portal.')")
  })

  it('still rewrites non-portal paths on the portal subdomain', () => {
    const code = source()
    expect(code).toMatch(
      /portalPath\s*=\s*pathname\s*===\s*'\/'\s*\?\s*'\/portal'\s*:\s*`\/portal\$\{pathname\}`/
    )
  })
})

describe('middleware: 404 route must be SSR (regression lock-in)', () => {
  // If 404.astro is prerendered, Astro's renderError fallback serves the
  // static dist/client/404.html via the ASSETS binding and BYPASSES
  // middleware. That breaks subdomain rewrite for every path that doesn't
  // match a concrete Astro route (admin.smd.services/analytics etc.) and
  // users see the marketing-layout 404 instead of the intended admin
  // redirect. Keep 404 server-rendered — middleware must always run.
  const source = () => readFileSync(resolve('src/pages/404.astro'), 'utf-8')

  it('404.astro must have prerender = false', () => {
    const code = source()
    expect(code).toMatch(/export\s+const\s+prerender\s*=\s*false/)
    expect(code).not.toMatch(/export\s+const\s+prerender\s*=\s*true/)
  })
})

describe('middleware: session resolution gating (#20)', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('only reads the cookie header on routes that can use a session', () => {
    const code = source()
    // Marketing pages are prerendered; reading request headers during
    // prerender triggers a build warning. The cookie read must be gated
    // behind a `needsSession` check so static pages stay clean.
    expect(code).toContain('const needsSession =')
    expect(code).toMatch(/if\s*\(\s*needsSession\s*\)/)
  })

  it('needsSession includes protected, auth, and API routes', () => {
    const code = source()
    // All three classes can legitimately consume locals.session:
    // - protected (admin/portal) require it
    // - auth routes read it (e.g. renewal flows)
    // - API routes include /api/auth/google/connect etc.
    expect(code).toContain('isProtectedRoute || isAuthRoute || isApiRoute')
  })
})

describe('middleware: admin login host guard', () => {
  const source = () => readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')

  it('rejects admin login POST when host is not admin.*', () => {
    const code = source()
    expect(code).toContain("requestHost.startsWith('admin.')")
    expect(code).toContain('wrong_host')
  })

  it('allows local dev hostnames (localhost, 127.0.0.1)', () => {
    const code = source()
    expect(code).toContain('localhost')
    expect(code).toContain('127.0.0.1')
  })
})

describe('middleware: login page shows wrong_host error', () => {
  it('login page maps wrong_host error to a user-visible message', () => {
    const code = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(code).toContain('wrong_host')
    expect(code).toContain('admin.smd.services')
  })
})
