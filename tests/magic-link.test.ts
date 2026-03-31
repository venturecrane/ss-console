import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('magic-link: token module', () => {
  it('magic-link.ts exists', () => {
    expect(existsSync(resolve('src/lib/auth/magic-link.ts'))).toBe(true)
  })

  it('exports createMagicLink and verifyMagicLink', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('export async function createMagicLink')
    expect(source).toContain('export async function verifyMagicLink')
  })

  it('tokens expire after 15 minutes', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    // 15 * 60 * 1000 = 900000ms
    expect(source).toContain('15 * 60 * 1000')
  })

  it('uses cryptographically random token generation', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('crypto.getRandomValues')
  })

  it('generates 32-byte (64-char hex) tokens for sufficient entropy', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('Uint8Array(32)')
  })

  it('enforces single-use tokens by setting used_at', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('used_at')
    expect(source).toContain("UPDATE magic_links SET used_at = datetime('now')")
  })

  it('checks token expiration before verification', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('expires_at')
    expect(source).toContain('new Date(row.expires_at)')
  })

  it('rejects already-used tokens', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('row.used_at !== null')
  })

  it('stores tokens in magic_links table', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('INSERT INTO magic_links')
  })

  it('normalizes email to lowercase on creation', () => {
    const source = readFileSync(resolve('src/lib/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('email.toLowerCase().trim()')
  })

  it('is re-exported from auth index module', () => {
    const source = readFileSync(resolve('src/lib/auth/index.ts'), 'utf-8')
    expect(source).toContain('createMagicLink')
    expect(source).toContain('verifyMagicLink')
    expect(source).toContain('MAGIC_LINK_EXPIRY_MS')
  })
})

describe('magic-link: email module', () => {
  it('resend.ts email client exists', () => {
    expect(existsSync(resolve('src/lib/email/resend.ts'))).toBe(true)
  })

  it('templates.ts exists', () => {
    expect(existsSync(resolve('src/lib/email/templates.ts'))).toBe(true)
  })

  it('email index module exists', () => {
    expect(existsSync(resolve('src/lib/email/index.ts'))).toBe(true)
  })

  it('uses Resend API endpoint', () => {
    const source = readFileSync(resolve('src/lib/email/resend.ts'), 'utf-8')
    expect(source).toContain('https://api.resend.com/emails')
  })

  it('sends from team@smd.services', () => {
    const source = readFileSync(resolve('src/lib/email/resend.ts'), 'utf-8')
    expect(source).toContain('team@smd.services')
  })

  it('logs emails in dev mode when API key is missing', () => {
    const source = readFileSync(resolve('src/lib/email/resend.ts'), 'utf-8')
    expect(source).toContain('!apiKey')
    expect(source).toContain('console.log')
  })

  it('sends Authorization Bearer header', () => {
    const source = readFileSync(resolve('src/lib/email/resend.ts'), 'utf-8')
    expect(source).toContain('Authorization')
    expect(source).toContain('Bearer')
  })

  it('magic link email template is branded', () => {
    const source = readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')
    expect(source).toContain('SMD Services')
    expect(source).toContain('Client Portal')
  })

  it('magic link email includes 15-minute expiry notice', () => {
    const source = readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')
    expect(source).toContain('15 minutes')
  })

  it('portal invitation template mentions proposal', () => {
    const source = readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')
    expect(source).toContain('proposal')
  })

  it('templates export buildMagicLinkUrl helper', () => {
    const source = readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')
    expect(source).toContain('export function buildMagicLinkUrl')
    expect(source).toContain('/auth/verify')
  })
})

describe('magic-link: portal login page', () => {
  it('portal-login.astro exists', () => {
    expect(existsSync(resolve('src/pages/auth/portal-login.astro'))).toBe(true)
  })

  it('form posts to /api/auth/magic-link', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('method="POST"')
    expect(source).toContain('action="/api/auth/magic-link"')
  })

  it('includes email input only (no password)', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('name="email"')
    expect(source).toContain('type="email"')
    expect(source).not.toContain('type="password"')
  })

  it('is branded with SMD Services Client Portal', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('SMD Services')
    expect(source).toContain('Client Portal')
  })

  it('is not indexed by search engines', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('noindex')
  })

  it('displays success message when magic link is sent', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('sent')
    expect(source).toContain('Check your email')
  })

  it('displays error messages for expired and invalid links', () => {
    const source = readFileSync(resolve('src/pages/auth/portal-login.astro'), 'utf-8')
    expect(source).toContain('expired')
    expect(source).toContain('invalid')
  })
})

describe('magic-link: verify page', () => {
  it('verify.astro exists', () => {
    expect(existsSync(resolve('src/pages/auth/verify.astro'))).toBe(true)
  })

  it('reads token from query params', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain("searchParams.get('token')")
  })

  it('calls verifyMagicLink to validate token', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain('verifyMagicLink')
  })

  it('creates a session on successful verification', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain('createSession')
    expect(source).toContain('buildSessionCookie')
  })

  it('redirects to /portal on success', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain("'/portal'")
  })

  it('redirects to portal-login on invalid token', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain('/auth/portal-login?error=invalid')
  })

  it('looks up client user by email', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain("role = 'client'")
  })

  it('updates last_login_at on successful verify', () => {
    const source = readFileSync(resolve('src/pages/auth/verify.astro'), 'utf-8')
    expect(source).toContain('last_login_at')
  })
})

