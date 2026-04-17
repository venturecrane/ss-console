import { describe, it, expect } from 'vitest'
import { resolveTurnstileConfig } from '../src/lib/booking/turnstile'

/**
 * #12: Turnstile config must be both-or-neither, and "neither" is only
 * valid on localhost. These tests lock in the fail-fast behavior so the
 * silent-bypass state (prod with keys unset) cannot return.
 */

describe('resolveTurnstileConfig', () => {
  it('returns enabled when both keys are set (prod)', () => {
    const config = resolveTurnstileConfig({
      PUBLIC_TURNSTILE_SITE_KEY: '0x1234',
      TURNSTILE_SECRET_KEY: 'secret-abc',
      APP_BASE_URL: 'https://smd.services',
    })
    expect(config).toEqual({ mode: 'enabled', siteKey: '0x1234', secretKey: 'secret-abc' })
  })

  it('returns enabled when both keys are set on localhost', () => {
    const config = resolveTurnstileConfig({
      PUBLIC_TURNSTILE_SITE_KEY: '0xTEST',
      TURNSTILE_SECRET_KEY: 'secret-test',
      APP_BASE_URL: 'http://localhost:4321',
    })
    expect(config.mode).toBe('enabled')
  })

  it('returns disabled when both keys are unset on localhost', () => {
    const config = resolveTurnstileConfig({
      APP_BASE_URL: 'http://localhost:4321',
    })
    expect(config).toEqual({ mode: 'disabled', siteKey: '' })
  })

  it('returns disabled when both keys are empty strings on localhost', () => {
    const config = resolveTurnstileConfig({
      PUBLIC_TURNSTILE_SITE_KEY: '',
      TURNSTILE_SECRET_KEY: '',
      APP_BASE_URL: 'http://127.0.0.1:4321',
    })
    expect(config).toEqual({ mode: 'disabled', siteKey: '' })
  })

  it('treats whitespace-only keys as unset', () => {
    const config = resolveTurnstileConfig({
      PUBLIC_TURNSTILE_SITE_KEY: '   ',
      TURNSTILE_SECRET_KEY: '\t',
      APP_BASE_URL: 'http://localhost',
    })
    expect(config.mode).toBe('disabled')
  })

  it('throws when only the site key is set (prod silent-bypass state)', () => {
    expect(() =>
      resolveTurnstileConfig({
        PUBLIC_TURNSTILE_SITE_KEY: '0x1234',
        APP_BASE_URL: 'https://smd.services',
      })
    ).toThrow(/misconfigured/i)
  })

  it('throws when only the secret key is set (broken-form state)', () => {
    expect(() =>
      resolveTurnstileConfig({
        TURNSTILE_SECRET_KEY: 'secret-abc',
        APP_BASE_URL: 'https://smd.services',
      })
    ).toThrow(/misconfigured/i)
  })

  it('throws when both keys are unset in a non-localhost environment', () => {
    expect(() =>
      resolveTurnstileConfig({
        APP_BASE_URL: 'https://smd.services',
      })
    ).toThrow(/misconfigured/i)
  })

  it('throws when both keys are empty on a prod-like origin', () => {
    expect(() =>
      resolveTurnstileConfig({
        PUBLIC_TURNSTILE_SITE_KEY: '',
        TURNSTILE_SECRET_KEY: '',
        APP_BASE_URL: 'https://smd.services',
      })
    ).toThrow(/misconfigured/i)
  })

  it('throws on only-secret-set mismatch even on localhost (config is wrong)', () => {
    // A half-set config indicates operator error regardless of environment.
    // The localhost exemption only applies when BOTH keys are unset.
    expect(() =>
      resolveTurnstileConfig({
        TURNSTILE_SECRET_KEY: 'secret-abc',
        APP_BASE_URL: 'http://localhost:4321',
      })
    ).toThrow(/misconfigured/i)
  })

  it('error message names both env vars and the current APP_BASE_URL', () => {
    try {
      resolveTurnstileConfig({
        PUBLIC_TURNSTILE_SITE_KEY: '',
        TURNSTILE_SECRET_KEY: '',
        APP_BASE_URL: 'https://smd.services',
      })
      throw new Error('expected throw')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      expect(msg).toContain('PUBLIC_TURNSTILE_SITE_KEY')
      expect(msg).toContain('TURNSTILE_SECRET_KEY')
      expect(msg).toContain('https://smd.services')
    }
  })

  it('does not treat 127.0.0.1 with a port as prod', () => {
    const config = resolveTurnstileConfig({
      APP_BASE_URL: 'http://127.0.0.1:8788/',
    })
    expect(config.mode).toBe('disabled')
  })

  it('does not treat a domain containing "localhost" as localhost (avoids bypass)', () => {
    // A deliberately crafted domain like "localhost.example.com" must NOT
    // trigger the localhost exemption.
    expect(() =>
      resolveTurnstileConfig({
        APP_BASE_URL: 'https://localhost.example.com',
      })
    ).toThrow(/misconfigured/i)
  })
})
