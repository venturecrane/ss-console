/**
 * Tests for the fleet-alerts Worker (#1709): pure condition evaluation and the
 * edge-triggered transition machinery (one alert per open, one recovery per
 * close, silence otherwise), exercised through runOnce with a fake D1 and a
 * stubbed Resend.
 *
 * WP-1 adds the two scheduler conditions (scheduler_error, work_overdue) with
 * per-field NULL-hold, the send-only-marks-on-success delivery fix, per-seat
 * isolation, the watcher self-ping, and the stale_holds surface.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  conditionLabel,
  evaluateConditions,
  runOnce,
  type Env,
  type FleetStatusRow,
  type StaleHold,
} from './index'

const NOW = Date.parse('2026-07-04T12:00:00.000Z')
const RED = 300
const OVERDUE = 900

function row(overrides: Partial<FleetStatusRow>): FleetStatusRow {
  return {
    customer_slug: 'smd',
    last_heartbeat_ts: '2026-07-04T11:59:00.000Z', // 60s ago = green
    sticky_stop_level: 'OK',
    sticky_stop_reason: null,
    sticky_stop_condition: null,
    scheduler_ok: null,
    scheduler_max_overdue_seconds: null,
    connectors_json: null,
    connector_check_ok: null,
    connector_token_age_json: null,
    spec_control_json: null,
    spec_control_ok: null,
    webhook_surface_json: null,
    webhook_surface_ok: null,
    gateway_loop_ok: null,
    gateway_loop_age_seconds: null,
    gateway_supervisor_state: null,
    gateway_restarts_last_hour: null,
    ...overrides,
  }
}

describe('evaluateConditions', () => {
  it('fresh heartbeat + OK breaker: both base conditions inactive', () => {
    const out = evaluateConditions([row({})], NOW, RED, { overdueThresholdSeconds: OVERDUE })
    expect(out.find((c) => c.condition === 'heartbeat_red')?.active).toBe(false)
    expect(out.find((c) => c.condition === 'hard_stop')?.active).toBe(false)
  })

  it('stale heartbeat past threshold is red', () => {
    const out = evaluateConditions(
      [row({ last_heartbeat_ts: '2026-07-04T11:50:00.000Z' })], // 600s ago
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    expect(out.find((c) => c.condition === 'heartbeat_red')?.active).toBe(true)
  })

  it('NULL heartbeat is provisioning-gray, never red (no false pages)', () => {
    const out = evaluateConditions([row({ last_heartbeat_ts: null })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.find((c) => c.condition === 'heartbeat_red')?.active).toBe(false)
  })

  it('unparseable heartbeat timestamp IS a fault (red)', () => {
    const out = evaluateConditions([row({ last_heartbeat_ts: 'garbage' })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.find((c) => c.condition === 'heartbeat_red')?.active).toBe(true)
  })

  it('HARD_STOP is active; WARN/SOFT_STOP/null are not', () => {
    for (const [level, want] of [
      ['HARD_STOP', true],
      ['WARN', false],
      ['SOFT_STOP', false],
      [null, false],
    ] as const) {
      const out = evaluateConditions([row({ sticky_stop_level: level })], NOW, RED, {
        overdueThresholdSeconds: OVERDUE,
      })
      expect(out.find((c) => c.condition === 'hard_stop')?.active).toBe(want)
    }
  })

  it('the hard_stop detail carries the cause the seat recorded', () => {
    const out = evaluateConditions(
      [
        row({
          sticky_stop_level: 'HARD_STOP',
          sticky_stop_condition: 'consecutive_tool_failures',
          sticky_stop_reason:
            'consecutive_tool_failures=8 (window=600s, skill=mcp_smokeball_list_matters)',
        }),
      ],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    const detail = out.find((c) => c.condition === 'hard_stop')?.detail ?? ''
    expect(detail).toContain('sticky_stop_level=HARD_STOP')
    expect(detail).toContain('condition=consecutive_tool_failures')
    // The operative half: the page must name the failing skill, or the reader
    // goes to the seat to find it (which is what happened on 2026-09-01).
    expect(detail).toContain('skill=mcp_smokeball_list_matters')
    // And hand them the way back: the same incident's responder cleared the
    // stop by raw sqlite because nothing in their path named the built surface.
    expect(detail).toContain('clear: admin.smd.services/admin/operator/smd')
    expect(detail).toContain('runbook docs/runbooks/operator/sticky-stop-clear.md')
  })

  it('the hard_stop detail degrades to the level alone on a pre-cause seat', () => {
    // A seat not yet reprovisioned onto the cause-carrying overlay. The line
    // must not claim a cause it does not have - but it still points at the
    // clear surface, which is true of every seat.
    const out = evaluateConditions([row({ sticky_stop_level: 'HARD_STOP' })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    const detail = out.find((c) => c.condition === 'hard_stop')?.detail ?? ''
    expect(detail).toBe(
      'sticky_stop_level=HARD_STOP | clear: admin.smd.services/admin/operator/smd ' +
        '(runbook docs/runbooks/operator/sticky-stop-clear.md)'
    )
    expect(detail).not.toContain('condition=')
  })

  it('the hard_stop label names no meter', () => {
    // Four meters drive this ladder; naming one in the subject asserts a cause
    // the condition never measured. Regression guard for the 2026-09-01
    // "Cost breaker HARD_STOP" page on a credential failure.
    const label = conditionLabel('hard_stop')
    for (const meter of ['Cost', 'cost', 'refusal', 'tool failure', 'runtime']) {
      expect(label).not.toContain(meter)
    }
    expect(label).toContain('HARD_STOP')
  })

  // --- scheduler conditions + per-field NULL-hold ---------------------------

  it('scheduler_ok=0 makes scheduler_error active', () => {
    const out = evaluateConditions([row({ scheduler_ok: 0 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.find((c) => c.condition === 'scheduler_error')?.active).toBe(true)
  })

  it('scheduler_ok=1 makes scheduler_error inactive', () => {
    const out = evaluateConditions([row({ scheduler_ok: 1 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.find((c) => c.condition === 'scheduler_error')?.active).toBe(false)
  })

  it('scheduler_ok=NULL pushes NO scheduler_error ConditionState (hold, never resolve)', () => {
    const out = evaluateConditions([row({ scheduler_ok: null })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.find((c) => c.condition === 'scheduler_error')).toBeUndefined()
  })

  it('overdue 901 > threshold 900 opens work_overdue; 899 does not', () => {
    const overdue = evaluateConditions([row({ scheduler_max_overdue_seconds: 901 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(overdue.find((c) => c.condition === 'work_overdue')?.active).toBe(true)
    const notOverdue = evaluateConditions([row({ scheduler_max_overdue_seconds: 899 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(notOverdue.find((c) => c.condition === 'work_overdue')?.active).toBe(false)
  })

  it('overdue=NULL pushes NO work_overdue ConditionState even when scheduler_ok=1', () => {
    const out = evaluateConditions(
      [row({ scheduler_ok: 1, scheduler_max_overdue_seconds: null })],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    expect(out.find((c) => c.condition === 'scheduler_error')?.active).toBe(false)
    expect(out.find((c) => c.condition === 'work_overdue')).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// runOnce edge-trigger behavior with a fake D1 + stubbed Resend
// ---------------------------------------------------------------------------

interface FakeSinkRow {
  rowid: number
  customer_slug: string
  source: string
  summary: string | null
  alert_date: string
  driver: string
  entity_id: string
  notified_at: string | null
}

interface FakeState {
  fleet: FleetStatusRow[]
  alertState: Map<string, 'open' | 'resolved'>
  writes: string[]
  /** Slugs for which any DB op should throw, to exercise per-seat isolation. */
  throwForSlug?: Set<string>
  /** Alert-sink rows (migration 0095). Absent = empty sink. */
  sink?: FakeSinkRow[]
  /** Set to make the sink SELECT throw, to prove the pager survives it. */
  sinkQueryThrows?: boolean
}

