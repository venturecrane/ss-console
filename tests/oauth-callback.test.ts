import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import {
  issueOAuthState,
  verifyOAuthState,
  DEFAULT_STATE_TTL_SECONDS,
} from '../src/lib/oauth/state'
import { getOAuthProvider } from '../src/lib/oauth/providers'
import { GET as oauthCallback } from '../src/pages/api/oauth/callback'

const SIGNING_KEY_B64 = 'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE='
const ALT_SIGNING_KEY_B64 = 'YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI='

const ADMIN_BASE = 'https://admin.smd.services'

function clearEnv(): void {
  for (const key of Object.keys(testEnv)) {
    delete (testEnv as unknown as Record<string, unknown>)[key]
  }
}

function applyDefaultEnv(): void {
  Object.assign(testEnv, {
    ADMIN_BASE_URL: ADMIN_BASE,
    OAUTH_STATE_SIGNING_KEY: SIGNING_KEY_B64,
    GOOGLE_CLIENT_ID: 'google-client-id',
    GOOGLE_CLIENT_SECRET: 'google-client-secret',
    MICROSOFT_GRAPH_CLIENT_ID: 'msgraph-client-id',
    MICROSOFT_GRAPH_CLIENT_SECRET: 'msgraph-client-secret',
  })
}

function redirect(url: string, status?: number): Response {
  return new Response(null, {
    status: status ?? 302,
    headers: { Location: url },
  })
}

async function invoke(opts: {
  url: string
  session?: { userId: string; role: string } | null
}): Promise<Response> {
  return await oauthCallback({
    request: new Request(opts.url),
    redirect,
    locals: {
      session: opts.session ?? null,
    },
  } as unknown as Parameters<typeof oauthCallback>[0])
}

function parseRedirect(response: Response): URL {
  const location = response.headers.get('Location')
  if (!location) throw new Error('redirect response missing Location header')
  return new URL(location)
}

describe('oauth/state', () => {
  beforeEach(() => {
    applyDefaultEnv()
  })
  afterEach(() => {
    clearEnv()
  })

  it('round-trips a valid state', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const result = await verifyOAuthState(state)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.payload.customer_id).toBe('acme')
      expect(result.payload.provider).toBe('google-workspace')
      expect(result.payload.reviewer_id).toBe('user-1')
      expect(result.payload.nonce).toMatch(/^[0-9a-f-]{36}$/)
      expect(result.payload.exp).toBeGreaterThan(Math.floor(Date.now() / 1000))
    }
  })

  it('rejects a tampered payload (bad signature)', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const [payloadB64, sigB64] = state.split('.')
    const tampered = `${payloadB64}A.${sigB64}`
    const result = await verifyOAuthState(tampered)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      // Tampering the payload changes the input to HMAC verify; signature
      // no longer matches.
      expect(result.error).toBe('bad_signature')
    }
  })

  it('rejects a state signed with a different key', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    // Rotate the signing key. The previously-issued state should now
    // fail signature verification.
    Object.assign(testEnv, { OAUTH_STATE_SIGNING_KEY: ALT_SIGNING_KEY_B64 })
    const result = await verifyOAuthState(state)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toBe('bad_signature')
  })

  it('rejects an expired state', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
      ttl_seconds: 1,
    })
    // Advance wall clock past expiry.
    vi.useFakeTimers()
    vi.setSystemTime(Date.now() + 2_000)
    try {
      const result = await verifyOAuthState(state)
      expect(result.ok).toBe(false)
      if (!result.ok) expect(result.error).toBe('expired')
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects a malformed state', async () => {
    const result = await verifyOAuthState('not-a-state')
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toBe('malformed')
  })

  it('defaults to a 10-minute TTL', () => {
    expect(DEFAULT_STATE_TTL_SECONDS).toBe(600)
  })

  it('refuses to issue a state when the signing key is missing', async () => {
    delete (testEnv as unknown as Record<string, unknown>).OAUTH_STATE_SIGNING_KEY
    await expect(
      issueOAuthState({
        customer_id: 'acme',
        provider: 'google-workspace',
        reviewer_id: 'user-1',
      })
    ).rejects.toThrow(/OAUTH_STATE_SIGNING_KEY/)
  })
})

describe('oauth/providers registry', () => {
  it('returns null for an unknown slug', () => {
    expect(getOAuthProvider('not-a-provider')).toBeNull()
  })

  it('points microsoft-graph at the v2 login endpoint', () => {
    const provider = getOAuthProvider('microsoft-graph')
    expect(provider).not.toBeNull()
    expect(provider?.token_url).toBe('https://login.microsoftonline.com/common/oauth2/v2.0/token')
  })

  it('points google-workspace at the oauth2.googleapis.com token endpoint', () => {
    const provider = getOAuthProvider('google-workspace')
    expect(provider).not.toBeNull()
    expect(provider?.token_url).toBe('https://oauth2.googleapis.com/token')
  })
})

