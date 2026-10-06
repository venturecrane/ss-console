import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

import {
  issueOAuthState,
  verifyOAuthState,
  DEFAULT_STATE_TTL_SECONDS,
} from '../src/lib/oauth/state'
import { getOAuthProvider } from '../src/lib/oauth/providers'
import { emitAuditEvent } from '../src/lib/oauth/audit'
import type { D1Database } from '@cloudflare/workers-types'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const { captureErrorMock } = vi.hoisted(() => ({ captureErrorMock: vi.fn() }))
vi.mock('../src/lib/observability/sentry', () => ({ captureError: captureErrorMock }))

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

/**
 * The callback's audit events reach D1 (code review 2026-10-06, I12). They
 * went to console.log only, behind a TODO naming #891, which closed with
 * nothing persisted. These run the real migration in node:sqlite behind a
 * D1-shaped adapter, so the assertion is on the row, not on a mock's call log.
 */
describe('oauth/audit persistence', () => {
  type Row = Record<string, unknown>

  async function migratedDb(): Promise<{
    d1: D1Database
    rows: () => Row[]
    cols: () => string[]
  }> {
    const { DatabaseSync } = await import('node:sqlite')
    const db = new DatabaseSync(':memory:')
    db.exec(readFileSync(join(process.cwd(), 'migrations/0121_oauth_callback_audit.sql'), 'utf8'))
    const d1 = {
      prepare(sql: string) {
        return {
          bind(...args: unknown[]) {
            return {
              async run() {
                db.prepare(sql).run(...(args as (string | null)[]))
                return { success: true }
              },
            }
          },
        }
      },
    } as unknown as D1Database
    return {
      d1,
      rows: () => db.prepare('SELECT * FROM oauth_callback_audit ORDER BY id').all(),
      cols: () =>
        (db.prepare('PRAGMA table_info(oauth_callback_audit)').all() as { name: string }[]).map(
          (c) => c.name
        ),
    }
  }

  beforeEach(() => {
    captureErrorMock.mockClear()
  })

  it('writes a token-issued row with the event metadata', async () => {
    const { d1, rows } = await migratedDb()
    await emitAuditEvent(d1, {
      action: 'token-issued',
      customer_id: 'firm-a',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
    })
    const [row] = rows()
    expect(row).toMatchObject({
      skill: 'oauth-callback',
      action: 'token-issued',
      customer_id: 'firm-a',
      provider: 'google-workspace',
      reviewer_id: 'user-1',
      reason: null,
    })
    expect(String(row.ts)).toMatch(/^\d{4}-\d{2}-\d{2}T/)
  })

  it('writes a token-rejected row with its reason and null identity', async () => {
    const { d1, rows } = await migratedDb()
    await emitAuditEvent(d1, {
      action: 'token-rejected',
      customer_id: null,
      provider: null,
      reviewer_id: null,
      reason: 'state_invalid',
    })
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toMatchObject({ action: 'token-rejected', reason: 'state_invalid' })
  })

  it('has no column that could hold token material', async () => {
    const { cols } = await migratedDb()
    for (const c of cols()) expect(c).not.toMatch(/token|secret|code|state/i)
  })

  it('a failed insert does not throw, and is logged and captured', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const broken = {
      prepare() {
        throw new Error('no such table: oauth_callback_audit')
      },
    } as unknown as D1Database
    await expect(
      emitAuditEvent(broken, {
        action: 'token-issued',
        customer_id: 'firm-a',
        provider: 'google-workspace',
        reviewer_id: 'user-1',
      })
    ).resolves.toBeUndefined()
    expect(captureErrorMock).toHaveBeenCalledTimes(1)
    expect(captureErrorMock.mock.calls[0][1]).toBe('oauth-callback-audit')
    expect(errSpy).toHaveBeenCalled()
    errSpy.mockRestore()
  })

  it('the callback passes the D1 binding at every emit site', () => {
    const src = readFileSync(
      join(process.cwd(), 'src/pages/portal/products/operator/oauth/[connector]/callback.ts'),
      'utf8'
    )
    const calls = src.match(/emitAuditEvent\(/g) ?? []
    const withDb = src.match(/emitAuditEvent\(env\.DB,/g) ?? []
    expect(calls.length).toBeGreaterThan(0)
    expect(withDb.length).toBe(calls.length)
  })
})
