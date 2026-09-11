/**
 * Coverage for the Fly-secret OAuth token relay at `src/lib/oauth/store.ts`.
 * Verifies: the on-disk google-auth authorized-user JSON shape, the
 * set-secret → restart sequence against a mocked Fly API, the
 * missing-refresh-token / unknown-customer / unavailable rejections, and that
 * no token material is ever logged.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import { createFlySecretTokenStore } from '../src/lib/oauth/store'
import type { ProviderTokenResponse } from '../src/lib/oauth/providers'

function clearEnv(): void {
  for (const key of Object.keys(testEnv)) {
    delete (testEnv as unknown as Record<string, unknown>)[key]
  }
}

const TOKEN: ProviderTokenResponse = {
  access_token: 'ACCESS-TOK',
  refresh_token: 'REFRESH-TOK',
  scopes: 'openid email https://www.googleapis.com/auth/gmail.modify',
  expires_at: '2026-06-02T04:57:53.000Z',
  obtained_at: '2026-06-02T03:57:53.000Z',
}

function input(
  overrides: Partial<Parameters<ReturnType<typeof createFlySecretTokenStore>['store']>[0]> = {}
) {
  return {
    customer_id: 'smd-staging',
    provider: 'google-workspace',
    reviewer_id: 'user_1',
    token: TOKEN,
    ...overrides,
  }
}

describe('createFlySecretTokenStore', () => {
  const ORIGINAL_FETCH = globalThis.fetch

  beforeEach(() => {
    Object.assign(testEnv, {
      FLY_API_TOKEN: 'fly-tok',
      GOOGLE_CLIENT_ID: 'gid',
      GOOGLE_CLIENT_SECRET: 'gsecret',
    })
  })
  afterEach(() => {
    clearEnv()
    globalThis.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  function mockFly(): { setSecretsBody: () => string; restarts: string[] } {
    let captured = ''
    const restarts: string[] = []
    globalThis.fetch = vi.fn(async (i: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof i === 'string' ? i : i.toString()
      if (url.includes('api.fly.io/graphql')) {
        captured = (init?.body as string) ?? ''
        return new Response(
          JSON.stringify({ data: { setSecrets: { release: { id: 'r', version: 2 } } } }),
          {
            status: 200,
          }
        )
      }
      if (url.endsWith('/restart')) {
        restarts.push(url)
        return new Response('{}', { status: 200 })
      }
      if (url.includes('/machines')) {
        return new Response(JSON.stringify([{ id: 'm1' }]), { status: 200 })
      }
      return new Response('not found', { status: 404 })
    })
    return { setSecretsBody: () => captured, restarts }
  }

  it('relays the google-auth authorized-user JSON and restarts the machine', async () => {
    const fly = mockFly()
    const result = await createFlySecretTokenStore().store(input())
    expect(result).toEqual({ ok: true })

    const body = JSON.parse(fly.setSecretsBody())
    expect(body.variables.input.appId).toBe('hermes-smd-staging')
    const secret = body.variables.input.secrets[0]
    expect(secret.key).toBe('GOOGLE_TOKEN_JSON')
    const onDisk = JSON.parse(atob(secret.value))
    expect(onDisk).toEqual({
      token: 'ACCESS-TOK',
      refresh_token: 'REFRESH-TOK',
      token_uri: 'https://oauth2.googleapis.com/token',
      client_id: 'gid',
      client_secret: 'gsecret',
      scopes: ['openid', 'email', 'https://www.googleapis.com/auth/gmail.modify'],
      universe_domain: 'googleapis.com',
      account: '',
      expiry: '2026-06-02T04:57:53.000Z',
    })
    expect(fly.restarts).toEqual([
      'https://api.machines.dev/v1/apps/hermes-smd-staging/machines/m1/restart',
    ])
  })

  it('never logs token material', async () => {
    mockFly()
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await createFlySecretTokenStore().store(input())
    const allArgs = [...errSpy.mock.calls, ...warnSpy.mock.calls].flat().join(' ')
    expect(allArgs).not.toContain('ACCESS-TOK')
    expect(allArgs).not.toContain('REFRESH-TOK')
  })

  it('rejects a token with no refresh_token without calling Fly', async () => {
    const spy = vi.fn()
    globalThis.fetch = spy
    const result = await createFlySecretTokenStore().store(
      input({ token: { ...TOKEN, refresh_token: null } })
    )
    expect(result).toEqual({ ok: false, reason: 'missing_refresh_token' })
    expect(spy).not.toHaveBeenCalled()
  })

  it('rejects an unknown customer without targeting any app', async () => {
    const spy = vi.fn()
    globalThis.fetch = spy
    const result = await createFlySecretTokenStore().store(input({ customer_id: 'not-a-customer' }))
    expect(result).toEqual({ ok: false, reason: 'unknown_customer' })
    expect(spy).not.toHaveBeenCalled()
  })

  it('returns unavailable when FLY_API_TOKEN is missing', async () => {
    delete (testEnv as unknown as Record<string, unknown>).FLY_API_TOKEN
    const result = await createFlySecretTokenStore().store(input())
    expect(result).toEqual({ ok: false, reason: 'unavailable' })
  })

  it('returns unavailable when setSecrets fails', async () => {
    globalThis.fetch = vi.fn(async (i: RequestInfo | URL) => {
      const url = typeof i === 'string' ? i : i.toString()
      if (url.includes('api.fly.io/graphql')) return new Response('err', { status: 500 })
      return new Response('{}', { status: 200 })
    })
    const result = await createFlySecretTokenStore().store(input())
    expect(result).toEqual({ ok: false, reason: 'unavailable' })
  })
})