function sinkRow(over: Partial<FakeSinkRow> = {}): FakeSinkRow {
  return {
    rowid: 1,
    customer_slug: 'pilot-smokeball',
    source: 'sentry',
    summary: 'Sentry SMD-OPERATOR-15: RuntimeError',
    alert_date: '2026-07-25',
    driver: '',
    entity_id: 'ent_1',
    notified_at: null,
    ...over,
  }
}

function computeStaleHolds(state: FakeState): StaleHold[] {
  const fleetBySlug = new Map(state.fleet.map((r) => [r.customer_slug, r]))
  const out: StaleHold[] = []
  for (const [key, status] of state.alertState) {
    if (status !== 'open') continue
    // Prefix conditions carry their own ':', so rejoin everything after the slug.
    const [slug, ...conditionParts] = key.split(':')
    const condition = conditionParts.join(':')
    const f = fleetBySlug.get(slug)
    const held =
      !f ||
      (condition === 'scheduler_error' && f.scheduler_ok === null) ||
      (condition === 'work_overdue' && f.scheduler_max_overdue_seconds === null) ||
      (condition === 'heartbeat_red' && f.last_heartbeat_ts === null) ||
      (condition === 'hard_stop' && f.sticky_stop_level === null) ||
      (condition === 'spec_control_unprovable' && f.spec_control_ok === null) ||
      // ss#2234: only a whole-map NULL strands a spec_control_broken key. A key
      // that merely vanishes is a WITHDRAWN declaration, which auto-resolves
      // through openSpecControlKeys — mirroring getStaleHolds' own comment.
      (condition.startsWith('spec_control_broken:') && f.spec_control_json === null) ||
      (condition === 'webhook_surface_unprovable' && f.webhook_surface_ok === null) ||
      // ss#2287, mirroring the spec_control_broken clause above.
      (condition.startsWith('webhook_surface_missing:') && f.webhook_surface_json === null)
    if (held) out.push({ customer_slug: slug, condition: condition as StaleHold['condition'] })
  }
  return out.sort(
    (a, b) =>
      a.customer_slug.localeCompare(b.customer_slug) || a.condition.localeCompare(b.condition)
  )
}

/**
 * Open `<prefix><payload>` rows fed back so a WITHDRAWN declaration/expectation
 * can resolve (ss#2234 spec-control, ss#2287 webhook-surface). Since ss#2316
 * both queries bind their prefix rather than inlining it, so the fake selects on
 * the BOUND VALUE — which is also what keeps this helper honest under a rename.
 */
function openKeysForPrefix(state: FakeState, prefix: string) {
  return [...state.alertState.entries()]
    .filter(([key, status]) => status === 'open' && key.includes(`:${prefix}`))
    .map(([key]) => {
      const [customer_slug, ...rest] = key.split(':')
      return { customer_slug, condition: rest.join(':') }
    })
}

function makeEnv(state: FakeState, withResend = true, extra: Partial<Env> = {}): Env {
  const db = {
    prepare(sql: string) {
      return {
        all() {
          if (sql.includes('FROM fleet_status')) {
            return Promise.resolve({ results: state.fleet })
          }
          throw new Error(`unexpected all(): ${sql}`)
        },
        bind(...args: unknown[]) {
          const key = `${args[0]}:${args[1]}`
          const slug = String(args[0])
          if (state.throwForSlug?.has(slug)) {
            const boom = () => {
              throw new Error(`boom for ${slug}`)
            }
            return { first: boom, run: boom, all: boom }
          }
          return {
            all() {
              // getStaleHolds (ss#2316: now bound — the prefixes travel as
              // parameters). NOTE: this returns a TypeScript reimplementation,
              // NOT the query. The SQL itself is exercised against real SQLite
              // in stale-holds.test.ts; assertions here cannot see it.
              if (sql.includes('LEFT JOIN fleet_status')) {
                return Promise.resolve({ results: computeStaleHolds(state) })
              }
              if (sql.includes("condition LIKE ? || '%'")) {
                return Promise.resolve({ results: openKeysForPrefix(state, String(args[0])) })
              }
              if (!sql.includes('FROM cost_anomaly_alerts')) {
                throw new Error(`unexpected bound all(): ${sql}`)
              }
              if (state.sinkQueryThrows) throw new Error('sink query boom')
              const limit = Number(args[0])
              const results = (state.sink ?? [])
                .filter((r) => r.notified_at === null && r.source !== 'cost')
                .slice(0, limit)
              return Promise.resolve({ results })
            },
            first() {
              if (!sql.includes('FROM fleet_alert_state')) {
                throw new Error(`unexpected first(): ${sql}`)
              }
              const status = state.alertState.get(key)
              return Promise.resolve(status ? { status } : null)
            },
            run() {
              if (sql.includes('INSERT INTO fleet_alert_state')) {
                state.alertState.set(key, 'open')
                state.writes.push(`open:${key}`)
              } else if (sql.includes("SET status = 'resolved'")) {
                state.alertState.set(key, 'resolved')
                state.writes.push(`resolve:${key}`)
              } else if (sql.includes('UPDATE cost_anomaly_alerts')) {
                const target = (state.sink ?? []).find((r) => r.rowid === Number(args[0]))
                if (target) target.notified_at = '2026-07-25T00:00:00Z'
                state.writes.push(`notify:${args[0]}`)
              } else {
                throw new Error(`unexpected run(): ${sql}`)
              }
              return Promise.resolve({})
            },
          }
        },
      }
    },
  }
  return {
    DB: db as unknown as D1Database,
    RESEND_API_KEY: withResend ? 'rk_test' : undefined,
    HEARTBEAT_RED_SECONDS: String(RED),
    WORK_OVERDUE_RED_SECONDS: String(OVERDUE),
    ...extra,
  }
}

