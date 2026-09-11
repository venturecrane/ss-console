import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import {
  issueOAuthState,
  verifyOAuthState,
  DEFAULT_STATE_TTL_SECONDS,
} from '../src/lib/oauth/state'
import { getOAuthProvider } from '../src/lib/oauth/providers'

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
