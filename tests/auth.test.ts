import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Source-level guards for the auth subsystem.
 *
 * Post 2026-05-25 Clerk-unified migration, this file covers:
 *   - session.ts (the magic-link path that survived for client invites)
 *   - middleware.ts (admin shim + portal Clerk + legacy fallback)
 *   - AdminLayout.astro (Clerk SignOutButton chrome)
 *   - admin-session-shim.ts (Clerk userId → SessionData adapter)
 *
 * Removed legacy suites (covered the PBKDF2 admin path that PR 3 deleted):
 *   - auth: password module
 *   - auth: login page
 *   - auth: API endpoints (logout/login)
 *   - auth: login endpoint rate limiting
 */

describe('auth: session module', () => {
  it('session.ts exports the core helpers still used by /auth/verify', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('export async function createSession')
    expect(source).toContain('export async function validateSession')
    expect(source).toContain('export async function renewSession')
    expect(source).toContain('export function buildSessionCookie')
    expect(source).toContain('export function parseSessionToken')
  })

  it('admin session duration is 7 days', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('7 * 24 * 60 * 60 * 1000')
  })

  it('uses cryptographically random session tokens', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('crypto.randomUUID()')
  })

  it('cookie is HttpOnly, Secure, SameSite=Lax', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('HttpOnly')
    expect(source).toContain('Secure')
    expect(source).toContain('SameSite=Lax')
  })

  it('validates via KV first then falls back to D1', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('kv.get')
    expect(source).toContain('SELECT * FROM sessions WHERE token')
  })

  it('client session duration is 30 days', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('30 * 24 * 60 * 60 * 1000')
  })

  it('exports getSessionDurationMs helper', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('export function getSessionDurationMs')
  })
})

describe('auth: buildSessionCookie behavior', () => {
  it('pins the cookie attributes the CSRF and XSS posture rests on', async () => {
    // HttpOnly keeps the token from page script; Secure keeps it off plain
    // HTTP; SameSite=Lax is the first CSRF layer (src/lib/security/cross-site.ts
    // is the second). A change to any of these is a security change and should
    // turn this red (2026-09-09 review, Security LOW 7).
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const attrs = buildSessionCookie('test-token', 'client')
      .split(';')
      .map((part) => part.trim())
    expect(attrs).toContain('HttpOnly')
    expect(attrs).toContain('Secure')
    expect(attrs).toContain('SameSite=Lax')
    expect(attrs).toContain('Path=/')
  })

  it('sets 30-day Max-Age for client role', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test-token', 'client')
    expect(cookie).toContain('Max-Age=2592000')
  })

  it('sets 7-day Max-Age for admin role', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test-token', 'admin')
    expect(cookie).toContain('Max-Age=604800')
  })

  it('defaults to admin duration when role omitted', async () => {
    const { buildSessionCookie } = await import('../src/lib/auth/session')
    const cookie = buildSessionCookie('test-token')
    expect(cookie).toContain('Max-Age=604800')
  })
})

describe('auth: admin session shim', () => {
  it('shim file exists', () => {
    expect(existsSync(resolve('src/lib/auth/admin-session-shim.ts'))).toBe(true)
  })

  it('exports resolveAdminSessionFromClerk', () => {
    const source = readFileSync(resolve('src/lib/auth/admin-session-shim.ts'), 'utf-8')
    expect(source).toContain('export async function resolveAdminSessionFromClerk')
  })

  it('gates resolution to role=admin in the SQL', () => {
    const source = readFileSync(resolve('src/lib/auth/admin-session-shim.ts'), 'utf-8')
    expect(source).toContain("WHERE clerk_user_id = ? AND role = 'admin'")
  })

  it('returns SessionData shape matching legacy locals.session', () => {
    const source = readFileSync(resolve('src/lib/auth/admin-session-shim.ts'), 'utf-8')
    expect(source).toContain('userId:')
    expect(source).toContain('orgId:')
    expect(source).toContain('email:')
    expect(source).toContain("role: 'admin'")
  })

  it('caches resolved sessions in KV with a bounded TTL', () => {
    const source = readFileSync(resolve('src/lib/auth/admin-session-shim.ts'), 'utf-8')
    expect(source).toContain('admin-session:')
    expect(source).toContain('expirationTtl')
  })
})

