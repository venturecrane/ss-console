import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('auth: password module', () => {
  it('password.ts exports hashPassword and verifyPassword', () => {
    const source = readFileSync(resolve('src/lib/auth/password.ts'), 'utf-8')
    expect(source).toContain('export async function hashPassword')
    expect(source).toContain('export async function verifyPassword')
  })

  it('uses PBKDF2 with Web Crypto API (Workers-compatible)', () => {
    const source = readFileSync(resolve('src/lib/auth/password.ts'), 'utf-8')
    expect(source).toContain('PBKDF2')
    expect(source).toContain('crypto.subtle')
    // Must NOT import bcrypt or argon2 as dependencies
    expect(source).not.toMatch(/import.*bcrypt/)
    expect(source).not.toMatch(/import.*argon2/)
    expect(source).not.toMatch(/require.*bcrypt/)
    expect(source).not.toMatch(/require.*argon2/)
  })

  it('uses constant-time comparison for hash verification', () => {
    const source = readFileSync(resolve('src/lib/auth/password.ts'), 'utf-8')
    // XOR-based constant-time compare pattern
    expect(source).toContain('diff |=')
  })

  it('uses sufficient iterations (>= 100000)', () => {
    const source = readFileSync(resolve('src/lib/auth/password.ts'), 'utf-8')
    expect(source).toContain('100_000')
  })
})

describe('auth: session module', () => {
  it('session.ts exports core session functions', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    expect(source).toContain('export async function createSession')
    expect(source).toContain('export async function validateSession')
    expect(source).toContain('export async function destroySession')
    expect(source).toContain('export async function renewSession')
  })

  it('session duration is 7 days', () => {
    const source = readFileSync(resolve('src/lib/auth/session.ts'), 'utf-8')
    // 7 * 24 * 60 * 60 * 1000
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
    // KV fast path mentioned in validateSession
    expect(source).toContain('kv.get')
    // D1 fallback
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

describe('auth: middleware', () => {
  it('middleware.ts exists', () => {
    expect(existsSync(resolve('src/middleware.ts'))).toBe(true)
  })

  it('protects /admin routes', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('/admin')
    expect(source).toContain("pathname.startsWith('/admin')")
  })

  it('redirects unauthenticated requests to login', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("redirect('/auth/login')")
  })

  it('attaches session to locals', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('context.locals.session = sessionData')
  })

  it('renews session on each authenticated request', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('renewSession')
  })

  it('refreshes session cookie on authenticated response', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('buildSessionCookie')
  })
})

describe('auth: login page', () => {
  it('login page exists', () => {
    expect(existsSync(resolve('src/pages/auth/login.astro'))).toBe(true)
  })

  it('login form posts to /api/auth/login', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('method="POST"')
    expect(source).toContain('action="/api/auth/login"')
  })

  it('includes email and password fields', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('name="email"')
    expect(source).toContain('name="password"')
    expect(source).toContain('type="email"')
    expect(source).toContain('type="password"')
  })

  it('displays error messages from query params', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('error')
    expect(source).toContain('Invalid email or password')
  })

  it('is branded with SMD Services', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('SMD Services')
  })

  it('is not indexed by search engines', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('noindex')
  })
})

describe('auth: API endpoints', () => {
  it('login endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/auth/login.ts'))).toBe(true)
  })

  it('logout endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/auth/logout.ts'))).toBe(true)
  })

  it('login endpoint validates credentials and creates session', () => {
    const source = readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')
    expect(source).toContain('verifyPassword')
    expect(source).toContain('createSession')
    expect(source).toContain('buildSessionCookie')
  })

  it('logout endpoint destroys session and clears cookie', () => {
    const source = readFileSync(resolve('src/pages/api/auth/logout.ts'), 'utf-8')
    expect(source).toContain('destroySession')
    expect(source).toContain('buildClearSessionCookie')
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

  it('admin layout includes logout form', () => {
    const source = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(source).toContain('/api/auth/logout')
  })

  it('admin layout is not indexed by search engines', () => {
    const source = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(source).toContain('noindex')
  })
})

describe('auth: migrations', () => {
  it('password_hash migration exists', () => {
    expect(existsSync(resolve('migrations/0004_add_password_hash.sql'))).toBe(true)
  })

  it('password_hash migration adds column to users table', () => {
    const sql = readFileSync(resolve('migrations/0004_add_password_hash.sql'), 'utf-8')
    expect(sql).toContain('ALTER TABLE users')
    expect(sql).toContain('password_hash')
  })

  it('admin seed migration exists', () => {
    expect(existsSync(resolve('migrations/0005_seed_admin_user.sql'))).toBe(true)
  })

  it('admin seed creates an admin role user', () => {
    const sql = readFileSync(resolve('migrations/0005_seed_admin_user.sql'), 'utf-8')
    expect(sql).toContain("'admin'")
    expect(sql).toContain('INSERT INTO users')
  })

  it('sessions table migration exists', () => {
    expect(existsSync(resolve('migrations/0006_create_sessions_table.sql'))).toBe(true)
  })

  it('sessions table has required columns', () => {
    const sql = readFileSync(resolve('migrations/0006_create_sessions_table.sql'), 'utf-8')
    expect(sql).toContain('CREATE TABLE sessions')
    expect(sql).toContain('token')
    expect(sql).toContain('user_id')
    expect(sql).toContain('org_id')
    expect(sql).toContain('expires_at')
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

describe('auth: login endpoint rate limiting', () => {
  it('login endpoint imports rateLimitByIp', () => {
    const source = readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')
    expect(source).toContain('rateLimitByIp')
    expect(source).toContain('rate-limit')
  })

  it('login endpoint rate limit uses auth-login bucket', () => {
    const source = readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')
    expect(source).toContain('auth-login')
  })

  it('login endpoint redirects to rate_limited error on block', () => {
    const source = readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')
    expect(source).toContain('rate_limited')
  })

  it('login page has rate_limited error message', () => {
    const source = readFileSync(resolve('src/pages/auth/login.astro'), 'utf-8')
    expect(source).toContain('rate_limited')
    expect(source).toContain('Too many sign-in attempts')
  })

  it('rate limit check occurs before DB lookup', () => {
    const source = readFileSync(resolve('src/pages/api/auth/login.ts'), 'utf-8')
    const rateLimitIdx = source.indexOf('rateLimitByIp')
    const dbLookupIdx = source.indexOf('SELECT * FROM users')
    expect(rateLimitIdx).toBeGreaterThan(-1)
    expect(dbLookupIdx).toBeGreaterThan(-1)
    expect(rateLimitIdx).toBeLessThan(dbLookupIdx)
  })
})
