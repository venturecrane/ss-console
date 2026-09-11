/**
 * The KV-backed limiter fails CLOSED when its binding is missing.
 *
 * Before 2026-09-10 a missing `BOOKING_CACHE` binding meant "dev mode, allow",
 * which is the one failure that turns every rate-limited public endpoint into
 * an unlimited one without a single test going red. Now it refuses and reports.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

const captureError = vi.fn()
vi.mock('../src/lib/observability/sentry', () => ({
  captureError: (...args: unknown[]) => captureError(...args),
}))

import { rateLimitByIp } from '../src/lib/booking/rate-limit'

type KvArg = Parameters<typeof rateLimitByIp>[0]

function memoryKv(): KvArg {
  const store = new Map<string, string>()
  return {
    get: async (key: string) => store.get(key) ?? null,
    put: async (key: string, value: string) => {
      store.set(key, value)
    },
  } as unknown as KvArg
}

describe('rateLimitByIp', () => {
  beforeEach(() => {
    captureError.mockClear()
  })
  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('refuses and reports when the KV binding is missing (fail closed)', async () => {
    const result = await rateLimitByIp(undefined, 'contact', '203.0.113.7', 3)
    expect(result.allowed).toBe(false)
    expect(captureError).toHaveBeenCalledTimes(1)
    expect(String(captureError.mock.calls[0]?.[0])).toContain('no KV binding')
    expect(captureError.mock.calls[0]?.[1]).toBe('booking.rate-limit')
  })

  it('counts and then refuses at the limit when KV is bound', async () => {
    const kv = memoryKv()
    const results = []
    for (let i = 0; i < 4; i++) results.push(await rateLimitByIp(kv, 'contact', '203.0.113.7', 3))
    expect(results.map((r) => r.allowed)).toEqual([true, true, true, false])
    expect(captureError).not.toHaveBeenCalled()
  })
})