describe('auth: middleware', () => {
  it('middleware.ts exists', () => {
    expect(existsSync(resolve('src/middleware.ts'))).toBe(true)
  })

  it('protects /admin routes', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('/admin')
    expect(source).toContain("pathname.startsWith('/admin')")
  })

  it('redirects unauthenticated requests to the unified sign-in', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("'/auth/sign-in'")
  })

  it('attaches session to locals', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('context.locals.session = sessionData')
  })

  it('renews session on each authenticated request', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('renewSession')
  })

  it('populates locals.session for admin paths via the Clerk shim', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('resolveAdminSessionFromClerk')
  })
})

describe('auth: unified sign-in pages', () => {
  it('sign-in page exists', () => {
    expect(existsSync(resolve('src/pages/auth/sign-in.astro'))).toBe(true)
  })

  it('sign-up page exists', () => {
    expect(existsSync(resolve('src/pages/auth/sign-up.astro'))).toBe(true)
  })

  it('after-sign-in dispatcher exists', () => {
    expect(existsSync(resolve('src/pages/auth/after-sign-in.ts'))).toBe(true)
  })

  it('sign-in page forces redirect through /auth/after-sign-in', () => {
    const source = readFileSync(resolve('src/pages/auth/sign-in.astro'), 'utf-8')
    expect(source).toContain('forceRedirectUrl="/auth/after-sign-in"')
  })

  it('after-sign-in dispatcher routes by users.role via ensureLocalUser (so pre-Clerk rows auto-link by email)', () => {
    const source = readFileSync(resolve('src/pages/auth/after-sign-in.ts'), 'utf-8')
    expect(source).toContain('resolveAdminSessionFromClerk')
    expect(source).toContain('ensureLocalUser')
    expect(source).toContain('locals.currentUser()')
    // The raw clerk_user_id-only SELECT was the bug: pre-Clerk users
    // rows with NULL clerk_user_id never matched. Guard against the
    // regression by asserting that direct lookup is gone.
    expect(source).not.toContain('SELECT role, entity_id FROM users WHERE clerk_user_id = ?')
  })

  it('sign-in page renders a sign-out recovery option when status=no_subscription', () => {
    const source = readFileSync(resolve('src/pages/auth/sign-in.astro'), 'utf-8')
    expect(source).toContain('SignOutButton')
    expect(source).toContain("status === 'no_subscription'")
  })
})