describe('oauth/callback endpoint', () => {
  const ORIGINAL_FETCH = globalThis.fetch

  beforeEach(() => {
    applyDefaultEnv()
  })

  afterEach(() => {
    clearEnv()
    globalThis.fetch = ORIGINAL_FETCH
    vi.restoreAllMocks()
  })

  it('redirects with provider_error reason when the provider returns an error', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?error=access_denied&error_description=user+declined&state=${encodeURIComponent(state)}`

    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    expect(response.status).toBe(302)
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('failed')
    expect(location.searchParams.get('reason')).toBe('provider_error')
    expect(location.searchParams.get('provider')).toBe('google-workspace')
    expect(location.pathname).toBe('/admin/customers/acme/connectors')
  })

  it('redirects with missing_params when code or state is absent', async () => {
    const response = await invoke({
      url: `${ADMIN_BASE}/api/oauth/callback`,
      session: { userId: 'user-1', role: 'admin' },
    })
    expect(response.status).toBe(302)
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('failed')
    expect(location.searchParams.get('reason')).toBe('missing_params')
  })

  it('redirects with bad_state when the state signature does not verify', async () => {
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=bogus`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    expect(response.status).toBe(302)
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('failed')
    expect(location.searchParams.get('reason')).toBe('bad_state')
  })

  it('redirects with expired_state when the state has expired', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
      ttl_seconds: 1,
    })
    vi.useFakeTimers()
    vi.setSystemTime(Date.now() + 2_000)
    try {
      const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
      const response = await invoke({
        url,
        session: { userId: 'user-1', role: 'admin' },
      })
      expect(response.status).toBe(302)
      const location = parseRedirect(response)
      expect(location.searchParams.get('reason')).toBe('expired_state')
    } finally {
      vi.useRealTimers()
    }
  })

  it('redirects with reviewer_mismatch when the session reviewer does not match the state', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-2', role: 'admin' },
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('reviewer_mismatch')
  })

  it('redirects with reviewer_mismatch when no session is present', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: null,
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('reviewer_mismatch')
  })

  it('redirects with reviewer_mismatch when the session role is not admin', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'client' },
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('reviewer_mismatch')
  })

  it('redirects with unknown_provider when the state targets an unregistered provider', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'not-a-real-provider',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('unknown_provider')
  })

  it('redirects with exchange_failed when the provider rejects the code', async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: 'invalid_grant' }), { status: 400 })
    )

    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('exchange_failed')
  })

  it('redirects to the success page when state, reviewer, and exchange all pass', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: 'access-xxx',
            refresh_token: 'refresh-yyy',
            scope: 'https://www.googleapis.com/auth/gmail.readonly',
            expires_in: 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
    )

    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    expect(response.status).toBe(302)
    const location = parseRedirect(response)
    expect(location.pathname).toBe('/admin/customers/acme/connectors')
    expect(location.searchParams.get('status')).toBe('connected')
    expect(location.searchParams.get('provider')).toBe('google-workspace')
  })

  it('dispatches to microsoft-graph when the state provider is microsoft-graph', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            access_token: 'access-ms',
            refresh_token: 'refresh-ms',
            scope: 'Mail.Read',
            expires_in: 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
    )
    globalThis.fetch = fetchMock

    const state = await issueOAuthState({
      customer_id: 'acme',
      provider: 'microsoft-graph',
      reviewer_id: 'user-1',
    })
    const url = `${ADMIN_BASE}/api/oauth/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      session: { userId: 'user-1', role: 'admin' },
    })
    expect(response.status).toBe(302)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const firstCallArgs = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(firstCallArgs[0]).toBe('https://login.microsoftonline.com/common/oauth2/v2.0/token')
  })
})

describe('oauth/callback source assertions', () => {
  it('endpoint file does not log token values', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(resolve('src/pages/api/oauth/callback.ts'), 'utf-8')
    // No access_token / refresh_token references inside console.log/error
    // calls — token material must never reach logs.
    const logLines = source.split('\n').filter((l) => l.includes('console.'))
    for (const line of logLines) {
      expect(line).not.toMatch(/access_token|refresh_token/)
    }
  })

  it('audit writer leaves a TODO for #891', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(resolve('src/lib/oauth/audit.ts'), 'utf-8')
    expect(source).toMatch(/TODO.*#891/)
  })

  it('store interface documents ADR 0010 and ships a no-op v1', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    const source = readFileSync(resolve('src/lib/oauth/store.ts'), 'utf-8')
    expect(source).toContain('ADR 0010')
    expect(source).toContain('createNoOpTokenStore')
  })
})