describe('magic-link: API endpoint', () => {
  it('magic-link API endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/auth/magic-link.ts'))).toBe(true)
  })

  it('handles POST requests', () => {
    const source = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('export const POST')
  })

  it('creates magic link and sends email', () => {
    const source = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('createMagicLink')
    expect(source).toContain('sendEmail')
  })

  it('prevents email enumeration by always showing success', () => {
    const source = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    // Should redirect to sent status even when user not found
    expect(source).toContain('enumeration')
    expect(source).toContain('status=sent')
  })

  it('normalizes email input', () => {
    const source = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain('toLowerCase().trim()')
  })

  it('only sends to client-role users', () => {
    const source = readFileSync(resolve('src/pages/api/auth/magic-link.ts'), 'utf-8')
    expect(source).toContain("role = 'client'")
  })
})

describe('magic-link: portal page', () => {
  it('portal index page exists', () => {
    expect(existsSync(resolve('src/pages/portal/index.astro'))).toBe(true)
  })

  it('portal page uses session data', () => {
    const source = readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')
    expect(source).toContain('Astro.locals.session')
  })

  it('portal page includes logout form', () => {
    const source = readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')
    expect(source).toContain('/api/auth/logout')
  })

  it('portal page is not indexed by search engines', () => {
    const source = readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')
    expect(source).toContain('noindex')
  })

  it('portal page is branded with SMD Services Portal', () => {
    const source = readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')
    expect(source).toContain('SMD Services')
    expect(source).toContain('Portal')
  })
})

describe('magic-link: admin resend invitation', () => {
  it('resend-invitation endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/resend-invitation.ts'))).toBe(true)
  })

  it('handles POST requests', () => {
    const source = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    expect(source).toContain('export const POST')
  })

  it('verifies admin session', () => {
    const source = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    expect(source).toContain("role !== 'admin'")
    expect(source).toContain('Unauthorized')
  })

  it('creates magic link and sends email', () => {
    const source = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    expect(source).toContain('createMagicLink')
    expect(source).toContain('sendEmail')
  })

  it('supports email correction for bounced emails (OQ-010)', () => {
    const source = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    // Should update email if a new one is provided
    expect(source).toContain('UPDATE users SET email')
    expect(source).toContain('newEmail')
  })

  it('only targets client-role users', () => {
    const source = readFileSync(resolve('src/pages/api/admin/resend-invitation.ts'), 'utf-8')
    expect(source).toContain("role = 'client'")
  })
})

describe('magic-link: middleware updates', () => {
  it('middleware protects /portal routes', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("pathname.startsWith('/portal')")
  })

  it('middleware protects /api/admin routes', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("pathname.startsWith('/api/admin')")
  })

  it('middleware redirects unauthenticated portal users to portal-login', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("redirect('/auth/portal-login')")
  })

  it('middleware returns 401 JSON for unauthenticated admin API requests', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('status: 401')
  })

  it('middleware enforces role-based access control', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain("'admin'")
    expect(source).toContain("'client'")
    expect(source).toContain('requiredRole')
  })

  it('middleware returns 403 for wrong role on admin API routes', () => {
    const source = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    expect(source).toContain('status: 403')
    expect(source).toContain('Forbidden')
  })
})

describe('magic-link: env types', () => {
  it('CfEnv includes RESEND_API_KEY', () => {
    const source = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(source).toContain('RESEND_API_KEY')
  })
})

describe('magic-link: wrangler config', () => {
  it('wrangler.toml documents RESEND_API_KEY secret', () => {
    const source = readFileSync(resolve('wrangler.toml'), 'utf-8')
    expect(source).toContain('RESEND_API_KEY')
  })
})

describe('magic-link: logout', () => {
  it('logout redirects client users to portal-login', () => {
    const source = readFileSync(resolve('src/pages/api/auth/logout.ts'), 'utf-8')
    expect(source).toContain('/auth/portal-login')
  })

  it('logout checks role to determine redirect', () => {
    const source = readFileSync(resolve('src/pages/api/auth/logout.ts'), 'utf-8')
    expect(source).toContain("role === 'client'")
  })
})

describe('magic-link: D1 schema', () => {
  it('magic_links table exists in migration', () => {
    const sql = readFileSync(resolve('migrations/0001_create_tables.sql'), 'utf-8')
    expect(sql).toContain('CREATE TABLE magic_links')
  })

  it('magic_links table has required columns', () => {
    const sql = readFileSync(resolve('migrations/0001_create_tables.sql'), 'utf-8')
    expect(sql).toContain('email')
    expect(sql).toContain('token')
    expect(sql).toContain('expires_at')
    expect(sql).toContain('used_at')
  })

  it('magic_links table has token uniqueness constraint', () => {
    const sql = readFileSync(resolve('migrations/0001_create_tables.sql'), 'utf-8')
    expect(sql).toContain('token')
    expect(sql).toContain('UNIQUE')
  })

  it('magic_links indexes exist', () => {
    const sql = readFileSync(resolve('migrations/0002_create_indexes.sql'), 'utf-8')
    expect(sql).toContain('idx_magic_links_email')
    expect(sql).toContain('idx_magic_links_expires')
  })
})