function stubResend(): ReturnType<typeof vi.fn> {
  const mock = vi
    .fn()
    // mockImplementation, NOT mockResolvedValue: a Response body can only be
    // read once, so a single shared instance makes every send after the first
    // throw on .json() and silently record as failed.
    .mockImplementation(
      async () => new Response(JSON.stringify({ id: 'resend-alert-1' }), { status: 200 })
    )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('runOnce edge triggering', () => {
  it('red seat with no prior state: opens once, emails once', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({ last_heartbeat_ts: '2026-07-04T11:00:00.000Z' })],
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(1)
    expect(summary.transitions[0]).toMatchObject({
      condition: 'heartbeat_red',
      kind: 'opened',
      emailed: true,
      resendId: 'resend-alert-1',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body))
    expect(body.to).toBe('team@smd.services')
    expect(body.subject).toContain('ALERT smd')
  })

  it('still-red seat with an open alert: SILENT (no storm)', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({ last_heartbeat_ts: '2026-07-04T11:00:00.000Z' })],
      alertState: new Map([['smd:heartbeat_red', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(state.writes).toHaveLength(0)
  })

  it('green seat with an open alert: resolves once with a recovery email', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({})], // fresh heartbeat
      alertState: new Map([['smd:heartbeat_red', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(1)
    expect(summary.transitions[0].kind).toBe('resolved')
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body))
    expect(body.subject).toContain('RECOVERED smd')
    expect(state.alertState.get('smd:heartbeat_red')).toBe('resolved')
  })

  it('HARD_STOP opens its own condition independently of the heartbeat', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ sticky_stop_level: 'HARD_STOP' })], // heartbeat fresh
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(1)
    expect(summary.transitions[0]).toMatchObject({ condition: 'hard_stop', kind: 'opened' })
  })
})

describe('runOnce scheduler conditions', () => {
  it('scheduler_ok=0 opens scheduler_error + emails', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 0 })],
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toEqual([
      expect.objectContaining({ condition: 'scheduler_error', kind: 'opened', emailed: true }),
    ])
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body))
    expect(body.subject).toContain('ALERT smd: Cron scheduler broken/unreadable')
    expect(state.alertState.get('smd:scheduler_error')).toBe('open')
  })

  it('scheduler_ok 0 -> 1 resolves scheduler_error + RECOVERED email', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 1 })],
      alertState: new Map([['smd:scheduler_error', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toEqual([
      expect.objectContaining({ condition: 'scheduler_error', kind: 'resolved' }),
    ])
    const body = JSON.parse(String(fetchMock.mock.calls[0][1].body))
    expect(body.subject).toContain('RECOVERED smd: Cron scheduler broken/unreadable')
    expect(state.alertState.get('smd:scheduler_error')).toBe('resolved')
  })

  it('scheduler_ok=NULL does NOT resolve a pre-seeded open scheduler_error (hold)', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: null })],
      alertState: new Map([['smd:scheduler_error', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(state.alertState.get('smd:scheduler_error')).toBe('open')
  })

  it('overdue 901 opens work_overdue', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 1, scheduler_max_overdue_seconds: 901 })],
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toEqual([
      expect.objectContaining({ condition: 'work_overdue', kind: 'opened' }),
    ])
  })

  it('overdue 899 does NOT open work_overdue', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 1, scheduler_max_overdue_seconds: 899 })],
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.transitions).toHaveLength(0)
  })

  it('overdue NULL + scheduler_ok=1 → scheduler_error only (no work_overdue state at all)', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 1, scheduler_max_overdue_seconds: null })],
      alertState: new Map([['smd:work_overdue', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    // No false RECOVERED for the held work_overdue, and scheduler_error stays quiet.
    expect(summary.transitions).toHaveLength(0)
    expect(state.alertState.get('smd:work_overdue')).toBe('open')
  })
})

describe('runOnce delivery + resilience', () => {
  it('send-failure leaves state UNMARKED so the next run retries and sends', async () => {
    // Run 1: Resend returns 500 → no state change, no transition recorded.
    const failMock = vi.fn().mockResolvedValue(new Response('resend down', { status: 500 }))
    vi.stubGlobal('fetch', failMock)
    const state: FakeState = {
      fleet: [row({ last_heartbeat_ts: '2026-07-04T11:00:00.000Z' })],
      alertState: new Map(),
      writes: [],
    }
    const first = await runOnce(makeEnv(state), NOW)
    expect(first.transitions).toHaveLength(0)
    expect(state.alertState.get('smd:heartbeat_red')).toBeUndefined()
    expect(state.writes).toHaveLength(0)

    // Run 2: Resend recovers → the alert opens and emails (natural retry).
    vi.unstubAllGlobals()
    const okMock = stubResend()
    const second = await runOnce(makeEnv(state), NOW)
    expect(second.transitions).toEqual([
      expect.objectContaining({ condition: 'heartbeat_red', kind: 'opened', emailed: true }),
    ])
    expect(okMock).toHaveBeenCalledTimes(1)
    expect(state.alertState.get('smd:heartbeat_red')).toBe('open')
  })

  it('no RESEND_API_KEY: state is NOT marked (would-be email never sent)', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const state: FakeState = {
      fleet: [row({ last_heartbeat_ts: '2026-07-04T11:00:00.000Z' })],
      alertState: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state, false), NOW)
    expect(summary.transitions).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(state.alertState.get('smd:heartbeat_red')).toBeUndefined()
  })

  it('one throwing seat does not abort evaluation of the others', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [
        row({ customer_slug: 'bad', last_heartbeat_ts: '2026-07-04T11:00:00.000Z' }),
        row({ customer_slug: 'good', last_heartbeat_ts: '2026-07-04T11:00:00.000Z' }),
      ],
      alertState: new Map(),
      writes: [],
      throwForSlug: new Set(['bad']),
    }
    const summary = await runOnce(makeEnv(state), NOW)
    // 'good' still opened + emailed despite 'bad' throwing.
    expect(summary.transitions).toEqual([
      expect.objectContaining({ customer_slug: 'good', condition: 'heartbeat_red' }),
    ])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(state.alertState.get('good:heartbeat_red')).toBe('open')
  })

  it('fires the watcher self-ping when ALERTER_HEALTHCHECKS_PING_URL is set', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: [row({})], // nothing to alert on
      alertState: new Map(),
      writes: [],
    }
    await runOnce(
      makeEnv(state, true, { ALERTER_HEALTHCHECKS_PING_URL: 'https://hc.example/ping' }),
      NOW
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe('https://hc.example/ping')
  })
})

