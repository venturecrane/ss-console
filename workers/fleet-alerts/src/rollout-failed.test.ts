/**
 * The failed-rollout pager (2026-10-21, migration 0133).
 *
 * Falsifiers, stated before the assertions:
 *
 *   page on a fresh failure  the whole feature: captain-mark's 2026-10-20
 *                            preflight refusal sat unread for a day.
 *   silence on same marker   the 2-minute cron re-reads the same rows 720
 *                            times a day. Paging on equality is an alert storm.
 *   page on advance          a second failure on the same seat is a second page.
 *   no backlog on first run  a seat with no marker pages only for the last day,
 *                            or deploying this mails the table's history.
 *   done/halted never page   halted is the seat that was never reached, already
 *                            covered by the failure that halted it; done is fine.
 *   send failure holds       a Resend outage must not move the marker.
 *   read failure is silent   a broken read pages nothing and does not throw,
 *                            so the seat pagers that ran first are unaffected.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { conditionLabel, type Env } from './index'
import {
  decideRolloutFailedPage,
  notifyRolloutFailures,
  rolloutFailedSubject,
  type FailedRolloutRow,
} from './rollout-failed'

const NOW = Date.parse('2026-10-21T23:00:00.000Z')

function rollout(overrides: Partial<FailedRolloutRow> = {}): FailedRolloutRow {
  return {
    rollout_id: 'ro-1',
    release_id: 'img-202610202216-41f5627a-1aa0a762',
    customer_slug: 'captain-mark',
    state: 'failed',
    detail:
      'preflight refused: secrets are STAGED on the seat and this release does not declare them: TELEGRAM_BOT_TOKEN',
    finished_at: '2026-10-21T22:59:12Z',
    ...overrides,
  }
}

describe('decideRolloutFailedPage', () => {
  it('pages on a fresh failure for a seat never paged before', () => {
    const page = decideRolloutFailedPage('captain-mark', [rollout()], null, NOW)
    expect(page?.marker).toBe('2026-10-21T22:59:12Z')
    expect(page?.rows).toHaveLength(1)
  })

  it('holds on a marker equal to the newest failure', () => {
    expect(
      decideRolloutFailedPage('captain-mark', [rollout()], '2026-10-21T22:59:12Z', NOW)
    ).toBeNull()
  })

  it('pages again when a newer failure lands, carrying only the new rows', () => {
    const rows = [
      rollout(),
      rollout({ rollout_id: 'ro-2', state: 'rolled_back', finished_at: '2026-10-21T23:30:00Z' }),
    ]
    const page = decideRolloutFailedPage(
      'captain-mark',
      rows,
      '2026-10-21T22:59:12Z',
      NOW + 3600_000
    )
    expect(page?.marker).toBe('2026-10-21T23:30:00Z')
    expect(page?.rows.map((r) => r.rollout_id)).toEqual(['ro-2'])
  })

  it('does not page a backlog older than a day on a seat with no marker', () => {
    const old = rollout({ finished_at: '2026-10-19T10:00:00Z' })
    expect(decideRolloutFailedPage('captain-mark', [old], null, NOW)).toBeNull()
  })

  it('never pages done, halted or waiting_idle rows, or another seat', () => {
    const rows = ['done', 'halted', 'waiting_idle', 'skipped_held'].map((state, i) =>
      rollout({ rollout_id: `ro-${i}`, state })
    )
    rows.push(rollout({ customer_slug: 'scott' }))
    expect(decideRolloutFailedPage('captain-mark', rows, null, NOW)).toBeNull()
  })

  it('ignores a row with no parseable finished_at rather than paging on junk', () => {
    expect(
      decideRolloutFailedPage('captain-mark', [rollout({ finished_at: null })], null, NOW)
    ).toBeNull()
  })
})

describe('rolloutFailedSubject', () => {
  it('names the seat, state, release and the head of the detail', () => {
    const page = decideRolloutFailedPage('captain-mark', [rollout()], null, NOW)!
    expect(rolloutFailedSubject(page)).toBe(
      '[captain-mark] fleet rollout failed: img-202610202216-41f5627a-1aa0a762 ' +
        '(preflight refused: secrets are STAGED on the seat and this r)'
    )
  })

  it('has a human label for the condition', () => {
    expect(conditionLabel('rollout_failed')).toMatch(/did not land/)
  })
})

// ---------------------------------------------------------------------------
// notifyRolloutFailures against a fake D1 that remembers the marker
// ---------------------------------------------------------------------------

interface FakeState {
  rollouts: FailedRolloutRow[]
  markers: Map<string, string>
  failRead?: boolean
}

function makeEnv(state: FakeState): Env {
  const db = {
    prepare(sql: string) {
      return {
        bind(...args: unknown[]) {
          return {
            all() {
              if (!sql.includes('FROM fleet_rollouts')) throw new Error(`unexpected all(): ${sql}`)
              if (state.failRead) return Promise.reject(new Error('D1 unavailable'))
              const cutoff = String(args[0])
              return Promise.resolve({
                results: state.rollouts.filter(
                  (r) =>
                    ['failed', 'rolled_back', 'tooling_error'].includes(r.state) &&
                    (r.finished_at ?? '') >= cutoff
                ),
              })
            },
            first() {
              const marker = state.markers.get(`${args[0]}:${args[1]}`)
              return Promise.resolve(marker === undefined ? null : { last_seen_marker: marker })
            },
            run() {
              state.markers.set(`${args[0]}:${args[1]}`, String(args[3]))
              return Promise.resolve({})
            },
          }
        },
      }
    },
  }
  return {
    DB: db as unknown as D1Database,
    RESEND_API_KEY: 'rk_test',
    HEARTBEAT_RED_SECONDS: '300',
  }
}

function stubResend(ok = true): ReturnType<typeof vi.fn> {
  const mock = vi
    .fn()
    .mockImplementation(async () =>
      ok
        ? new Response(JSON.stringify({ id: 'resend-rollout-1' }), { status: 200 })
        : new Response('down', { status: 500 })
    )
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('notifyRolloutFailures', () => {
  it('pages once, moves the marker, and is silent on the next tick', async () => {
    const mock = stubResend()
    const state: FakeState = { rollouts: [rollout()], markers: new Map() }
    const first = await notifyRolloutFailures(makeEnv(state), NOW)
    expect(first).toEqual([
      {
        customer_slug: 'captain-mark',
        marker: '2026-10-21T22:59:12Z',
        releases: ['img-202610202216-41f5627a-1aa0a762'],
        emailed: true,
        resendId: 'resend-rollout-1',
      },
    ])
    expect(state.markers.get('captain-mark:rollout_failed')).toBe('2026-10-21T22:59:12Z')
    const body = JSON.parse(String((mock.mock.calls[0][1] as RequestInit).body))
    expect(body.to).toBe('team@smd.services')
    expect(body.html).toContain('--only captain-mark')
    expect(await notifyRolloutFailures(makeEnv(state), NOW + 120_000)).toEqual([])
    expect(mock).toHaveBeenCalledTimes(1)
  })

  it('a Resend failure does not move the marker, so the next tick retries', async () => {
    stubResend(false)
    const state: FakeState = { rollouts: [rollout()], markers: new Map() }
    const out = await notifyRolloutFailures(makeEnv(state), NOW)
    expect(out[0].emailed).toBe(false)
    expect(state.markers.size).toBe(0)
  })

  it('a read failure pages nothing and does not throw', async () => {
    const mock = stubResend()
    const state: FakeState = { rollouts: [rollout()], markers: new Map(), failRead: true }
    expect(await notifyRolloutFailures(makeEnv(state), NOW)).toEqual([])
    expect(mock).not.toHaveBeenCalled()
  })
})
