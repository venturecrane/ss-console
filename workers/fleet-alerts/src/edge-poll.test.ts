/**
 * The outside-in edge poll (review 2026-09-10, wave 8.2): target parsing, the
 * health contract, the consecutive-run counting that makes it flap-safe, and
 * the store, run against real SQLite with the real migration so the SQL is the
 * thing under test (the same discipline as stale-holds.test.ts).
 */

import { readFileSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'
import { describe, it, expect } from 'vitest'
import {
  classifyHealthResponse,
  edgeThresholds,
  evaluateEdgePoll,
  parseEdgeTargets,
  probeEdge,
  retireUnconfiguredTargets,
  runEdgePolls,
  type EdgeCounters,
  type FetchLike,
} from './edge-poll'
import { EDGE_DOWN_CONDITION } from './conditions'
import type { Env } from './index'

// Resolved from this file, not the cwd: the root vitest run and the Worker's
// own both collect this suite.
const MIGRATION = new URL('../../../migrations/0115_edge_poll_state.sql', import.meta.url).pathname
const NOW = Date.parse('2026-09-11T22:00:00.000Z')
const HEALTH = 'https://smd.services/api/health'
const THRESHOLDS = { failThreshold: 3, recoverThreshold: 2, timeoutMs: 10_000 }

/**
 * fleet_alert_state as migration 0116 leaves it (the columns retirement reads
 * and the CHECK that admits edge_down). The real chain is pinned by
 * tests/fleet-alert-conditions-migrated.test.ts; this mirror exists so the
 * retirement SQL runs against real SQLite here too.
 */
const ALERT_TABLE = `CREATE TABLE fleet_alert_state (
  customer_slug TEXT NOT NULL,
  condition TEXT NOT NULL CHECK (condition IN ('heartbeat_red', 'edge_down')),
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  opened_at TEXT NOT NULL,
  resolved_at TEXT,
  last_alert_id TEXT,
  last_seen_marker TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (customer_slug, condition)
)`

/** A D1-shaped adapter over node:sqlite, just wide enough for edge-poll.ts. */
function realDb(): { d1: D1Database; raw: DatabaseSync } {
  const raw = new DatabaseSync(':memory:')
  raw.exec(readFileSync(MIGRATION, 'utf8'))
  raw.exec(ALERT_TABLE)
  const d1 = {
    prepare(sql: string) {
      const stmt = raw.prepare(sql)
      return {
        all: () => Promise.resolve({ results: stmt.all() }),
        bind(...args: unknown[]) {
          const bound = args as Array<string | number | null>
          return {
            first: () =>
              Promise.resolve((stmt.get(...bound) as Record<string, unknown> | undefined) ?? null),
            run: () => {
              stmt.run(...bound)
              return Promise.resolve({})
            },
            all: () => Promise.resolve({ results: stmt.all(...bound) }),
          }
        },
      }
    },
  }
  return { d1: d1 as unknown as D1Database, raw }
}

function env(extra: Partial<Env> = {}, db?: D1Database): Env {
  return {
    DB: db ?? realDb().d1,
    EDGE_POLL_TARGETS: `web=${HEALTH}`,
    ...extra,
  }
}

const ok: FetchLike = () =>
  Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))
const dbDown: FetchLike = () =>
  Promise.resolve(new Response(JSON.stringify({ status: 'error' }), { status: 503 }))

describe('parseEdgeTargets', () => {
  it('reads comma-separated name=https-url entries', () => {
    const { targets, errors } = parseEdgeTargets(
      ` web=${HEALTH}, admin=https://admin.smd.services/x `
    )
    expect(errors).toEqual([])
    expect(targets).toEqual([
      { name: 'web', url: HEALTH },
      { name: 'admin', url: 'https://admin.smd.services/x' },
    ])
  })

  it('unset or empty means no targets and no errors', () => {
    expect(parseEdgeTargets(undefined)).toEqual({ targets: [], errors: [] })
    expect(parseEdgeTargets('')).toEqual({ targets: [], errors: [] })
  })

  it('rejects malformed, non-https, oddly named and duplicate entries as errors, keeping the rest', () => {
    const { targets, errors } = parseEdgeTargets(
      `nourl,web=${HEALTH},plain=http://smd.services/api/health,Bad Name=${HEALTH},web=${HEALTH}`
    )
    expect(targets).toEqual([{ name: 'web', url: HEALTH }])
    expect(errors).toHaveLength(4)
    expect(errors.join('\n')).toContain('must be an https:// URL')
    expect(errors.join('\n')).toContain('listed twice')
  })
})

