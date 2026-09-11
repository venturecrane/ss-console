/**
 * GET /api/booking/slots is rate-limited per IP.
 *
 * Until 2026-09-11 this was the one public booking route with no limiter,
 * on the reasoning that availability is uninteresting to scrape. Every
 * request is one upstream Google freeBusy call, so an unlimited public read
 * was an unlimited upstream spend. The limiter runs before anything else.
 *
 * What makes this red: a full bucket that does not produce the 429 the other
 * booking routes produce, or a fresh bucket that never reaches the handler.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'

vi.mock('../../src/lib/db/integrations', () => ({
  getIntegration: vi.fn(async () => null),
  getGoogleAccessToken: vi.fn(async () => null),
}))

import { GET } from '../../src/pages/api/booking/slots'

function createMemoryKv(): KVNamespace {
  const store = new Map<string, string>()
  return {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    put: vi.fn(async (key: string, value: string) => {
      store.set(key, value)
    }),
    delete: vi.fn(async (key: string) => {
      store.delete(key)
    }),
    list: vi.fn(),
    getWithMetadata: vi.fn(),
  } as unknown as KVNamespace
}

function request(ip: string): Parameters<typeof GET>[0] {
  const url = new URL('https://smd.services/api/booking/slots')
  return {
    url,
    request: new Request(url, { headers: { 'cf-connecting-ip': ip } }),
    params: {},
    locals: {},
  } as unknown as Parameters<typeof GET>[0]
}

describe('GET /api/booking/slots rate limit', () => {
  let kv: KVNamespace

  beforeEach(() => {
    kv = createMemoryKv()
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { BOOKING_CACHE: kv, DB: {} })
  })

  it('returns 429 rate_limited when the per-IP bucket is full', async () => {
    const ip = '203.0.113.9'
    const windowId = Math.floor(Date.now() / 1000 / 3600)
    await kv.put(`rl:slots:${ip}:${windowId}`, '120')

    const res = await GET(request(ip))
    expect(res.status).toBe(429)
    const body: { error: string } = await res.json()
    expect(body.error).toBe('rate_limited')
  })

  it('a fresh bucket reaches the handler (here: the no-integration 503), and counts the request', async () => {
    const ip = '203.0.113.10'
    const res = await GET(request(ip))
    expect(res.status).toBe(503)
    const body: { error: string } = await res.json()
    expect(body.error).toBe('calendar_unavailable')
    const windowId = Math.floor(Date.now() / 1000 / 3600)
    expect(await kv.get(`rl:slots:${ip}:${windowId}`)).toBe('1')
  })
})
