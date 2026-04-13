import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  getAppBaseUrl,
  getPortalBaseUrl,
  requireAppBaseUrl,
  requirePortalBaseUrl,
  buildAppUrl,
  buildPortalUrl,
} from '../src/lib/config/app-url'

/**
 * Issue #173: Outbound auth and portal links must be built from a canonical
 * APP_BASE_URL env var, never from request-derived host/protocol values.
 *
 * These tests cover:
 *   1. The helper module behavior (parsing, normalization, strict guards).
 *   2. File-content guards proving the affected endpoints use the helper
 *      and no longer reach into `url.protocol` / `url.host` / `request.url`
 *      to construct outbound links.
 *   3. Env declarations and wrangler config document the new vars.
 */

describe('canonical app-url helper: getAppBaseUrl', () => {
  it('returns the trimmed value when set', () => {
    expect(getAppBaseUrl({ APP_BASE_URL: 'https://smd.services' })).toBe('https://smd.services')
  })

  it('strips trailing slashes', () => {
    expect(getAppBaseUrl({ APP_BASE_URL: 'https://smd.services/' })).toBe('https://smd.services')
    expect(getAppBaseUrl({ APP_BASE_URL: 'https://smd.services///' })).toBe('https://smd.services')
  })

  it('trims surrounding whitespace', () => {
    expect(getAppBaseUrl({ APP_BASE_URL: '  https://smd.services  ' })).toBe('https://smd.services')
  })

  it('returns null when env var is missing', () => {
    expect(getAppBaseUrl({})).toBeNull()
  })

  it('returns null when env var is blank', () => {
    expect(getAppBaseUrl({ APP_BASE_URL: '' })).toBeNull()
    expect(getAppBaseUrl({ APP_BASE_URL: '   ' })).toBeNull()
  })
})

describe('canonical app-url helper: getPortalBaseUrl', () => {
  it('returns PORTAL_BASE_URL when set', () => {
    expect(
      getPortalBaseUrl({
        APP_BASE_URL: 'https://smd.services',
        PORTAL_BASE_URL: 'https://portal.smd.services',
      })
    ).toBe('https://portal.smd.services')
  })

  it('falls back to APP_BASE_URL when PORTAL_BASE_URL is unset', () => {
    expect(getPortalBaseUrl({ APP_BASE_URL: 'https://smd.services' })).toBe('https://smd.services')
  })

  it('falls back to APP_BASE_URL when PORTAL_BASE_URL is blank', () => {
    expect(
      getPortalBaseUrl({
        APP_BASE_URL: 'https://smd.services',
        PORTAL_BASE_URL: '   ',
      })
    ).toBe('https://smd.services')
  })

  it('returns null when neither is set', () => {
    expect(getPortalBaseUrl({})).toBeNull()
  })
})

describe('canonical app-url helper: strict guards', () => {
  it('requireAppBaseUrl throws a descriptive error when missing', () => {
    expect(() => requireAppBaseUrl({})).toThrow(/APP_BASE_URL is not configured/)
  })

  it('requireAppBaseUrl returns the value when set', () => {
    expect(requireAppBaseUrl({ APP_BASE_URL: 'https://smd.services' })).toBe('https://smd.services')
  })

  it('requirePortalBaseUrl throws when neither var is set', () => {
    expect(() => requirePortalBaseUrl({})).toThrow(/PORTAL_BASE_URL/)
  })

  it('requirePortalBaseUrl honors APP_BASE_URL fallback', () => {
    expect(requirePortalBaseUrl({ APP_BASE_URL: 'https://smd.services' })).toBe(
      'https://smd.services'
    )
  })
})

describe('canonical app-url helper: URL builders', () => {
  const env = {
    APP_BASE_URL: 'https://smd.services',
    PORTAL_BASE_URL: 'https://portal.smd.services',
  }

  it('buildAppUrl prepends the base origin', () => {
    expect(buildAppUrl(env, '/api/webhooks/signwell')).toBe(
      'https://smd.services/api/webhooks/signwell'
    )
  })

  it('buildAppUrl handles paths without a leading slash', () => {
    expect(buildAppUrl(env, 'api/webhooks/signwell')).toBe(
      'https://smd.services/api/webhooks/signwell'
    )
  })

  it('buildPortalUrl defaults to /portal when no path is supplied', () => {
    expect(buildPortalUrl(env)).toBe('https://portal.smd.services/portal')
  })

  it('buildPortalUrl honors a custom path', () => {
    expect(buildPortalUrl(env, '/portal/invoices')).toBe(
      'https://portal.smd.services/portal/invoices'
    )
  })

  it('buildPortalUrl falls back to APP_BASE_URL when PORTAL_BASE_URL is unset', () => {
    expect(buildPortalUrl({ APP_BASE_URL: 'https://smd.services' }, '/portal')).toBe(
      'https://smd.services/portal'
    )
  })

  it('buildAppUrl throws when APP_BASE_URL is missing', () => {
    expect(() => buildAppUrl({}, '/foo')).toThrow(/APP_BASE_URL/)
  })

  it('buildPortalUrl throws when neither base URL is set', () => {
    expect(() => buildPortalUrl({}, '/portal')).toThrow(/PORTAL_BASE_URL/)
  })
})