describe('edgeThresholds', () => {
  it('defaults to 3 failures, 2 recoveries, 10s', () => {
    expect(edgeThresholds({})).toEqual(THRESHOLDS)
  })

  it('reads the vars, floors the counts at 1 and clamps the timeout to 1s..30s', () => {
    expect(
      edgeThresholds({
        EDGE_POLL_FAIL_THRESHOLD: '5',
        EDGE_POLL_RECOVER_THRESHOLD: '0',
        EDGE_POLL_TIMEOUT_MS: '90000',
      })
    ).toEqual({ failThreshold: 5, recoverThreshold: 2, timeoutMs: 30_000 })
    expect(edgeThresholds({ EDGE_POLL_TIMEOUT_MS: '5' }).timeoutMs).toBe(10_000)
    expect(edgeThresholds({ EDGE_POLL_FAIL_THRESHOLD: 'three' }).failThreshold).toBe(3)
  })
})

describe('classifyHealthResponse', () => {
  it('200 with body status ok is the only green', () => {
    expect(classifyHealthResponse(200, '{"status":"ok"}')).toEqual({
      ok: true,
      detail: 'HTTP 200, body status "ok"',
    })
  })

  it('503 status error (the D1 probe failed) is red with the body status named', () => {
    expect(classifyHealthResponse(503, '{"status":"error"}')).toEqual({
      ok: false,
      detail: 'HTTP 503, body status "error"',
    })
  })

  it('a 200 that is not the health shape is red, with the first bytes recorded', () => {
    const r = classifyHealthResponse(200, '<html>maintenance</html>')
    expect(r.ok).toBe(false)
    expect(r.detail).toContain('HTTP 200, body is not the health shape: <html>maintenance</html>')
  })

  it('a redirect is red (the admin and portal hosts answer /api/health this way)', () => {
    expect(classifyHealthResponse(302, '').ok).toBe(false)
  })
})

describe('probeEdge', () => {
  it('GETs the target with a manual redirect policy and classifies the answer', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: FetchLike = (url, init) => {
      calls.push({ url, init })
      return ok(url, init)
    }
    const probe = await probeEdge({ name: 'web', url: HEALTH }, fetchImpl, 10_000)
    expect(probe.ok).toBe(true)
    expect(calls[0]?.url).toBe(HEALTH)
    expect(calls[0]?.init?.method).toBe('GET')
    expect(calls[0]?.init?.redirect).toBe('manual')
    expect(calls[0]?.init?.signal).toBeInstanceOf(AbortSignal)
  })

  it('a timeout is a failed probe naming the deadline, never a throw', async () => {
    const timedOut: FetchLike = () => Promise.reject(new DOMException('aborted', 'TimeoutError'))
    const probe = await probeEdge({ name: 'web', url: HEALTH }, timedOut, 4_000)
    expect(probe).toEqual({ ok: false, detail: 'no answer within 4000ms' })
  })

  it('a network error is a failed probe carrying the reason', async () => {
    const dead: FetchLike = () =>
      Promise.reject(new TypeError('getaddrinfo ENOTFOUND smd.services'))
    const probe = await probeEdge({ name: 'web', url: HEALTH }, dead, 10_000)
    expect(probe).toEqual({ ok: false, detail: 'fetch failed: getaddrinfo ENOTFOUND smd.services' })
  })
})