describe('runOnce stale_holds surface', () => {
  it('lists an open alert whose seat has no fleet_status row (the orphan case)', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [], // pilot-smokeball has no row post-rekey
      alertState: new Map([['pilot-smokeball:heartbeat_red', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.stale_holds).toEqual([
      { customer_slug: 'pilot-smokeball', condition: 'heartbeat_red' },
    ])
  })

  it('lists a scheduler_error open row whose seat now reports scheduler_ok=NULL', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: null })],
      alertState: new Map([['smd:scheduler_error', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.stale_holds).toEqual([{ customer_slug: 'smd', condition: 'scheduler_error' }])
  })

  it('does not list an open alert whose field is populated', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ scheduler_ok: 0 })],
      alertState: new Map([['smd:scheduler_error', 'open']]),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.stale_holds).toHaveLength(0)
  })
})

describe('connector conditions (ADR 0080)', () => {
  const entry = (over: Record<string, unknown> = {}) =>
    JSON.stringify({
      smokeball: {
        consecutive_failures: 4,
        run_age_seconds: 400,
        conn_evidence: true,
        last_ok_age_seconds: 900,
        last_error_message: 'Smokeball GET /matters -> HTTP 401: (empty body)',
        ...over,
      },
    })

  it('NULL connectors_json pushes no connector_down state at all (whole-map hold)', () => {
    const out = evaluateConditions([row({})], NOW, RED, { overdueThresholdSeconds: OVERDUE })
    expect(out.some((c) => c.condition.startsWith('connector_down:'))).toBe(false)
  })

  it('conn-class path opens: >=3 consecutive with evidence and run age >= threshold', () => {
    const out = evaluateConditions([row({ connectors_json: entry() })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      connectorRunAgeThresholdSeconds: 300,
    })
    const c = out.find((x) => x.condition === 'connector_down:smokeball')
    expect(c?.active).toBe(true)
    expect(c?.detail).toContain('connection-class evidence')
    expect(c?.detail).toContain('HTTP 401')
  })

  it('a young run holds even with count + evidence (burst suppression)', () => {
    const out = evaluateConditions(
      [row({ connectors_json: entry({ run_age_seconds: 120 }) })],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE, connectorRunAgeThresholdSeconds: 300 }
    )
    expect(out.some((x) => x.condition === 'connector_down:smokeball')).toBe(false)
  })

  it('business-only run never opens via the conn path, opens via the backstop at 10/900', () => {
    const noEvidence = { conn_evidence: false, consecutive_failures: 9, run_age_seconds: 5000 }
    const held = evaluateConditions([row({ connectors_json: entry(noEvidence) })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      connectorRunAgeThresholdSeconds: 300,
    })
    expect(held.some((x) => x.condition === 'connector_down:smokeball')).toBe(false)

    const backstop = { conn_evidence: false, consecutive_failures: 10, run_age_seconds: 900 }
    const paged = evaluateConditions([row({ connectors_json: entry(backstop) })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      connectorRunAgeThresholdSeconds: 300,
    })
    const c = paged.find((x) => x.condition === 'connector_down:smokeball')
    expect(c?.active).toBe(true)
    expect(c?.detail).toContain('signature-free backstop')
  })

  it('count 0 pushes inactive (resolves); counts 1-2 push nothing (ambiguous hold)', () => {
    const resolved = evaluateConditions(
      [row({ connectors_json: JSON.stringify({ smokeball: { consecutive_failures: 0 } }) })],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    expect(resolved.find((x) => x.condition === 'connector_down:smokeball')?.active).toBe(false)

    const ambiguous = evaluateConditions(
      [
        row({
          connectors_json: JSON.stringify({
            smokeball: { consecutive_failures: 2, run_age_seconds: 4000, conn_evidence: true },
          }),
        }),
      ],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    expect(ambiguous.some((x) => x.condition === 'connector_down:smokeball')).toBe(false)
  })

  it('a failing run missing run_age_seconds is dropped (age-gated conditions need an age)', () => {
    const out = evaluateConditions(
      [row({ connectors_json: JSON.stringify({ smokeball: { consecutive_failures: 7 } }) })],
      NOW,
      RED,
      { overdueThresholdSeconds: OVERDUE }
    )
    expect(out.some((x) => x.condition === 'connector_down:smokeball')).toBe(false)
  })

  it('corrupt connectors_json degrades to a hold, never throws', () => {
    const out = evaluateConditions([row({ connectors_json: '{nope' })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(out.some((x) => x.condition.startsWith('connector_down:'))).toBe(false)
  })

  it('servers are independent: one down, one healthy in the same map', () => {
    const map = JSON.stringify({
      smokeball: { consecutive_failures: 4, run_age_seconds: 400, conn_evidence: true },
      agentmail: { consecutive_failures: 0 },
    })
    const out = evaluateConditions([row({ connectors_json: map })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      connectorRunAgeThresholdSeconds: 300,
    })
    expect(out.find((x) => x.condition === 'connector_down:smokeball')?.active).toBe(true)
    expect(out.find((x) => x.condition === 'connector_down:agentmail')?.active).toBe(false)
  })

  it('connector_check_error follows scheduler_ok semantics with NULL-hold', () => {
    const held = evaluateConditions([row({})], NOW, RED, { overdueThresholdSeconds: OVERDUE })
    expect(held.some((x) => x.condition === 'connector_check_error')).toBe(false)

    const broken = evaluateConditions([row({ connector_check_ok: 0 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(broken.find((x) => x.condition === 'connector_check_error')?.active).toBe(true)

    const healthy = evaluateConditions([row({ connector_check_ok: 1 })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
    })
    expect(healthy.find((x) => x.condition === 'connector_check_error')?.active).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Alert-sink delivery (migration 0095) — the push path Sentry rows lacked
// ---------------------------------------------------------------------------

describe('alert-sink notification', () => {
  const healthy = (): FleetStatusRow[] => [row({ last_heartbeat_ts: new Date(NOW).toISOString() })]

  it('emails an undelivered sentry row and marks it notified', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: healthy(),
      alertState: new Map(),
      writes: [],
      sink: [sinkRow()],
    }

    const summary = await runOnce(makeEnv(state), NOW)

    expect(summary.sink_notifications).toHaveLength(1)
    expect(summary.sink_notifications[0]).toMatchObject({
      customer_slug: 'pilot-smokeball',
      source: 'sentry',
      emailed: true,
    })
    expect(state.sink![0].notified_at).not.toBeNull()
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.to).toBe('team@smd.services')
    expect(body.subject).toContain('pilot-smokeball')
    expect(body.subject).toContain('Sentry issue alert')
  })

  it('delivers each row exactly once across runs', async () => {
    stubResend()
    const state: FakeState = {
      fleet: healthy(),
      alertState: new Map(),
      writes: [],
      sink: [sinkRow()],
    }

    const first = await runOnce(makeEnv(state), NOW)
    const second = await runOnce(makeEnv(state), NOW)

    expect(first.sink_notifications).toHaveLength(1)
    expect(second.sink_notifications).toHaveLength(0)
  })

  it('does NOT mark notified when the send fails, so the next run retries', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => new Response('nope', { status: 500 }))
    )
    const state: FakeState = {
      fleet: healthy(),
      alertState: new Map(),
      writes: [],
      sink: [sinkRow()],
    }

    const summary = await runOnce(makeEnv(state), NOW)

    expect(summary.sink_notifications[0].emailed).toBe(false)
    expect(state.sink![0].notified_at).toBeNull()
    expect(state.writes).not.toContain('notify:1')
  })

  it('ignores cost rows — the cost worker already emails those', async () => {
    stubResend()
    const state: FakeState = {
      fleet: healthy(),
      alertState: new Map(),
      writes: [],
      sink: [sinkRow({ rowid: 7, source: 'cost', summary: null })],
    }

    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.sink_notifications).toHaveLength(0)
  })

  it('batches: at most SINK_NOTIFY_BATCH per run, remainder deferred not dropped', async () => {
    stubResend()
    const sink = Array.from({ length: 14 }, (_, i) => sinkRow({ rowid: i + 1 }))
    const state: FakeState = { fleet: healthy(), alertState: new Map(), writes: [], sink }

    const first = await runOnce(makeEnv(state), NOW)
    const second = await runOnce(makeEnv(state), NOW)

    expect(first.sink_notifications).toHaveLength(10)
    expect(second.sink_notifications).toHaveLength(4)
    expect(sink.every((r) => r.notified_at !== null)).toBe(true)
  })

  it('a broken sink query never suppresses the fleet_status pager', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [row({ last_heartbeat_ts: '2026-07-04T11:00:00.000Z' })],
      alertState: new Map(),
      writes: [],
      sinkQueryThrows: true,
    }

    const summary = await runOnce(makeEnv(state), NOW)

    expect(summary.sink_notifications).toHaveLength(0)
    expect(summary.transitions.some((t) => t.condition === 'heartbeat_red')).toBe(true)
  })

  it('escapes HTML in sink summaries (they carry Machine exception text)', async () => {
    const fetchMock = stubResend()
    const state: FakeState = {
      fleet: healthy(),
      alertState: new Map(),
      writes: [],
      sink: [sinkRow({ summary: '<img src=x onerror="alert(1)">' })],
    }

    await runOnce(makeEnv(state), NOW)

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body.html).not.toContain('<img')
    expect(body.html).toContain('&lt;img')
  })
})

it('escapes HTML in transition details (connector errors are Machine-controlled)', async () => {
  const fetchMock = stubResend()
  const map = JSON.stringify({
    smokeball: {
      consecutive_failures: 4,
      run_age_seconds: 400,
      conn_evidence: true,
      last_error_message: '<script>alert(1)</script>',
    },
  })
  const state: FakeState = {
    fleet: [row({ connectors_json: map })],
    alertState: new Map(),
    writes: [],
  }

  await runOnce(makeEnv(state), NOW)

  const bodies = fetchMock.mock.calls.map((c) => JSON.parse(c[1].body as string).html as string)
  const connectorEmail = bodies.find((b) => b.includes('smokeball'))
  expect(connectorEmail).toBeDefined()
  expect(connectorEmail).not.toContain('<script>')
  expect(connectorEmail).toContain('&lt;script&gt;')
})

describe('connector_token_expiring (ss#2148)', () => {
  const LIFETIMES = { smokeball: 30 }
  const WARN = 5
  const ageJson = (days: number) => JSON.stringify({ smokeball: days * 86400 })
  const tokenStates = (r: FleetStatusRow) =>
    evaluateConditions([r], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      tokenLifetimesDays: LIFETIMES,
      tokenWarnDays: WARN,
    }).filter((c) => c.condition.startsWith('connector_token_expiring:'))

  it('NULL token-age json pushes nothing (hold)', () => {
    expect(tokenStates(row({}))).toHaveLength(0)
  })

  it('corrupt token-age json pushes nothing (hold, never a page from junk)', () => {
    expect(tokenStates(row({ connector_token_age_json: '{nope' }))).toHaveLength(0)
  })

  it('age below the warn threshold pushes inactive (rotation resolves an open alert)', () => {
    const out = tokenStates(row({ connector_token_age_json: ageJson(3) }))
    expect(out).toHaveLength(1)
    expect(out[0].condition).toBe('connector_token_expiring:smokeball')
    expect(out[0].active).toBe(false)
  })

  it('age at lifetime - warn opens the condition', () => {
    const out = tokenStates(row({ connector_token_age_json: ageJson(25) }))
    expect(out).toHaveLength(1)
    expect(out[0].active).toBe(true)
    expect(out[0].detail).toContain('25d old')
  })

  it('age past the lifetime stays open', () => {
    const out = tokenStates(row({ connector_token_age_json: ageJson(31) }))
    expect(out[0].active).toBe(true)
  })

  it('a server with no recorded lifetime is never evaluated (no guessed pages)', () => {
    const out = tokenStates(
      row({ connector_token_age_json: JSON.stringify({ agentmail: 999 * 86400 }) })
    )
    expect(out).toHaveLength(0)
  })

  it('no lifetimes configured disables the condition class entirely', () => {
    const out = evaluateConditions([row({ connector_token_age_json: ageJson(29) })], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      tokenWarnDays: WARN,
    }).filter((c) => c.condition.startsWith('connector_token_expiring:'))
    expect(out).toHaveLength(0)
  })

  it('labels the condition with the server name', () => {
    expect(conditionLabel('connector_token_expiring:smokeball')).toBe(
      'Connector credential expiring: smokeball'
    )
  })
})

