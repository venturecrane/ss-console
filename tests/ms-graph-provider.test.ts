/**
 * Coverage for the Microsoft Graph provider entry at
 * `src/lib/oauth/providers/ms-graph.ts`. Verifies scope discipline
 * and Mail.Send refusal.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import {
  MS_GRAPH_PHASE_1_SCOPES,
  MS_GRAPH_TOKEN_URL,
  microsoftGraphProvider,
} from '../src/lib/oauth/providers/ms-graph'

function clearEnv(): void {
  for (const key of Object.keys(testEnv)) {
    delete (testEnv as unknown as Record<string, unknown>)[key]
  }
}

describe('MS_GRAPH_PHASE_1_SCOPES', () => {
  it('does NOT include Mail.Send', () => {
    expect(MS_GRAPH_PHASE_1_SCOPES).not.toContain('Mail.Send')
    expect(MS_GRAPH_PHASE_1_SCOPES.every((s) => s.toLowerCase() !== 'mail.send')).toBe(true)
  })

  it('matches the lifecycle spec scope set', () => {
    const expected = new Set([
      'offline_access',
      'User.Read',
      'Mail.Read',
      'Mail.ReadWrite',
      'MailboxSettings.Read',
      'Calendars.ReadWrite',
      'Files.Read',
      'Files.ReadWrite.AppFolder',
    ])
    expect(new Set(MS_GRAPH_PHASE_1_SCOPES)).toEqual(expected)
  })

  it('includes offline_access for refresh-token issuance', () => {
    expect(MS_GRAPH_PHASE_1_SCOPES).toContain('offline_access')
  })

  it('is frozen so callers cannot mutate it', () => {
    expect(Object.isFrozen(MS_GRAPH_PHASE_1_SCOPES)).toBe(true)
  })
})

describe('microsoftGraphProvider entry', () => {
  const ORIGINAL_FETCH = globalThis.fetch

  beforeEach(() => {
    Object.assign(testEnv, {
      MICROSOFT_GRAPH_CLIENT_ID: 'app-id',
      MICROSOFT_GRAPH_CLIENT_SECRET: 'app-secret',
    })
  })

  afterEach(() => {
    clearEnv()
    globalThis.fetch = ORIGINAL_FETCH
  })

  it('declares the canonical slug and label', () => {
    expect(microsoftGraphProvider.slug).toBe('microsoft-graph')
    expect(microsoftGraphProvider.label).toBe('Microsoft Graph')
    expect(microsoftGraphProvider.token_url).toBe(MS_GRAPH_TOKEN_URL)
  })

  it('exchange_code POSTs to the v2 token endpoint with the Phase-1 scopes', async () => {
    let capturedBody = ''
    globalThis.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      capturedBody = (init?.body as URLSearchParams).toString()
      return new Response(
        JSON.stringify({
          access_token: 'tok',
          refresh_token: 'refresh',
          expires_in: 3600,
          scope: 'Mail.Read Calendars.ReadWrite',
          token_type: 'Bearer',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    })
    const result = await microsoftGraphProvider.exchange_code({
      code: 'AUTH123',
      redirect_uri:
        'https://portal.smd.services/portal/products/operator/oauth/microsoft-graph/callback',
    })
    expect(result.access_token).toBe('tok')
    expect(result.refresh_token).toBe('refresh')
    expect(capturedBody).toContain('grant_type=authorization_code')
    expect(capturedBody).toContain('client_id=app-id')
    expect(capturedBody).toContain('code=AUTH123')
    expect(capturedBody).toContain('offline_access')
    expect(capturedBody).not.toContain('Mail.Send')
  })

  it('throws when MICROSOFT_GRAPH_CLIENT_ID is missing', async () => {
    delete (testEnv as unknown as Record<string, unknown>).MICROSOFT_GRAPH_CLIENT_ID
    await expect(
      microsoftGraphProvider.exchange_code({
        code: 'x',
        redirect_uri: 'https://example/cb',
      })
    ).rejects.toThrow(/MICROSOFT_GRAPH_CLIENT_ID/)
  })

  it('propagates a structured error on non-2xx token responses', async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: 'invalid_grant' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    await expect(
      microsoftGraphProvider.exchange_code({
        code: 'x',
        redirect_uri: 'https://example/cb',
      })
    ).rejects.toThrow(/token exchange failed \(400\)/)
  })
})