describe('evaluateEdgePoll (pure counting)', () => {
  const bad = { ok: false, detail: 'HTTP 503, body status "error"' }
  const good = { ok: true, detail: 'HTTP 200, body status "ok"' }

  it('holds through the first failures and goes active at the threshold', () => {
    let prior: EdgeCounters | null = null
    const verdicts: string[] = []
    for (let i = 0; i < 4; i++) {
      const r = evaluateEdgePoll(prior, bad, THRESHOLDS)
      verdicts.push(r.verdict)
      prior = r.counters
    }
    expect(verdicts).toEqual(['hold', 'hold', 'active', 'active'])
    expect(prior).toEqual({ consecutive_failures: 4, consecutive_successes: 0 })
  })

  it('one good probe resets the failure run: fail, fail, ok, fail never pages', () => {
    let prior: EdgeCounters | null = null
    const verdicts: string[] = []
    for (const probe of [bad, bad, good, bad, bad]) {
      const r = evaluateEdgePoll(prior, probe, THRESHOLDS)
      verdicts.push(r.verdict)
      prior = r.counters
    }
    expect(verdicts).not.toContain('active')
  })

  it('recovery needs its own run: the first good probe after an outage is a hold', () => {
    const down: EdgeCounters = { consecutive_failures: 5, consecutive_successes: 0 }
    const first = evaluateEdgePoll(down, good, THRESHOLDS)
    expect(first.verdict).toBe('hold')
    expect(first.counters).toEqual({ consecutive_failures: 0, consecutive_successes: 1 })
    const second = evaluateEdgePoll(first.counters, good, THRESHOLDS)
    expect(second.verdict).toBe('inactive')
  })

  it('a steady green edge pushes inactive every tick (a no-op transition, never a page)', () => {
    const steady: EdgeCounters = { consecutive_failures: 0, consecutive_successes: 400 }
    expect(evaluateEdgePoll(steady, good, THRESHOLDS).verdict).toBe('inactive')
  })
})

describe('runEdgePolls against the real migration', () => {
  it('three failed ticks open the condition once; the rows record the run and keep last_ok_at', async () => {
    const { d1, raw } = realDb()
    const e = env({}, d1)
    // A first good probe from a fresh store is one sample: a hold, not a recovery.
    const first = await runEdgePolls(e, NOW, ok)
    expect(first.conditions).toEqual([])
    expect(first.polls[0]).toMatchObject({ ok: true, verdict: 'hold', consecutive_successes: 1 })
    const okRow = raw
      .prepare('SELECT last_ok_at FROM edge_poll_state WHERE target = ?')
      .get('web') as {
      last_ok_at: string
    }
    expect(okRow.last_ok_at).toBe('2026-09-11T22:00:00.000Z')

    const seen: string[] = []
    for (let tick = 1; tick <= 3; tick++) {
      const out = await runEdgePolls(e, NOW + tick * 120_000, dbDown)
      seen.push(
        out.conditions.length === 0 ? 'hold' : out.conditions[0]?.active ? 'active' : 'inactive'
      )
    }
    expect(seen).toEqual(['hold', 'hold', 'active'])

    const row = raw.prepare('SELECT * FROM edge_poll_state WHERE target = ?').get('web') as Record<
      string,
      unknown
    >
    expect(row.consecutive_failures).toBe(3)
    expect(row.consecutive_successes).toBe(0)
    expect(row.last_ok).toBe(0)
    expect(row.last_detail).toBe('HTTP 503, body status "error"')
    expect(row.last_checked_at).toBe('2026-09-11T22:06:00.000Z')
    // The failed probes did not erase when the edge last answered well.
    expect(row.last_ok_at).toBe('2026-09-11T22:00:00.000Z')
  })

  it('the active condition is keyed by target name, carries the condition and a runbook-shaped detail', async () => {
    const e = env({ EDGE_POLL_FAIL_THRESHOLD: '1' })
    const out = await runEdgePolls(e, NOW, dbDown)
    expect(out.conditions).toHaveLength(1)
    const c = out.conditions[0]
    expect(c?.customer_slug).toBe('web')
    expect(c?.condition).toBe(EDGE_DOWN_CONDITION)
    expect(c?.active).toBe(true)
    expect(c?.detail).toContain(`1 consecutive failed probes of ${HEALTH}`)
    expect(c?.detail).toContain('HTTP 503, body status "error"')
    expect(c?.detail).toContain('Resolves after 2 consecutive good probes')
    expect(out.polls[0]).toMatchObject({
      target: 'web',
      ok: false,
      verdict: 'active',
      consecutive_failures: 1,
    })
  })

  it('no targets configured: probes nothing, pushes nothing', async () => {
    let called = 0
    const counting: FetchLike = (url, init) => {
      called++
      return ok(url, init)
    }
    const out = await runEdgePolls(env({ EDGE_POLL_TARGETS: '' }), NOW, counting)
    expect(out).toEqual({ conditions: [], polls: [] })
    expect(called).toBe(0)
  })

  it('an unmigrated store is fail-soft: the probe runs, nothing is pushed, nothing throws', async () => {
    // No edge_poll_state table at all: every statement rejects.
    const d1 = {
      prepare(sql: string) {
        return {
          bind: () => ({
            first: () => Promise.reject(new Error(`no such table: ${sql.slice(0, 20)}`)),
            run: () => Promise.reject(new Error('no such table')),
            all: () => Promise.reject(new Error('no such table')),
          }),
        }
      },
    }
    const out = await runEdgePolls(env({}, d1 as unknown as D1Database), NOW, dbDown)
    expect(out).toEqual({ conditions: [], polls: [] })
  })
})