describe('spec_control (ss#2234)', () => {
  const specJson = (entries: Record<string, { declared: boolean; installed: boolean }>) =>
    JSON.stringify(entries)
  const specStates = (r: FleetStatusRow, openKeys: string[] = []) =>
    evaluateConditions([r], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      openSpecControlKeys: openKeys.length ? { [r.customer_slug]: openKeys } : {},
    }).filter((c) => c.condition.startsWith('spec_control_'))

  it('NULL spec-control fields push nothing (hold)', () => {
    expect(specStates(row({}))).toHaveLength(0)
  })

  it('corrupt spec-control json pushes no per-key condition (hold, never a page from junk)', () => {
    const out = specStates(row({ spec_control_ok: 1, spec_control_json: '{nope' }))
    expect(out.filter((c) => c.condition.startsWith('spec_control_broken:'))).toHaveLength(0)
  })

  it('declared and installed pushes inactive (a landed spec resolves)', () => {
    const out = specStates(
      row({
        spec_control_ok: 1,
        spec_control_json: specJson({ 'staff.voice': { declared: true, installed: true } }),
      })
    )
    const broken = out.filter((c) => c.condition === 'spec_control_broken:staff.voice')
    expect(broken).toHaveLength(1)
    expect(broken[0].active).toBe(false)
    expect(broken[0].detail).toContain('spec installed')
  })

  it('declared and NOT installed opens the condition', () => {
    const out = specStates(
      row({
        spec_control_ok: 1,
        spec_control_json: specJson({ 'staff.voice': { declared: true, installed: false } }),
      })
    )
    const broken = out.filter((c) => c.condition === 'spec_control_broken:staff.voice')
    expect(broken[0].active).toBe(true)
    expect(broken[0].detail).toContain('none is installed')
  })

  it('keys are independent — one broken, one healthy on the same seat', () => {
    const out = specStates(
      row({
        spec_control_ok: 1,
        spec_control_json: specJson({
          'staff.voice': { declared: true, installed: true },
          'staff.format': { declared: true, installed: false },
        }),
      })
    )
    const byCondition = Object.fromEntries(out.map((c) => [c.condition, c.active]))
    expect(byCondition['spec_control_broken:staff.voice']).toBe(false)
    expect(byCondition['spec_control_broken:staff.format']).toBe(true)
  })

  it('an entry missing either flag is dropped, never defaulted', () => {
    // `installed` is what opens and closes the alert; inferring it would be
    // manufacturing the verdict.
    const out = specStates(
      row({
        spec_control_ok: 1,
        spec_control_json: JSON.stringify({ 'staff.voice': { declared: true } }),
      })
    )
    expect(out.filter((c) => c.condition.startsWith('spec_control_broken:'))).toHaveLength(0)
  })

  it('a WITHDRAWN declaration resolves its open alert and says which repair it was', () => {
    // The key vanishes from the map entirely when voice_spec flips to `none`,
    // so without openSpecControlKeys nothing would ever evaluate it again and
    // the alert would sit open forever.
    const out = specStates(row({ spec_control_ok: 1, spec_control_json: '{}' }), ['staff.voice'])
    const broken = out.filter((c) => c.condition === 'spec_control_broken:staff.voice')
    expect(broken).toHaveLength(1)
    expect(broken[0].active).toBe(false)
    expect(broken[0].detail).toContain('no longer declared')
  })

  it('ok=0 opens spec_control_unprovable and HOLDS every per-key condition', () => {
    // "We cannot look" must never be reported as the firm's missing spec, and
    // must not resolve keys from data the seat just said it cannot trust.
    const out = specStates(
      row({
        spec_control_ok: 0,
        spec_control_json: specJson({ 'staff.voice': { declared: true, installed: true } }),
      }),
      ['staff.voice']
    )
    expect(out).toHaveLength(1)
    expect(out[0].condition).toBe('spec_control_unprovable')
    expect(out[0].active).toBe(true)
  })

  it('ok=1 resolves spec_control_unprovable', () => {
    const out = specStates(row({ spec_control_ok: 1, spec_control_json: '{}' }))
    const unprovable = out.filter((c) => c.condition === 'spec_control_unprovable')
    expect(unprovable).toHaveLength(1)
    expect(unprovable[0].active).toBe(false)
  })

  it('ok NULL pushes no unprovable condition (a quiet seat has not recovered)', () => {
    const out = specStates(row({ spec_control_json: '{}' }))
    expect(out.filter((c) => c.condition === 'spec_control_unprovable')).toHaveLength(0)
  })

  it('labels both condition forms', () => {
    expect(conditionLabel('spec_control_broken:staff.voice')).toBe(
      'Authored spec declared but not installed: staff.voice'
    )
    expect(conditionLabel('spec_control_unprovable')).toBe(
      'Authored-spec manifest unreadable (spec health unknown)'
    )
  })

  it('runOnce opens once, stays silent while open, then resolves once when the spec lands', async () => {
    const state: FakeState = {
      fleet: [
        row({
          customer_slug: 'pilot-smokeball',
          spec_control_ok: 1,
          spec_control_json: specJson({ 'staff.voice': { declared: true, installed: false } }),
        }),
      ],
      alertState: new Map(),
      writes: [],
    }
    const env = makeEnv(state)
    const fetchMock = stubResend()

    await runOnce(env, NOW)
    const bodies = () => fetchMock.mock.calls.map((c) => String((c[1] as { body: string }).body))
    expect(bodies().some((b) => b.includes('ALERT') && b.includes('staff.voice'))).toBe(true)
    expect(state.writes).toContain('open:pilot-smokeball:spec_control_broken:staff.voice')

    // Still broken on the next tick: no second email.
    fetchMock.mockClear()
    await runOnce(env, NOW)
    expect(bodies().filter((b) => b.includes('staff.voice'))).toHaveLength(0)

    // The spec lands.
    fetchMock.mockClear()
    state.fleet = [
      row({
        customer_slug: 'pilot-smokeball',
        spec_control_ok: 1,
        spec_control_json: specJson({ 'staff.voice': { declared: true, installed: true } }),
      }),
    ]
    await runOnce(env, NOW)
    const recovered = bodies().filter((b) => b.includes('RECOVERED') && b.includes('staff.voice'))
    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toContain('spec installed')
  })
})