describe('auth: after-sign-in surface selection (host-aware dual-role dispatch)', () => {
  const ADMIN = 'admin.smd.services'
  const PORTAL = 'portal.smd.services'

  it('routes a dual-eligible user to the host they signed in from', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    const base = { adminHost: ADMIN, portalHost: PORTAL, adminEligible: true, portalEligible: true }
    expect(chooseSignedInSurface({ ...base, host: PORTAL })).toBe('portal')
    expect(chooseSignedInSurface({ ...base, host: ADMIN })).toBe('admin')
  })

  it('falls back to admin-first for a dual-eligible user on the apex/unknown host', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    expect(
      chooseSignedInSurface({
        host: 'smd.services',
        adminHost: ADMIN,
        portalHost: PORTAL,
        adminEligible: true,
        portalEligible: true,
      })
    ).toBe('admin')
  })

  it('keeps single-role behavior: admin-only → admin even on the portal host', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    expect(
      chooseSignedInSurface({
        host: PORTAL,
        adminHost: ADMIN,
        portalHost: PORTAL,
        adminEligible: true,
        portalEligible: false,
      })
    ).toBe('admin')
  })

  it('keeps single-role behavior: client-only → portal even on the admin host', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    expect(
      chooseSignedInSurface({
        host: ADMIN,
        adminHost: ADMIN,
        portalHost: PORTAL,
        adminEligible: false,
        portalEligible: true,
      })
    ).toBe('portal')
  })

  it('returns none when the user qualifies for neither surface', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    expect(
      chooseSignedInSurface({
        host: PORTAL,
        adminHost: ADMIN,
        portalHost: PORTAL,
        adminEligible: false,
        portalEligible: false,
      })
    ).toBe('none')
  })

  it('ignores host preference when base URLs are unconfigured (local dev)', async () => {
    const { chooseSignedInSurface } = await import('../src/lib/auth/after-sign-in-target')
    // No admin/portal host known → host can never match → admin-first default.
    expect(
      chooseSignedInSurface({
        host: 'localhost',
        adminHost: null,
        portalHost: null,
        adminEligible: true,
        portalEligible: true,
      })
    ).toBe('admin')
  })

  it('hostnameOf parses base URLs and tolerates missing/garbage input', async () => {
    const { hostnameOf } = await import('../src/lib/auth/after-sign-in-target')
    expect(hostnameOf('https://admin.smd.services')).toBe('admin.smd.services')
    expect(hostnameOf('https://portal.smd.services/portal')).toBe('portal.smd.services')
    expect(hostnameOf(null)).toBeNull()
    expect(hostnameOf(undefined)).toBeNull()
    expect(hostnameOf('')).toBeNull()
    expect(hostnameOf('not a url')).toBeNull()
  })

  it('dispatcher uses host-aware selection and fails safe to the admin-first default', () => {
    const source = readFileSync(resolve('src/pages/auth/after-sign-in.ts'), 'utf-8')
    expect(source).toContain('chooseSignedInSurface')
    // Fail-safe: the sole admin must never be stranded by a dispatcher throw.
    expect(source).toContain('try {')
    expect(source).toContain('catch')
    // Portal eligibility must be binding-based, not gated on users.role —
    // an admin bound to a customer entity is a legitimate portal seat.
    expect(source).toContain('userRow.entity_id || auth.orgId')
  })
})

describe('auth: portal products stay decoupled (no cross-surface links)', () => {
  // The admin console and the client portal are two separate products that
  // happen to share a login credential. Neither should link into the other
  // (Captain decision 2026-06-10 — "Amazon doesn't put a Costco button in its
  // header"). Each is navigated to and operated independently; the host-aware
  // dispatcher routes each product's own sign-in to that product.
  it('admin layout does not link into the client portal', () => {
    const source = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(source).not.toContain('buildPortalUrl')
    expect(source).not.toContain('Operator (client view)')
  })

  it('portal header does not link into the admin console', () => {
    const source = readFileSync(resolve('src/components/portal/PortalHeader.astro'), 'utf-8')
    expect(source).not.toContain('buildAdminUrl')
    expect(source).not.toContain('Admin console')
  })
})

describe('auth: admin dashboard', () => {
  it('admin index page exists', () => {
    expect(existsSync(resolve('src/pages/admin/index.astro'))).toBe(true)
  })

  it('admin page uses session data', () => {
    const source = readFileSync(resolve('src/pages/admin/index.astro'), 'utf-8')
    expect(source).toContain('Astro.locals.session')
  })

  it('admin layout includes Clerk sign-out button', () => {
    const source = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(source).toContain('SignOutButton')
    expect(source).toContain('/auth/sign-in?status=signed_out')
  })

  it('admin layout is not indexed by search engines', () => {
    const source = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(source).toContain('noindex')
  })
})

describe('auth: migrations (historical)', () => {
  it('password_hash migration still exists in history', () => {
    // We don't drop the migration even though the column is no longer
    // written to; D1 migrations are append-only and the schema column
    // still exists in the users table (NULL for the post-cutover admin
    // row). Removing the migration would diverge prod from local.
    expect(existsSync(resolve('migrations/0004_add_password_hash.sql'))).toBe(true)
  })

  it('admin seed migration still exists in history', () => {
    expect(existsSync(resolve('migrations/0005_seed_admin_user.sql'))).toBe(true)
  })

  it('sessions table migration still exists', () => {
    expect(existsSync(resolve('migrations/0006_create_sessions_table.sql'))).toBe(true)
  })
})

describe('auth: env.d.ts types', () => {
  it('declares AuthSession interface', () => {
    const source = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(source).toContain('interface AuthSession')
  })

  it('adds session to App.Locals', () => {
    const source = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(source).toContain('session: AuthSession | null')
  })
})
