/**
 * Tests for POST /api/internal/sentry-probe (#1626).
 *
 * The probe exists to prove the Sentry middleware seam on the real
 * runtime, so the unit tests only pin the two safety properties:
 *   - Unauthenticated requests get the uniform 401 and do NOT throw
 *     (no anonymous path to generating Sentry events).
 *   - An authenticated request throws (the error must escape the
 *     handler so `withSentryRequestHandler` sees it — a caught error
 *     here would silently break the probe's purpose).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { APIContext } from 'astro'
import { env as testEnv } from 'cloudflare:workers'
import { POST } from '../src/pages/api/internal/sentry-probe'
import { machineKeyHashHex } from './helpers/machine-credential'

const KEY = '0'.repeat(64)
const SALT = '11'.repeat(16)

/** The join row the verifier selects, credentialed for KEY (no shared key exists). */
function mockDb(slugToEntity: Record<string, string>) {
  return {
    prepare: vi.fn((_sql: string) => ({
      bind: (slug: string) => ({
        first: async <T>(): Promise<T | null> => {
          const entityId = slugToEntity[slug]
          return entityId
            ? ({
                entity_id: entityId,
                key_hash: machineKeyHashHex(KEY, SALT),
                salt: SALT,
                prev_key_hash: null,
                prev_salt: null,
                prev_expires_at: null,
              } as unknown as T)
            : null
        },
      }),
    })),
  }
}

function ctx(headers: Record<string, string>): APIContext {
  return {
    request: new Request('https://example/api/internal/sentry-probe', {
      method: 'POST',
      headers,
    }),
  } as unknown as APIContext
}

beforeEach(() => {
  for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
  Object.assign(testEnv, { DB: mockDb({ smd: 'ent-smd' }) })
})

describe('POST /api/internal/sentry-probe', () => {
  it('returns 401 without throwing when the bearer key is missing', async () => {
    const res = await (POST(ctx({ 'X-Tenant-Slug': 'smd' })) as Promise<Response>)
    expect(res.status).toBe(401)
  })

  it('returns 401 without throwing on a wrong bearer key', async () => {
    const res = await (POST(
      ctx({ Authorization: 'Bearer nope', 'X-Tenant-Slug': 'smd' })
    ) as Promise<Response>)
    expect(res.status).toBe(401)
  })

  it('returns 401 on an unknown tenant slug', async () => {
    const res = await (POST(
      ctx({ Authorization: `Bearer ${KEY}`, 'X-Tenant-Slug': 'ghost' })
    ) as Promise<Response>)
    expect(res.status).toBe(401)
  })

  it('throws a deliberate error on valid auth so the middleware wrapper sees it', async () => {
    await expect(
      POST(ctx({ Authorization: `Bearer ${KEY}`, 'X-Tenant-Slug': 'smd' })) as Promise<Response>
    ).rejects.toThrow(/sentry-probe: deliberate uncaught error \(tenant=smd\)/)
  })
})
