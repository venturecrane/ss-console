/**
 * Tests for the Sentry 24h sync (ADR 0023 Wave 1).
 *
 * Covers:
 *   - Unavailable-env path: skips with status='unavailable', never calls fetch.
 *   - Happy path: query-string shape, bearer header, count extraction.
 *   - Failure paths: HTTP non-2xx, network throw, malformed JSON, malformed
 *     payload shape — all return count=null without throwing.
 *   - Writer: ON CONFLICT upsert binds in the right order; nulls allowed;
 *     the conflict target is the table's real PRIMARY KEY (customer_slug),
 *     because a target SQLite cannot resolve fails the whole statement and
 *     the sync dies silently (it did, 2026-07-23 to 2026-10-01).
 */

import { describe, it, expect, vi } from 'vitest'
import {
  fetchTenantErrorsLast24h,
  writeSentrySync,
  type FleetStatusWriter,
  type SentrySyncEnv,
} from './sentry-sync'

const FULL_ENV: SentrySyncEnv = {
  SENTRY_AUTH_TOKEN: 'sentry-tok',
  SENTRY_ORG_SLUG: 'smd',
  SENTRY_PROJECT_ID: '12345',
}

function makeFetch(response: {
  ok?: boolean
  status?: number
  body?: unknown
  throws?: boolean
}): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (response.throws) throw new Error('network down')
    const _unused = { input, init }
    return new Response(JSON.stringify(response.body ?? {}), {
      status: response.status ?? (response.ok === false ? 500 : 200),
      headers: { 'Content-Type': 'application/json' },
    })
  })
}

describe('fetchTenantErrorsLast24h', () => {
  it('returns unavailable when any required env is missing', async () => {
    const fetchSpy = vi.fn()
    const r = await fetchTenantErrorsLast24h({}, 'smd', fetchSpy)
    expect(r.status).toBe('unavailable')
    expect(r.count).toBe(null)
    expect(fetchSpy.mock.calls).toHaveLength(0)
  })

  it('returns ok + count on happy path', async () => {
    const captured: { url: string; auth: string }[] = []
    const fetchSpy: typeof fetch = vi.fn(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString()
      const headers = (init?.headers ?? {}) as Record<string, string>
      captured.push({ url, auth: headers.Authorization })
      return new Response(JSON.stringify({ data: [{ 'count()': 17 }] }), { status: 200 })
    })

    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('ok')
    expect(r.count).toBe(17)
    expect(captured[0].url).toContain('organizations/smd/events/')
    expect(captured[0].url).toContain('field=count')
    expect(captured[0].url).toContain('query=tenant%3Asmd')
    expect(captured[0].url).toContain('statsPeriod=24h')
    expect(captured[0].url).toContain('project=12345')
    expect(captured[0].auth).toBe('Bearer sentry-tok')
  })

  it('accepts the alternate "count" key shape', async () => {
    const fetchSpy = makeFetch({ body: { data: [{ count: 5 }] } })
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.count).toBe(5)
  })

  it('returns zero when data is empty array', async () => {
    const fetchSpy = makeFetch({ body: { data: [] } })
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('ok')
    expect(r.count).toBe(0)
  })

  it('returns http_error on non-2xx', async () => {
    const fetchSpy = makeFetch({ ok: false, status: 403 })
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('http_error')
    expect(r.count).toBe(null)
    expect(r.reason).toContain('403')
  })

  it('returns http_error when fetch throws', async () => {
    const fetchSpy = makeFetch({ throws: true })
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('http_error')
    expect(r.count).toBe(null)
    expect(r.reason).toContain('network')
  })

  it('returns parse_error when body is not JSON', async () => {
    const fetchSpy = vi.fn(async () => new Response('not json', { status: 200 }))
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('parse_error')
    expect(r.count).toBe(null)
  })

  it('returns ok + null count when shape is unexpected', async () => {
    const fetchSpy = makeFetch({ body: { data: [{ something_else: 'oops' }] } })
    const r = await fetchTenantErrorsLast24h(FULL_ENV, 'smd', fetchSpy)
    expect(r.status).toBe('ok')
    expect(r.count).toBe(null)
  })

  it('floors fractional / clamps negative count values', async () => {
    const fetchA = makeFetch({ body: { data: [{ 'count()': 3.7 }] } })
    const fetchB = makeFetch({ body: { data: [{ 'count()': -2 }] } })
    expect((await fetchTenantErrorsLast24h(FULL_ENV, 's', fetchA)).count).toBe(3)
    expect((await fetchTenantErrorsLast24h(FULL_ENV, 's', fetchB)).count).toBe(0)
  })
})

describe('writeSentrySync', () => {
  function makeWriter(): { writer: FleetStatusWriter; binds: unknown[][]; sqls: string[] } {
    const binds: unknown[][] = []
    const sqls: string[] = []
    const writer: FleetStatusWriter = {
      prepare: (sql: string) => {
        sqls.push(sql)
        return {
          bind: (...args: (string | number | null)[]) => {
            binds.push(args)
            return { run: async () => undefined }
          },
        }
      },
    }
    return { writer, binds, sqls }
  }

  it('skips entirely when status is unavailable', async () => {
    const { writer, binds } = makeWriter()
    await writeSentrySync(
      writer,
      'ent-smd',
      'smd',
      { customer_slug: 'smd', status: 'unavailable', count: null },
      '2026-05-26T18:00:00Z'
    )
    expect(binds).toHaveLength(0)
  })

  it('binds entity_id, slug, count, synced_at on success', async () => {
    const { writer, binds, sqls } = makeWriter()
    await writeSentrySync(
      writer,
      'ent-smd',
      'smd',
      { customer_slug: 'smd', status: 'ok', count: 12 },
      '2026-05-26T18:00:00Z'
    )
    expect(binds).toHaveLength(1)
    expect(binds[0]).toEqual(['ent-smd', 'smd', 12, '2026-05-26T18:00:00Z', '2026-05-26T18:00:00Z'])
    expect(sqls[0]).toContain('INSERT INTO fleet_status')
    // fleet_status is keyed on customer_slug (migration 0093); entity_id is a
    // plain column several seats share. ON CONFLICT(entity_id) is not a valid
    // conflict target and SQLite rejects the statement outright.
    expect(sqls[0]).toContain('ON CONFLICT(customer_slug) DO UPDATE')
    expect(sqls[0]).not.toContain('ON CONFLICT(entity_id)')
    expect(sqls[0]).toContain('entity_id               = excluded.entity_id')
  })

  it('binds NULL count for http_error / parse_error', async () => {
    const { writer, binds } = makeWriter()
    await writeSentrySync(
      writer,
      'ent-smd',
      'smd',
      { customer_slug: 'smd', status: 'http_error', count: null, reason: 'HTTP 500' },
      '2026-05-26T18:00:00Z'
    )
    expect(binds[0][2]).toBe(null)
    // synced_at still stamped — null+timestamp is honest about "we tried, no data"
    expect(binds[0][3]).toBe('2026-05-26T18:00:00Z')
  })
})
