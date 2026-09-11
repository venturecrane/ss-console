/**
 * The refused-or-unsent escalation pager (ss#2547).
 *
 * Organized as a sibling suite for `stale-holds.test.ts`'s reason: the property
 * under test here is a MARKER ADVANCE across runs, and `index.test.ts`'s fake
 * D1 has no notion of `last_seen_marker` at all. A test written against that
 * fake could not observe a re-page, which is the single behavior this module
 * exists to prevent.
 *
 * The falsifiers each case is guarding against, stated before the assertions:
 *
 *   hold on NULL          a seat that cannot answer must not page. If it did,
 *                         every healthy seat would page on every cron tick.
 *   hold on undefined     the same, for a database where migration 0109 has not
 *                         landed. `row.send_refusals_last_ts` is then absent,
 *                         not null, and `x !== null` would have let it through.
 *   page on first marker  the whole feature. A seat that has never paged has no
 *                         row, and no row must not read as "already handled".
 *   silence on same ts    the 2-minute cron re-reads the same row 720 times a
 *                         day. Paging on equality is an alert storm.
 *   page on advance       the 2026-08-19 case followed by the 2026-08-20 case:
 *                         two separate days, two separate pages.
 *   never RECOVERED       a refusal has no green state. A RECOVERED email would
 *                         tell a human the routine is reaching them again when
 *                         all that happened is the seat stopped trying.
 *   send failure holds    a Resend outage must not move the marker, or the
 *                         alert exists only in a Worker log forever.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { conditionLabel, runOnce, type Env, type FleetStatusRow } from './index'
import {
  decideSendRefusedPage,
  parseSendRefusalEvents,
  sendRefusedSubject,
  SEND_REFUSED_CONDITION,
} from './send-refused'

const NOW = Date.parse('2026-08-22T12:00:00.000Z')

/** The 2026-08-19 pilot refusals, in the shape the ingest handler stores. */
const REFUSED_JSON = JSON.stringify([
  {
    ts: '2026-08-19T14:01:20.000Z',
    kind: 'refused',
    routine: 'deadline-miss-escalator',
    tool: 'smd_send_message',
    reason: 'IDENTIFIER_UNVERIFIED',
  },
  {
    ts: '2026-08-19T14:00:45.000Z',
    kind: 'refused',
    routine: 'deadline-miss-escalator',
    tool: 'smd_send_message',
    reason: 'FABRICATION_FILTER_TRIGGERED',
  },
])

/** The 2026-08-20 case: it woke with five needs-you items and never tried. */
const UNSENT_JSON = JSON.stringify([
  {
    ts: '2026-08-20T14:00:10.000Z',
    kind: 'unsent',
    routine: 'deadline-miss-escalator',
    reason: 'no_send_attempted',
    needs_you: 5,
  },
])

