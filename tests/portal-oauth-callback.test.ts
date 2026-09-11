/**
 * Coverage for the customer-facing portal OAuth callback at
 * `src/pages/portal/products/operator/oauth/[connector]/callback.ts`.
 *
 * Mirrors the admin-side callback test (tests/oauth-callback.test.ts)
 * but exercises the reviewer-id-bound-to-Clerk-session path: state
 * verification + clerk reviewer match + connector-path/state-provider
 * agreement + redirect-to-portal on success.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import { issueOAuthState } from '../src/lib/oauth/state'

// The callback re-resolves the reviewer's CURRENT access on the instance before
// storing a token (2026-09-10). That helper reads D1 through the portal session
// bridge; here it is a switch the test flips, so the callback's own branches are
// what is under test. Default: the reviewer is still a principal.
const accessKind = { value: 'allowed' as 'allowed' | 'redirect' }
const resolveOperatorAccess = vi.fn(
  async (
    _db: unknown,
    _locals: unknown,
    options: { allowedRoles: string[]; customerSlug: string }
  ) =>
    accessKind.value === 'allowed'
      ? { kind: 'allowed' as const, options }
      : { kind: 'redirect' as const, to: '/portal/products/operator' }
)
vi.mock('../src/lib/portal/operator-access.js', () => ({
  resolveOperatorAccess: (
    db: unknown,
    locals: unknown,
    options: { allowedRoles: string[]; customerSlug: string }
  ) => resolveOperatorAccess(db, locals, options),
}))

import { GET as portalCallback } from '../src/pages/portal/products/operator/oauth/[connector]/callback'

const SIGNING_KEY_B64 = 'YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE='
const PORTAL_BASE = 'https://portal.smd.services'

function clearEnv(): void {
  for (const key of Object.keys(testEnv)) {
    delete (testEnv as unknown as Record<string, unknown>)[key]
  }
}

function applyDefaultEnv(): void {
  Object.assign(testEnv, {
    PORTAL_BASE_URL: PORTAL_BASE,
    APP_BASE_URL: PORTAL_BASE,
    OAUTH_STATE_SIGNING_KEY: SIGNING_KEY_B64,
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

function makeAuth(userId: string | null): () => { userId: string | null } {
  return () => ({ userId })
}

async function invoke(opts: {
  url: string
  connector: string
  authUserId?: string | null
}): Promise<Response> {
  return await portalCallback({
    request: new Request(opts.url),
    redirect,
    params: { connector: opts.connector },
    locals: {
      auth: makeAuth(opts.authUserId ?? null),
    } as unknown as App.Locals,
  } as unknown as Parameters<typeof portalCallback>[0])
}

function parseRedirect(response: Response): URL {
  const location = response.headers.get('Location')
  if (!location) throw new Error('redirect response missing Location header')
  return new URL(location)
}

describe('portal oauth callback', () => {
  const ORIGINAL_FETCH = globalThis.fetch

  beforeEach(() => {
    applyDefaultEnv()
    accessKind.value = 'allowed'
    resolveOperatorAccess.mockClear()
  })

  afterEach(() => {
    clearEnv()
    globalThis.fetch = ORIGINAL_FETCH
  })

  it('redirects with access_revoked when the reviewer is no longer a principal at callback time', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })
    accessKind.value = 'redirect'
    // The token endpoint must never be reached: refusal precedes exchange.
    globalThis.fetch = vi.fn(async () => {
      throw new Error('token exchange must not run for a revoked reviewer')
    })
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=AUTHCODE&state=${encodeURIComponent(state)}`
    const response = await invoke({ url, connector: 'microsoft-graph', authUserId: 'user_owner' })
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('failed')
    expect(location.searchParams.get('reason')).toBe('access_revoked')
    // Re-resolved with the initiator's own gate: principal on THIS instance.
    expect(resolveOperatorAccess).toHaveBeenCalledTimes(1)
    expect(resolveOperatorAccess.mock.calls[0]?.[2]).toEqual({
      allowedRoles: ['principal'],
      customerSlug: 'acme-law',
    })
  })

  it('redirects with missing_params when state and code are absent', async () => {
    const response = await invoke({
      url: `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback`,
      connector: 'microsoft-graph',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('failed')
    expect(location.searchParams.get('reason')).toBe('missing_params')
    // No verified state ⇒ no known instance ⇒ falls back to the bare operator root.
    expect(location.origin).toBe(PORTAL_BASE)
    expect(location.pathname).toBe('/portal/products/operator')
  })

  it('redirects with provider_error when issuer rejects consent', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_xyz',
    })
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?error=consent_required&error_description=admin+consent+required&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: 'user_xyz',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('provider_error')
    expect(location.searchParams.get('provider')).toBe('microsoft-graph')
  })

  it('redirects with bad_state when signature does not verify', async () => {
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=abc&state=garbage`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: 'user_xyz',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('bad_state')
  })

  it('redirects with reviewer_mismatch when Clerk userId does not match state', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: 'user_someone_else',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('reviewer_mismatch')
  })

  it('redirects with reviewer_mismatch when no Clerk auth is present', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: null,
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('reviewer_mismatch')
  })

  it('redirects with unknown_connector when the path connector differs from the state provider', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })
    const url = `${PORTAL_BASE}/portal/products/operator/oauth/google-workspace/callback?code=abc&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'google-workspace',
      authUserId: 'user_owner',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('unknown_connector')
  })

  it('redirects to settings with status=connected on the happy path', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })

    // Mock the Microsoft Graph token exchange.
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const target = typeof input === 'string' ? input : input.toString()
      if (target === 'https://login.microsoftonline.com/common/oauth2/v2.0/token') {
        const body = (init?.body as URLSearchParams).toString()
        expect(body).toContain('grant_type=authorization_code')
        // The redirect_uri sent to the token endpoint MUST be the portal one.
        expect(body).toContain(
          'redirect_uri=https%3A%2F%2Fportal.smd.services%2Fportal%2Fproducts%2Foperator%2Foauth%2Fmicrosoft-graph%2Fcallback'
        )
        return new Response(
          JSON.stringify({
            access_token: 'msg-access',
            refresh_token: 'msg-refresh',
            expires_in: 3600,
            scope: 'Mail.Read Calendars.ReadWrite Files.ReadWrite.AppFolder',
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      return new Response('not mocked', { status: 500 })
    })

    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=AUTHCODE&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: 'user_owner',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('status')).toBe('connected')
    expect(location.searchParams.get('provider')).toBe('microsoft-graph')
    // Verified state carries the instance slug, so the return lands on that
    // operator's settings page.
    expect(location.pathname).toBe('/portal/products/operator/acme-law/settings')
  })

  it('redirects with exchange_failed when the token endpoint rejects the code', async () => {
    const state = await issueOAuthState({
      customer_id: 'acme-law',
      provider: 'microsoft-graph',
      reviewer_id: 'user_owner',
    })
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: 'invalid_grant' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
    )

    const url = `${PORTAL_BASE}/portal/products/operator/oauth/microsoft-graph/callback?code=BAD&state=${encodeURIComponent(state)}`
    const response = await invoke({
      url,
      connector: 'microsoft-graph',
      authUserId: 'user_owner',
    })
    const location = parseRedirect(response)
    expect(location.searchParams.get('reason')).toBe('exchange_failed')
  })
})