describe('webhook_surface (ss#2287 — the ss#2222 warn tier)', () => {
  const surfaceJson = (entries: Record<string, { expected: boolean; offered: boolean }>) =>
    JSON.stringify(entries)
  const surfaceStates = (r: FleetStatusRow, openTools: string[] = []) =>
    evaluateConditions([r], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      openWebhookSurfaceKeys: openTools.length ? { [r.customer_slug]: openTools } : {},
    }).filter((c) => c.condition.startsWith('webhook_surface_'))

  it('NULL webhook-surface fields push nothing (hold)', () => {
    // Covers the pre-ss#2222 overlay, a seat that serves no webhook platform,
    // and a sentinel written by a dead pid. None of those is a recovery.
    expect(surfaceStates(row({}))).toHaveLength(0)
  })

  it('corrupt map pushes no per-tool condition (hold, never a page from junk)', () => {
    const out = surfaceStates(row({ webhook_surface_ok: 1, webhook_surface_json: '{nope' }))
    expect(out.filter((c) => c.condition.startsWith('webhook_surface_missing:'))).toHaveLength(0)
  })

  it('expected and NOT offered opens the condition', () => {
    const out = surfaceStates(
      row({
        webhook_surface_ok: 1,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: true, offered: false },
        }),
      })
    )
    const missing = out.filter((c) => c.condition === 'webhook_surface_missing:operator_seat_facts')
    expect(missing).toHaveLength(1)
    expect(missing[0].active).toBe(true)
    expect(missing[0].detail).toContain('not offered')
  })

  it('expected and offered pushes inactive (a restored tool resolves)', () => {
    const out = surfaceStates(
      row({
        webhook_surface_ok: 1,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: true, offered: true },
        }),
      })
    )
    const missing = out.filter((c) => c.condition === 'webhook_surface_missing:operator_seat_facts')
    expect(missing[0].active).toBe(false)
    expect(missing[0].detail).toContain('offered on the webhook surface')
  })

  it('tools are independent — one missing, one offered on the same seat', () => {
    const out = surfaceStates(
      row({
        webhook_surface_ok: 1,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: true, offered: true },
          read_file: { expected: true, offered: false },
        }),
      })
    )
    const byCondition = Object.fromEntries(out.map((c) => [c.condition, c.active]))
    expect(byCondition['webhook_surface_missing:operator_seat_facts']).toBe(false)
    expect(byCondition['webhook_surface_missing:read_file']).toBe(true)
  })

  it('an entry missing either flag is dropped, never defaulted', () => {
    const out = surfaceStates(
      row({
        webhook_surface_ok: 1,
        webhook_surface_json: JSON.stringify({ operator_seat_facts: { expected: true } }),
      })
    )
    expect(out.filter((c) => c.condition.startsWith('webhook_surface_missing:'))).toHaveLength(0)
  })

  it('a WITHDRAWN expectation resolves its open alert and says which repair it was', () => {
    // The tool leaves WEBHOOK_EXPECTED_TOOLS: its key vanishes from the map, so
    // without openWebhookSurfaceKeys the alert would sit open forever.
    const out = surfaceStates(row({ webhook_surface_ok: 1, webhook_surface_json: '{}' }), [
      'operator_seat_facts',
    ])
    const missing = out.filter((c) => c.condition === 'webhook_surface_missing:operator_seat_facts')
    expect(missing).toHaveLength(1)
    expect(missing[0].active).toBe(false)
    expect(missing[0].detail).toContain('no longer expected')
  })

  it('expected:false resolves the same way a vanished key does', () => {
    const out = surfaceStates(
      row({
        webhook_surface_ok: 1,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: false, offered: false },
        }),
      })
    )
    const missing = out.filter((c) => c.condition === 'webhook_surface_missing:operator_seat_facts')
    expect(missing[0].active).toBe(false)
    expect(missing[0].detail).toContain('no longer expected')
  })

  it('ok=0 opens webhook_surface_unprovable and HOLDS every per-tool condition', () => {
    // "We cannot look" must never be reported as a missing tool, and must not
    // resolve tools from data the seat just said it cannot trust.
    const out = surfaceStates(
      row({
        webhook_surface_ok: 0,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: true, offered: true },
        }),
      }),
      ['operator_seat_facts']
    )
    expect(out).toHaveLength(1)
    expect(out[0].condition).toBe('webhook_surface_unprovable')
    expect(out[0].active).toBe(true)
  })

  it('ok=1 resolves webhook_surface_unprovable', () => {
    const out = surfaceStates(row({ webhook_surface_ok: 1, webhook_surface_json: '{}' }))
    const unprovable = out.filter((c) => c.condition === 'webhook_surface_unprovable')
    expect(unprovable).toHaveLength(1)
    expect(unprovable[0].active).toBe(false)
  })

  it('ok NULL pushes no unprovable condition (a quiet seat has not recovered)', () => {
    const out = surfaceStates(row({ webhook_surface_json: '{}' }))
    expect(out.filter((c) => c.condition === 'webhook_surface_unprovable')).toHaveLength(0)
  })

  it('labels both condition forms', () => {
    expect(conditionLabel('webhook_surface_missing:operator_seat_facts')).toBe(
      'Webhook tool expected but not offered: operator_seat_facts'
    )
    expect(conditionLabel('webhook_surface_unprovable')).toBe(
      'Webhook tool surface unresolvable (warn-tier health unknown)'
    )
  })

  it('runOnce opens once, stays silent while open, then resolves once when the tool returns', async () => {
    const state: FakeState = {
      fleet: [
        row({
          customer_slug: 'pilot-smokeball',
          webhook_surface_ok: 1,
          webhook_surface_json: surfaceJson({
            operator_seat_facts: { expected: true, offered: false },
          }),
        }),
      ],
      alertState: new Map(),
      writes: [],
    }
    const env = makeEnv(state)
    const fetchMock = stubResend()

    await runOnce(env, NOW)
    const bodies = () => fetchMock.mock.calls.map((c) => String((c[1] as { body: string }).body))
    expect(bodies().some((b) => b.includes('ALERT') && b.includes('operator_seat_facts'))).toBe(
      true
    )
    expect(state.writes).toContain(
      'open:pilot-smokeball:webhook_surface_missing:operator_seat_facts'
    )

    // Still missing on the next tick: no second email.
    fetchMock.mockClear()
    await runOnce(env, NOW)
    expect(bodies().filter((b) => b.includes('operator_seat_facts'))).toHaveLength(0)

    // The tool returns to the surface.
    fetchMock.mockClear()
    state.fleet = [
      row({
        customer_slug: 'pilot-smokeball',
        webhook_surface_ok: 1,
        webhook_surface_json: surfaceJson({
          operator_seat_facts: { expected: true, offered: true },
        }),
      }),
    ]
    await runOnce(env, NOW)
    const recovered = bodies().filter(
      (b) => b.includes('RECOVERED') && b.includes('operator_seat_facts')
    )
    expect(recovered).toHaveLength(1)
    expect(recovered[0]).toContain('offered on the webhook surface')
  })

  it('an open alert whose seat stops reporting the map surfaces as a stale hold', async () => {
    const state: FakeState = {
      fleet: [
        row({
          customer_slug: 'pilot-smokeball',
          webhook_surface_ok: 1,
          webhook_surface_json: surfaceJson({
            operator_seat_facts: { expected: true, offered: false },
          }),
        }),
      ],
      alertState: new Map(),
      writes: [],
    }
    const env = makeEnv(state)
    stubResend()
    await runOnce(env, NOW)

    // Overlay rollback: both fields go NULL. The alert holds (correct) and must
    // therefore appear in stale_holds so a human can clear it.
    state.fleet = [row({ customer_slug: 'pilot-smokeball' })]
    const summary = await runOnce(env, NOW)
    expect(summary.stale_holds).toContainEqual({
      customer_slug: 'pilot-smokeball',
      condition: 'webhook_surface_missing:operator_seat_facts',
    })
  })
})

