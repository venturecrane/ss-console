/**
 * POST /api/events, driven end to end against a migrated D1 and an in-memory
 * KV: the per-IP bound and the cookie-first session identity that closed the
 * "unauthenticated, effectively unbounded D1 write" finding (2026-08-23 review
 * C3, carried three reviews, closed 2026-09-10).
 *
 * What would turn each test red: dropping the `rateLimitByIp` call (the IP
 * test writes rows again), restoring body-first session resolution (the cookie
 * test records the body's id), or removing the seed-from-body path (the
 * cookieless test records a random id and the client's queue splits sessions).
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database, KVNamespace } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { POST } from '../src/pages/api/events'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')
const COOKIE_SID = '11111111-1111-4111-8111-111111111111'
const BODY_SID = '22222222-2222-4222-8222-222222222222'
const IP = '203.0.113.9'

function memoryKv(): { kv: KVNamespace; store: Map<string, string> } {
  const store = new Map<string, string>()
  const kv = {
    get: async (key: string) => store.get(key) ?? null,
    put: async (key: string, value: string) => {
      store.set(key, value)
    },
  } as unknown as KVNamespace
  return { kv, store }
}

async function post(opts: {
  cookie?: string
  bodySessionId?: string
  ip?: string
}): Promise<Response> {
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (opts.cookie) headers.cookie = opts.cookie
  if (opts.ip) headers['cf-connecting-ip'] = opts.ip
  const request = new Request('https://smd.services/api/events', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      session_id: opts.bodySessionId,
      events: [{ event_name: 'page_view', path: '/operator' }],
    }),
  })
  return POST({ request } as unknown as Parameters<typeof POST>[0])
}

async function sessionIdsWritten(db: D1Database): Promise<string[]> {
  const rows = await db.prepare('SELECT session_id FROM events ORDER BY rowid').all<{
    session_id: string
  }>()
  return (rows.results ?? []).map((r) => r.session_id)
}

describe('POST /api/events', () => {
  let db: D1Database
  let store: Map<string, string>

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    const kv = memoryKv()
    store = kv.store
    Object.assign(testEnv, { DB: db, BOOKING_CACHE: kv.kv })
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('the cookie is the session identity; a disagreeing body id is ignored', async () => {
    const res = await post({ cookie: `ss_sid=${COOKIE_SID}`, bodySessionId: BODY_SID, ip: IP })
    expect(res.status).toBe(204)
    expect(await sessionIdsWritten(db)).toEqual([COOKIE_SID])
    // The cookie already carried the id, so nothing is re-set.
    expect(res.headers.get('set-cookie')).toBeNull()
  })

  it('with no cookie the body id seeds the session and the cookie is set to it', async () => {
    const res = await post({ bodySessionId: BODY_SID, ip: IP })
    expect(res.status).toBe(204)
    expect(await sessionIdsWritten(db)).toEqual([BODY_SID])
    const setCookie = res.headers.get('set-cookie') ?? ''
    expect(setCookie).toContain(`ss_sid=${BODY_SID}`)
    expect(setCookie).toContain('SameSite=Lax')
    // The tracker reads this cookie from document.cookie, so it stays readable.
    expect(setCookie).not.toContain('HttpOnly')
  })

  it('clamps to 204 without writing once the per-IP hour bucket is full', async () => {
    const windowId = Math.floor(Date.now() / 1000 / 3600)
    store.set(`rl:events:${IP}:${windowId}`, '600')
    const res = await post({ cookie: `ss_sid=${COOKIE_SID}`, ip: IP })
    expect(res.status).toBe(204)
    expect(await sessionIdsWritten(db)).toEqual([])
  })

  it('a different IP is a different bucket', async () => {
    const windowId = Math.floor(Date.now() / 1000 / 3600)
    store.set(`rl:events:${IP}:${windowId}`, '600')
    const res = await post({ cookie: `ss_sid=${COOKIE_SID}`, ip: '198.51.100.4' })
    expect(res.status).toBe(204)
    expect(await sessionIdsWritten(db)).toEqual([COOKIE_SID])
  })
})