describe('retiring targets that leave the config', () => {
  function openAlert(raw: DatabaseSync, slug: string): void {
    raw
      .prepare(
        `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at) VALUES (?, ?, 'open', ?)`
      )
      .run(slug, EDGE_DOWN_CONDITION, '2026-09-11T23:46:36Z')
  }
  function targets(raw: DatabaseSync): string[] {
    return (
      raw.prepare('SELECT target FROM edge_poll_state ORDER BY target').all() as {
        target: string
      }[]
    ).map((r) => r.target)
  }

  it('a removed target loses its counters, and its open alert is pushed inactive with the reason', async () => {
    const { d1, raw } = realDb()
    // Two targets probed once each, then `probe` leaves the config while down.
    await runEdgePolls(
      env({ EDGE_POLL_TARGETS: `web=${HEALTH},probe=${HEALTH}x` }, d1),
      NOW,
      dbDown
    )
    expect(targets(raw)).toEqual(['probe', 'web'])
    openAlert(raw, 'probe')

    const out = await retireUnconfiguredTargets(d1, new Set(['web']))
    expect(out.retired).toEqual(['probe'])
    expect(targets(raw)).toEqual(['web'])
    expect(out.conditions).toEqual([
      {
        customer_slug: 'probe',
        condition: EDGE_DOWN_CONDITION,
        active: false,
        detail: expect.stringContaining('probe was removed from EDGE_POLL_TARGETS'),
      },
    ])
  })

  it('configured targets, resolved rows, and other conditions are untouched', async () => {
    const { d1, raw } = realDb()
    await runEdgePolls(env({}, d1), NOW, ok)
    openAlert(raw, 'web')
    raw
      .prepare(
        `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at) VALUES ('old', 'edge_down', 'resolved', 'x')`
      )
      .run()
    raw
      .prepare(
        `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at) VALUES ('seat', 'heartbeat_red', 'open', 'x')`
      )
      .run()
    const out = await retireUnconfiguredTargets(d1, new Set(['web']))
    expect(out).toEqual({ conditions: [], retired: [] })
    expect(targets(raw)).toEqual(['web'])
  })

  it('runEdgePolls retires on the tick the target disappears; the transition machinery gets the inactive state', async () => {
    const { d1, raw } = realDb()
    await runEdgePolls(
      env({ EDGE_POLL_TARGETS: `web=${HEALTH},probe=${HEALTH}x` }, d1),
      NOW,
      dbDown
    )
    openAlert(raw, 'probe')
    const out = await runEdgePolls(env({}, d1), NOW + 120_000, ok)
    expect(out.polls.map((p) => p.target)).toEqual(['web'])
    expect(out.conditions).toEqual([
      expect.objectContaining({
        customer_slug: 'probe',
        condition: EDGE_DOWN_CONDITION,
        active: false,
      }),
    ])
    expect(targets(raw)).toEqual(['web'])
  })

  it('a config with a parse error retires nothing: a typo must not resolve a real outage', async () => {
    const { d1, raw } = realDb()
    await runEdgePolls(
      env({ EDGE_POLL_TARGETS: `web=${HEALTH},probe=${HEALTH}x` }, d1),
      NOW,
      dbDown
    )
    openAlert(raw, 'probe')
    // `probe` is now malformed rather than absent; `web` still parses.
    const out = await runEdgePolls(
      env({ EDGE_POLL_TARGETS: `web=${HEALTH},probe=http://insecure` }, d1),
      NOW + 120_000,
      ok
    )
    expect(out.polls.map((p) => p.target)).toEqual(['web'])
    expect(out.conditions).toEqual([])
    expect(targets(raw)).toEqual(['probe', 'web'])
  })

  it('an empty config polls nothing and forgets everything, so unset means gone', async () => {
    const { d1, raw } = realDb()
    await runEdgePolls(env({}, d1), NOW, dbDown)
    expect(targets(raw)).toEqual(['web'])
    const out = await runEdgePolls(env({ EDGE_POLL_TARGETS: '' }, d1), NOW + 120_000, ok)
    expect(out).toEqual({ conditions: [], polls: [] })
    expect(targets(raw)).toEqual([])
  })
})