describe('gateway loop + supervisor (ss#2488 part 2)', () => {
  const loopStates = (r: FleetStatusRow, red = 120) =>
    evaluateConditions([r], NOW, RED, {
      overdueThresholdSeconds: OVERDUE,
      gatewayLoopRedSeconds: red,
    }).filter((c) => c.condition.startsWith('gateway_'))
  const one = (r: FleetStatusRow, cond: string, red = 120) =>
    loopStates(r, red).filter((c) => c.condition === cond)

  it('all four fields NULL push nothing (hold)', () => {
    // A pre-part-2 overlay, a Hermes pin with no loop heartbeat, a seat without
    // the supervisor. None of those is a recovery and none is a page.
    expect(loopStates(row({}))).toHaveLength(0)
  })

  it('a row missing the columns entirely (undefined, pre-0107 read) pushes nothing', () => {
    // The critique's case: `undefined !== null` is TRUE, so a strict-null guard
    // would fall through to `=== 0` (false) and push active:false -- a false
    // RECOVERED. The guards use `== null` precisely so this holds.
    const r = row({}) as unknown as Record<string, unknown>
    delete r.gateway_loop_ok
    delete r.gateway_loop_age_seconds
    delete r.gateway_supervisor_state
    delete r.gateway_restarts_last_hour
    expect(loopStates(r as unknown as FleetStatusRow)).toHaveLength(0)
  })

  // -- gateway_loop_wedged ---------------------------------------------------
  it('ok=1, age past threshold opens wedged', () => {
    const out = one(
      row({ gateway_loop_ok: 1, gateway_loop_age_seconds: 400 }),
      'gateway_loop_wedged'
    )
    expect(out).toHaveLength(1)
    expect(out[0].active).toBe(true)
    expect(out[0].detail).toContain('400s')
  })

  it('ok=1, age under threshold resolves wedged', () => {
    const out = one(
      row({ gateway_loop_ok: 1, gateway_loop_age_seconds: 10 }),
      'gateway_loop_wedged'
    )
    expect(out).toHaveLength(1)
    expect(out[0].active).toBe(false)
  })

  it('ok=1 with a NULL age holds wedged (arming latch / boot suppression is not a verdict)', () => {
    // `null > 120` is false in JS. Without the both-present guard this would
    // RESOLVE an open wedge on a number nobody measured.
    expect(
      one(row({ gateway_loop_ok: 1, gateway_loop_age_seconds: null }), 'gateway_loop_wedged')
    ).toHaveLength(0)
  })

  it('ok=0 holds wedged and opens unprovable -- never resolves an open wedge on disowned data', () => {
    const out = loopStates(row({ gateway_loop_ok: 0, gateway_loop_age_seconds: 5 }))
    expect(out.filter((c) => c.condition === 'gateway_loop_wedged')).toHaveLength(0)
    const unp = out.filter((c) => c.condition === 'gateway_loop_unprovable')
    expect(unp).toHaveLength(1)
    expect(unp[0].active).toBe(true)
  })

  it('ok=1 resolves unprovable', () => {
    const out = one(
      row({ gateway_loop_ok: 1, gateway_loop_age_seconds: 5 }),
      'gateway_loop_unprovable'
    )
    expect(out).toHaveLength(1)
    expect(out[0].active).toBe(false)
  })

  it('threshold is honoured from options', () => {
    const r = row({ gateway_loop_ok: 1, gateway_loop_age_seconds: 200 })
    expect(one(r, 'gateway_loop_wedged', 300)[0].active).toBe(false)
    expect(one(r, 'gateway_loop_wedged', 120)[0].active).toBe(true)
  })

  // -- gateway_restarted -----------------------------------------------------
  it('restarts >= 1 opens restarted; 0 resolves it; NULL holds', () => {
    expect(one(row({ gateway_restarts_last_hour: 1 }), 'gateway_restarted')[0].active).toBe(true)
    expect(one(row({ gateway_restarts_last_hour: 3 }), 'gateway_restarted')[0].active).toBe(true)
    expect(one(row({ gateway_restarts_last_hour: 0 }), 'gateway_restarted')[0].active).toBe(false)
    expect(one(row({ gateway_restarts_last_hour: null }), 'gateway_restarted')).toHaveLength(0)
  })

  // -- supervisor state --------------------------------------------------------
  it('refusing opens refusing and resolves inert', () => {
    const out = loopStates(row({ gateway_supervisor_state: 'refusing' }))
    expect(out.find((c) => c.condition === 'gateway_supervisor_refusing')!.active).toBe(true)
    expect(out.find((c) => c.condition === 'gateway_supervisor_inert')!.active).toBe(false)
  })

  it('inert and not-watching both open inert, with distinct detail', () => {
    const a = one(row({ gateway_supervisor_state: 'inert' }), 'gateway_supervisor_inert')[0]
    const b = one(row({ gateway_supervisor_state: 'not-watching' }), 'gateway_supervisor_inert')[0]
    expect(a.active).toBe(true)
    expect(b.active).toBe(true)
    expect(a.detail).toContain('argv')
    expect(b.detail).toContain('no loop heartbeat')
  })

  it('never-healthy opens inert, and says a human is the only recovery path', () => {
    // The 2026-09-01 pilot-smokeball crash loop. A gateway that wedges DURING
    // startup never writes a first beat, so the seat supervisor never arms and
    // its state stayed `not-armed` -- which does not page, and must not, since
    // `not-armed` is also every healthy seat's first thirty seconds. The seat
    // restarted every ~15 minutes for two and a half hours and reached nobody.
    //
    // It shares gateway_supervisor_inert rather than adding a fourth condition
    // because `condition` carries a CHECK constraint (migrations 0107, 0109) --
    // a new name without a migration is a REJECTED row, i.e. a page that
    // silently never lands. The detail is where the three are told apart.
    const c = one(row({ gateway_supervisor_state: 'never-healthy' }), 'gateway_supervisor_inert')[0]
    expect(c.active).toBe(true)
    expect(c.detail).toContain('NEVER CAME UP')
    // The two halves an on-call needs: that nothing automatic follows, and that
    // it is not the supervisor's job to kill it.
    expect(c.detail).toMatch(/will NOT restart it/)
    expect(c.detail).toMatch(/needs a human/)
  })

  it('starting resolves both supervisor conditions — it is every healthy boot', () => {
    // Before the fix this window reported `inert` and paged on every boot: the
    // entrypoint forks the supervisor while still root, and bootstrap.sh runs
    // for minutes before its own gateway exec, so /proc/<main>/cmdline
    // legitimately names no hermes for that whole time.
    const out = loopStates(row({ gateway_supervisor_state: 'starting' }))
    expect(out.find((c) => c.condition === 'gateway_supervisor_inert')!.active).toBe(false)
    expect(out.find((c) => c.condition === 'gateway_supervisor_refusing')!.active).toBe(false)
  })

  it('armed and not-armed resolve both supervisor conditions; NULL holds both', () => {
    for (const s of ['armed', 'not-armed']) {
      const out = loopStates(row({ gateway_supervisor_state: s }))
      expect(out.find((c) => c.condition === 'gateway_supervisor_refusing')!.active).toBe(false)
      expect(out.find((c) => c.condition === 'gateway_supervisor_inert')!.active).toBe(false)
    }
    expect(
      loopStates(row({ gateway_supervisor_state: null })).filter((c) =>
        c.condition.startsWith('gateway_supervisor')
      )
    ).toHaveLength(0)
  })

  it('labels are human, not identifiers', () => {
    expect(conditionLabel('gateway_loop_wedged')).toMatch(/wedged/i)
    expect(conditionLabel('gateway_supervisor_refusing')).toMatch(/human/i)
    expect(conditionLabel('gateway_supervisor_inert')).toMatch(/cannot act/i)
  })
})