describe('canonical app-url: magic-link endpoint uses canonical helper', () => {
  const path = resolve('src/pages/api/auth/magic-link.ts')

  it('imports requireAppBaseUrl from the config helper', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toMatch(/from ['"][^'"]*lib\/config\/app-url['"]/)
    expect(source).toContain('requireAppBaseUrl')
  })

  it('does not derive base URL from request host/protocol', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).not.toContain('url.protocol')
    expect(source).not.toContain('url.host')
    expect(source).not.toMatch(/new URL\([^)]*request\.url/)
  })

  it('still passes a canonical baseUrl into buildMagicLinkUrl', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toContain('buildMagicLinkUrl(baseUrl, token)')
  })
})

describe('canonical app-url: resend-invitation endpoint uses canonical helper', () => {
  const path = resolve('src/pages/api/admin/resend-invitation.ts')

  it('imports requireAppBaseUrl from the config helper', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toMatch(/from ['"][^'"]*lib\/config\/app-url['"]/)
    expect(source).toContain('requireAppBaseUrl')
  })

  it('does not derive base URL from request host/protocol', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).not.toContain('url.protocol')
    expect(source).not.toContain('url.host')
    expect(source).not.toMatch(/new URL\([^)]*request\.url/)
  })
})

describe('canonical app-url: follow-ups endpoint uses canonical helper', () => {
  const path = resolve('src/pages/api/admin/follow-ups/[id].ts')

  it('imports buildPortalUrl from the config helper', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toMatch(/from ['"][^'"]*lib\/config\/app-url['"]/)
    expect(source).toContain('buildPortalUrl')
  })

  it('does not derive base URL from request host/protocol', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).not.toContain('url.protocol')
    expect(source).not.toContain('url.host')
    expect(source).not.toMatch(/new URL\([^)]*request\.url/)
  })
})

describe('canonical app-url: invoices endpoint uses canonical helper', () => {
  const path = resolve('src/pages/api/admin/invoices/[id].ts')

  it('imports buildPortalUrl from the config helper', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toMatch(/from ['"][^'"]*lib\/config\/app-url['"]/)
    expect(source).toContain('buildPortalUrl')
  })

  it('does not derive portal URL from request.url', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).not.toMatch(/new URL\([^)]*request\.url/)
  })
})

describe('canonical app-url: signwell signature orchestration uses canonical helper', () => {
  const path = resolve('src/lib/sow/service.ts')

  it('imports buildAppUrl from the config helper', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).toMatch(/from ['"][^'"]*config\/app-url['"]/)
    expect(source).toContain('buildAppUrl')
  })

  it('does not derive callback URL from url.origin or request.url', () => {
    const source = readFileSync(path, 'utf-8')
    expect(source).not.toContain('url.origin')
    expect(source).not.toMatch(/new URL\([^)]*request\.url/)
  })
})

describe('canonical app-url: env declarations', () => {
  it('CfEnv declares APP_BASE_URL', () => {
    const source = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(source).toContain('APP_BASE_URL')
  })

  it('CfEnv declares PORTAL_BASE_URL', () => {
    const source = readFileSync(resolve('src/env.d.ts'), 'utf-8')
    expect(source).toContain('PORTAL_BASE_URL')
  })

  it('wrangler.toml declares APP_BASE_URL under [vars]', () => {
    const source = readFileSync(resolve('wrangler.toml'), 'utf-8')
    expect(source).toContain('[vars]')
    expect(source).toContain('APP_BASE_URL')
  })

  it('wrangler.toml declares PORTAL_BASE_URL', () => {
    const source = readFileSync(resolve('wrangler.toml'), 'utf-8')
    expect(source).toContain('PORTAL_BASE_URL')
  })

  it('.dev.vars.example documents APP_BASE_URL for local dev', () => {
    const source = readFileSync(resolve('.dev.vars.example'), 'utf-8')
    expect(source).toContain('APP_BASE_URL')
    expect(source).toContain('PORTAL_BASE_URL')
  })
})
