/**
 * Release restarts hold boot-shaped pages (2026-10-31, ./restart-hold).
 *
 * Falsifiers, stated before the assertions:
 *
 *   held while deploying     the whole feature: captain-mark paged five SEV1s
 *                            for a release boot on 2026-10-31.
 *   a recovery goes through  holding an inactive state would strand an open
 *                            alert until the next release.
 *   other conditions page    a hard stop during a release is still a hard stop.
 *   a finished rollout pages a boot that never came back ends the rollout
 *                            (rolled_back/failed) and must page.
 *   the cap ends the hold    a controller that died mid-rollout leaves the row
 *                            `deploying` forever (2026-10-30).
 *   a failed read holds none a broken read must page as before, never silence.
 *   the query is real        it runs against the migration's own table.
 */

import { readFileSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'
import { describe, it, expect } from 'vitest'
import {
  HOLD_CAP_MS,
  readRestartingSeats,
  restartingSeats,
  withoutRestartPages,
} from './restart-hold'

const NOW = Date.parse('2026-10-31T19:06:00Z')
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')

function row(state: string, startedMsAgo = 5 * 60 * 1000, slug = 'captain-mark') {
  return { customer_slug: slug, state, started_at: iso(NOW - startedMsAgo) }
}

function st(condition: string, active = true, slug = 'captain-mark') {
  return { customer_slug: slug, condition, active, detail: '' }
}

describe('restartingSeats', () => {
  it('is the seats whose newest rollout is deploying or smoking', () => {
    const seats = restartingSeats([row('deploying'), row('smoking', 1000, 'scott')], NOW)
    expect([...seats].sort()).toEqual(['captain-mark', 'scott'])
  })

  it.each(['done', 'rolled_back', 'failed', 'tooling_error', 'waiting_idle', 'pending', 'halted'])(
    'is not a seat whose rollout is %s',
    (state) => {
      expect(restartingSeats([row(state)], NOW).size).toBe(0)
    }
  )

  it('ends at the cap, measured from started_at', () => {
    expect(restartingSeats([row('deploying', HOLD_CAP_MS - 1000)], NOW).size).toBe(1)
    expect(restartingSeats([row('deploying', HOLD_CAP_MS + 1000)], NOW).size).toBe(0)
  })

  it('holds nothing for a row with no start time', () => {
    expect(
      restartingSeats([{ customer_slug: 'x', state: 'deploying', started_at: null }], NOW).size
    ).toBe(0)
  })
})

describe('withoutRestartPages', () => {
  const restarting = new Set(['captain-mark'])

  it('holds the active boot-shaped states of a restarting seat', () => {
    const states = [
      st('heartbeat_red'),
      st('spec_unverified'),
      st('gateway_supervisor_inert'),
      st('gateway_loop_wedged'),
      st('work_overdue'),
    ]
    const { kept, held } = withoutRestartPages(states, restarting)
    expect(kept).toEqual([])
    expect(held).toHaveLength(5)
  })

  it('lets a recovery through, so an open alert can close', () => {
    expect(withoutRestartPages([st('heartbeat_red', false)], restarting).kept).toHaveLength(1)
  })

  it('pages every other condition, and every other seat', () => {
    const { kept } = withoutRestartPages(
      [st('hard_stop'), st('selftest_failing'), st('heartbeat_red', true, 'scott')],
      restarting
    )
    expect(kept.map((s) => `${s.customer_slug}:${s.condition}`)).toEqual([
      'captain-mark:hard_stop',
      'captain-mark:selftest_failing',
      'scott:heartbeat_red',
    ])
  })
})

/** fleet_rollouts exactly as migration 0129 creates it, behind a D1-shaped wrapper. */
function d1With(rows: Array<{ id: string; slug: string; state: string; started: string }>) {
  const sql = readFileSync(
    new URL('../../../migrations/0129_fleet_release_system.sql', import.meta.url).pathname,
    'utf8'
  )
  const create = /CREATE TABLE fleet_rollouts \([\s\S]*?\n\);/.exec(sql)?.[0]
  if (!create) throw new Error('fleet_rollouts not found in migration 0129')
  const db = new DatabaseSync(':memory:')
  db.exec(create)
  const insert = db.prepare(
    'INSERT INTO fleet_rollouts (rollout_id, release_id, customer_slug, state, started_at) VALUES (?, ?, ?, ?, ?)'
  )
  for (const r of rows) insert.run(r.id, 'rel', r.slug, r.state, r.started)
  return {
    prepare: (q: string) => ({
      all: async () => ({ results: db.prepare(q).all() }),
    }),
  } as unknown as D1Database
}

describe('readRestartingSeats', () => {
  it('reads each seat by its NEWEST rollout', async () => {
    const db = d1With([
      { id: 'a', slug: 'captain-mark', state: 'done', started: iso(NOW - 3_600_000) },
      { id: 'b', slug: 'captain-mark', state: 'deploying', started: iso(NOW - 60_000) },
      { id: 'c', slug: 'scott', state: 'deploying', started: iso(NOW - 3_600_000) },
      { id: 'd', slug: 'scott', state: 'done', started: iso(NOW - 60_000) },
    ])
    expect([...(await readRestartingSeats(db, NOW))]).toEqual(['captain-mark'])
  })

  it('holds nothing when the read fails', async () => {
    const broken = {
      prepare: () => ({ all: async () => Promise.reject(new Error('D1 down')) }),
    } as unknown as D1Database
    expect((await readRestartingSeats(broken, NOW)).size).toBe(0)
  })
})