function row(overrides: Partial<FleetStatusRow> = {}): FleetStatusRow {
  return {
    customer_slug: 'pilot-smokeball',
    last_heartbeat_ts: '2026-08-22T11:59:30.000Z',
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
    send_refusals: null,
    send_refusals_last_ts: null,
    send_refusals_json: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// The pure decision
// ---------------------------------------------------------------------------

describe('decideSendRefusedPage', () => {
  it('holds when the seat reports NULL (it cannot answer, it did not recover)', () => {
    expect(decideSendRefusedPage(row(), null)).toBeNull()
  })

  it('holds when the COLUMN is absent (a pre-0109 database mid-deploy)', () => {
    const preMigration = row()
    delete (preMigration as Partial<FleetStatusRow>).send_refusals_last_ts
    expect(decideSendRefusedPage(preMigration, null)).toBeNull()
  })

  it('holds on an unparseable marker rather than paging on junk', () => {
    expect(
      decideSendRefusedPage(row({ send_refusals_last_ts: 'not-a-timestamp' }), null)
    ).toBeNull()
  })

  it('pages on the first marker a seat ever reports (no row is not "handled")', () => {
    const page = decideSendRefusedPage(
      row({ send_refusals_last_ts: '2026-08-19T14:01:20.000Z', send_refusals_json: REFUSED_JSON }),
      null
    )
    expect(page).not.toBeNull()
    expect(page?.kind).toBe('refused')
    expect(page?.reason).toBe('IDENTIFIER_UNVERIFIED')
    expect(page?.events).toHaveLength(2)
  })

  it('holds when the marker equals the one already paged for', () => {
    expect(
      decideSendRefusedPage(
        row({
          send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
          send_refusals_json: REFUSED_JSON,
        }),
        '2026-08-19T14:01:20.000Z'
      )
    ).toBeNull()
  })

  it('holds when the marker went BACKWARDS (a restored volume, not a new event)', () => {
    expect(
      decideSendRefusedPage(
        row({ send_refusals_last_ts: '2026-08-19T14:01:20.000Z' }),
        '2026-08-20T14:00:10.000Z'
      )
    ).toBeNull()
  })

  it('pages again the next day, when the marker advances', () => {
    const page = decideSendRefusedPage(
      row({ send_refusals_last_ts: '2026-08-20T14:00:10.000Z', send_refusals_json: UNSENT_JSON }),
      '2026-08-19T14:01:20.000Z'
    )
    expect(page?.kind).toBe('unsent')
    expect(page?.events[0]?.needs_you).toBe(5)
  })

  it('still pages when the detail list is missing or corrupt', () => {
    const page = decideSendRefusedPage(
      row({ send_refusals_last_ts: '2026-08-19T14:01:20.000Z', send_refusals_json: '{not json' }),
      null
    )
    expect(page?.marker).toBe('2026-08-19T14:01:20.000Z')
    expect(page?.kind).toBe('unknown')
    expect(page?.events).toEqual([])
  })
})

describe('parseSendRefusalEvents', () => {
  it('drops entries missing ts or kind and keeps their valid siblings', () => {
    const events = parseSendRefusalEvents(
      JSON.stringify([{ kind: 'refused' }, { ts: '2026-08-19T14:00:45.000Z', kind: 'refused' }])
    )
    expect(events).toEqual([{ ts: '2026-08-19T14:00:45.000Z', kind: 'refused' }])
  })

  it('reads a NULL or absent column as no detail, never as an error', () => {
    expect(parseSendRefusalEvents(null)).toEqual([])
    expect(parseSendRefusalEvents(undefined)).toEqual([])
  })
})

describe('sendRefusedSubject', () => {
  it('names the seat, the kind, and the head of the reason verbatim', () => {
    const page = decideSendRefusedPage(
      row({ send_refusals_last_ts: '2026-08-19T14:01:20.000Z', send_refusals_json: REFUSED_JSON }),
      null
    )
    expect(sendRefusedSubject(page!)).toBe(
      '[pilot-smokeball] send refused: refused/IDENTIFIER_UNVERIFIED'
    )
  })

  it('caps the reason at 60 characters so the subject stays readable', () => {
    const long = 'x'.repeat(200)
    const page = decideSendRefusedPage(
      row({
        send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
        send_refusals_json: JSON.stringify([
          { ts: '2026-08-19T14:01:20.000Z', kind: 'refused', reason: long },
        ]),
      }),
      null
    )
    expect(sendRefusedSubject(page!)).toBe(
      `[pilot-smokeball] send refused: refused/${'x'.repeat(60)}`
    )
  })
})

describe('conditionLabel', () => {
  it('labels send_refused in the terms a human needs on the page', () => {
    expect(conditionLabel('send_refused')).toBe(
      "a routine's outbound send was refused by a gate, or a wake with needs-you items sent nothing"
    )
  })
})

// ---------------------------------------------------------------------------
// runOnce, against a fake D1 that actually remembers the marker
// ---------------------------------------------------------------------------

interface FakeState {
  fleet: FleetStatusRow[]
  /** (slug, condition) -> the row this module writes. */
  alertRows: Map<string, { status: string; last_seen_marker: string | null }>
  writes: string[]
}

function makeEnv(state: FakeState, withResend = true): Env {
  const db = {
    prepare(sql: string) {
      return {
        all() {
          if (sql.includes('FROM fleet_status')) return Promise.resolve({ results: state.fleet })
          throw new Error(`unexpected all(): ${sql}`)
        },
        bind(...args: unknown[]) {
          const key = `${args[0]}:${args[1]}`
          return {
            all() {
              // Everything the other modules read is empty in this suite: the
              // point here is the marker, not the level conditions.
              return Promise.resolve({ results: [] })
            },
            first() {
              if (sql.includes('last_seen_marker')) {
                const row = state.alertRows.get(key)
                return Promise.resolve(row ? { last_seen_marker: row.last_seen_marker } : null)
              }
              if (sql.includes('SELECT status FROM fleet_alert_state')) {
                const row = state.alertRows.get(key)
                return Promise.resolve(row ? { status: row.status } : null)
              }
              throw new Error(`unexpected first(): ${sql}`)
            },
            run() {
              if (sql.includes('last_seen_marker')) {
                // args: slug, condition, resendId, marker
                state.alertRows.set(key, {
                  status: 'resolved',
                  last_seen_marker: String(args[3]),
                })
                state.writes.push(`marker:${key}:${String(args[3])}`)
                return Promise.resolve({})
              }
              if (sql.includes('INSERT INTO fleet_alert_state')) {
                state.alertRows.set(key, { status: 'open', last_seen_marker: null })
                state.writes.push(`open:${key}`)
                return Promise.resolve({})
              }
              if (sql.includes("SET status = 'resolved'")) {
                state.alertRows.set(key, { status: 'resolved', last_seen_marker: null })
                state.writes.push(`resolve:${key}`)
                return Promise.resolve({})
              }
              throw new Error(`unexpected run(): ${sql}`)
            },
          }
        },
      }
    },
  }
  return {
    DB: db as unknown as D1Database,
    RESEND_API_KEY: withResend ? 'rk_test' : undefined,
    HEARTBEAT_RED_SECONDS: '300',
  }
}

function stubResend(ok = true): ReturnType<typeof vi.fn> {
  const mock = vi
    .fn()
    .mockImplementation(async () =>
      ok
        ? new Response(JSON.stringify({ id: 'resend-refused-1' }), { status: 200 })
        : new Response('resend down', { status: 500 })
    )
  vi.stubGlobal('fetch', mock)
  return mock
}

function sentSubjects(mock: ReturnType<typeof vi.fn>): string[] {
  return mock.mock.calls
    .filter((call) => String(call[0]).includes('api.resend.com'))
    .map((call) => JSON.parse(String((call[1] as RequestInit).body)).subject as string)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('runOnce send-refusal paging', () => {
  it('a seat reporting nothing is silent and writes no marker', async () => {
    const mock = stubResend()
    const state: FakeState = { fleet: [row()], alertRows: new Map(), writes: [] }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.send_refusals).toEqual([])
    expect(state.writes).toEqual([])
    expect(sentSubjects(mock)).toEqual([])
  })

  it('pages once on the first marker, and NOT again on the same one', async () => {
    const mock = stubResend()
    const state: FakeState = {
      fleet: [
        row({
          send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
          send_refusals_json: REFUSED_JSON,
        }),
      ],
      alertRows: new Map(),
      writes: [],
    }
    const env = makeEnv(state)
    const first = await runOnce(env, NOW)
    expect(first.send_refusals).toHaveLength(1)
    expect(first.send_refusals[0]?.emailed).toBe(true)
    expect(state.alertRows.get(`pilot-smokeball:${SEND_REFUSED_CONDITION}`)).toEqual({
      status: 'resolved',
      last_seen_marker: '2026-08-19T14:01:20.000Z',
    })

    const second = await runOnce(env, NOW)
    expect(second.send_refusals).toEqual([])
    expect(sentSubjects(mock)).toEqual([
      '[SMD Ops] [pilot-smokeball] send refused: refused/IDENTIFIER_UNVERIFIED',
    ])
  })

  it('pages again when the next day brings a newer event', async () => {
    const mock = stubResend()
    const seat = row({
      send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
      send_refusals_json: REFUSED_JSON,
    })
    const state: FakeState = { fleet: [seat], alertRows: new Map(), writes: [] }
    const env = makeEnv(state)
    await runOnce(env, NOW)
    seat.send_refusals_last_ts = '2026-08-20T14:00:10.000Z'
    seat.send_refusals_json = UNSENT_JSON
    const second = await runOnce(env, NOW)
    expect(second.send_refusals[0]?.kind).toBe('unsent')
    expect(sentSubjects(mock)).toHaveLength(2)
    expect(sentSubjects(mock)[1]).toContain('unsent/no_send_attempted')
  })

  it('never emits a RECOVERED notice for send_refused', async () => {
    const mock = stubResend()
    const seat = row({
      send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
      send_refusals_json: REFUSED_JSON,
    })
    const state: FakeState = { fleet: [seat], alertRows: new Map(), writes: [] }
    const env = makeEnv(state)
    await runOnce(env, NOW)
    // The seat recovers: nothing refused since. Under a level-shaped condition
    // this is exactly where a RECOVERED would fire.
    seat.send_refusals = 0
    await runOnce(env, NOW)
    await runOnce(env, NOW)
    expect(sentSubjects(mock).filter((s) => s.includes('RECOVERED'))).toEqual([])
    expect(state.writes.filter((w) => w.includes(SEND_REFUSED_CONDITION))).toEqual([
      `marker:pilot-smokeball:${SEND_REFUSED_CONDITION}:2026-08-19T14:01:20.000Z`,
    ])
  })

  it('a failed send leaves the marker unmoved so the next cron retries', async () => {
    const mock = stubResend(false)
    const state: FakeState = {
      fleet: [
        row({
          send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
          send_refusals_json: REFUSED_JSON,
        }),
      ],
      alertRows: new Map(),
      writes: [],
    }
    const env = makeEnv(state)
    const first = await runOnce(env, NOW)
    expect(first.send_refusals[0]?.emailed).toBe(false)
    expect(state.alertRows.has(`pilot-smokeball:${SEND_REFUSED_CONDITION}`)).toBe(false)

    vi.unstubAllGlobals()
    const retry = stubResend()
    const second = await runOnce(env, NOW)
    expect(second.send_refusals[0]?.emailed).toBe(true)
    expect(sentSubjects(retry)).toHaveLength(1)
    expect(mock).toBeDefined()
  })

  it('one seat with a broken row cannot stop another seat from paging', async () => {
    stubResend()
    const state: FakeState = {
      fleet: [
        row({ customer_slug: 'a', send_refusals_last_ts: 'garbage' }),
        row({
          customer_slug: 'b',
          send_refusals_last_ts: '2026-08-19T14:01:20.000Z',
          send_refusals_json: REFUSED_JSON,
        }),
      ],
      alertRows: new Map(),
      writes: [],
    }
    const summary = await runOnce(makeEnv(state), NOW)
    expect(summary.send_refusals.map((n) => n.customer_slug)).toEqual(['b'])
  })
})
