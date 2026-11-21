/**
 * Lead time, the split: held, busy and pipeline minutes, when they are not
 * recorded, where the clock starts, and rounding. Fixtures are the D1 rows the fleet writers store
 * (tests/helpers/lead-time-fleet.ts); the tick writes no row for a held seat,
 * so a lifted hold is visible only through recorded hold events.
 */

import { describe, it, expect } from 'vitest'
import { overThreshold } from '../src/lib/admin/lead-time'
import { clipUnion, splitFor } from '../src/lib/admin/lead-time-split'
import { describeArrival } from '../src/lib/admin/lead-time-summary'
import type { RolloutRow } from '../src/lib/admin/merge-to-seat'
import {
  Fleet,
  RECORDED,
  arrived,
  at,
  headlineOf,
  iso,
  sha,
  split,
  waiting,
} from './helpers/lead-time-fleet'

describe('holds', () => {
  it('the tick writes no row for a held seat: a hold placed and lifted leaves no trace, so the arrival is not marked and its split is unknown', () => {
    const f = new Fleet()
    f.roll(f.image(1, 0), 'twin-a', 'done', 4, 6)
    // client-a was held from minute 5 to 150 and unheld: no rollout row, no hold row survives.
    f.roll('img-1', 'client-a', 'done', 170, 175)
    const [m] = f.arrivals(200)
    const a = arrived(m.headline)
    expect(a.split).toEqual({ recorded: false, totalMinutes: 175, missing: 'hold history' })
    expect(overThreshold(a)).toBe(false)
    expect(describeArrival(m).marked).toBe(false)
  })

  it('with hold events recorded, a hold placed and lifted twice counts both stretches', () => {
    const f = new Fleet(true)
    f.roll(f.image(1, 0), 'twin-a', 'done', 1, 3)
    f.hold('client-a', 5, 20)
    f.hold('client-a', 30, 50)
    f.roll('img-1', 'client-a', 'done', 50, 55)
    expect(arrived(headlineOf(f.arrivals(10_000, RECORDED), 1)).split).toEqual(split(55, 20, 35, 0))
  })

  it('a hold still in place now: in progress, waiting on the hold, held time running to now once recorded', () => {
    const f = new Fleet(true)
    f.roll(f.image(1, 0), 'twin-a', 'done', 5, 8)
    f.hold('client-a', 10)
    const today = waiting(headlineOf(f.arrivals(100), 1))
    expect(today.waitingOn).toEqual({ reason: 'hold', seat: 'client-a' })
    expect(today.split.recorded).toBe(false)
    const recorded = waiting(headlineOf(f.arrivals(100, RECORDED), 1))
    expect(recorded.split).toEqual(split(100, 10, 90, 0))
  })

  it('a hold on the release twin is a hold on its client', () => {
    const f = new Fleet()
    f.image(1, 0)
    f.hold('twin-a', 2)
    expect(waiting(headlineOf(f.arrivals(60), 1)).waitingOn).toEqual({
      reason: 'hold',
      seat: 'twin-a',
    })
  })

  it('a hold on the twin after the twin is done no longer gates the client', () => {
    const f = new Fleet(true)
    f.roll(f.image(1, 0), 'twin-a', 'done', 5, 8)
    f.hold('twin-a', 9)
    f.roll('img-1', 'client-a', 'done', 10, 15)
    expect(arrived(headlineOf(f.arrivals(100, RECORDED), 1)).split).toEqual(split(15, 15, 0, 0))
  })

  it('a window that starts before hold history was recorded is not split, even once it is', () => {
    const f = new Fleet(true)
    f.deliver(f.image(1, 0), 8, 10, 15)
    const a = arrived(headlineOf(f.arrivals(100, iso(5)), 1))
    expect(a.split).toMatchObject({ recorded: false, missing: 'hold history' })
  })
})

describe('busy', () => {
  it('busy inside a successful rollout counts its recorded seconds', () => {
    const f = new Fleet(true)
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 4, 6)
    f.waited(r, 'client-a', 'done', [10, 25], 600)
    expect(arrived(headlineOf(f.arrivals(100, RECORDED), 1)).split).toEqual(split(25, 15, 0, 10))
  })

  it('a lease wait (waiting_idle with zero busy seconds) is not busy', () => {
    const f = new Fleet(true)
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 4, 6)
    f.waited(r, 'client-a', 'waiting_idle', [8, 10], 0)
    f.roll(r, 'client-a', 'done', 20, 25)
    expect(arrived(headlineOf(f.arrivals(100, RECORDED), 1)).split).toEqual(split(25, 25, 0, 0))
  })

  it('an attempt with no recorded busy seconds makes the split not recorded', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.waited(r, 'twin-a', 'done', [4, 6], 0)
    f.roll(r, 'client-a', 'done', 10, 25)
    const a = arrived(headlineOf(f.arrivals(100, RECORDED), 1))
    expect(a.split).toEqual({ recorded: false, totalMinutes: 25, missing: 'busy time' })
  })

  it('a seat busy at the first visit and done at a later one: busy is the wait, not a count of rows', () => {
    const once = new Fleet(true)
    const r1 = once.image(1, 0)
    once.roll(r1, 'twin-a', 'done', 4, 6)
    once.waited(r1, 'client-a', 'waiting_idle', [8, 18], 600)
    once.roll(r1, 'client-a', 'done', 30, 35)

    const often = new Fleet(true)
    const r2 = often.image(1, 0)
    often.roll(r2, 'twin-a', 'done', 4, 6)
    for (const start of [8, 13, 18, 23]) {
      often.waited(r2, 'client-a', 'waiting_idle', [start, start + 4], 240)
    }
    often.roll(r2, 'client-a', 'done', 30, 35)

    expect(arrived(headlineOf(once.arrivals(100, RECORDED), 1)).split).toEqual(split(35, 13, 0, 22))
    expect(arrived(headlineOf(often.arrivals(100, RECORDED), 1)).split).toEqual(
      split(35, 13, 0, 22)
    )
  })

  it('a seat still waiting now: busy when its seconds say so, an idle wait when they are not recorded', () => {
    const known = new Fleet(true)
    known.roll(known.image(1, 0), 'twin-a', 'done', 4, 6)
    known.waited('img-1', 'client-a', 'waiting_idle', [8, 18], 600)
    expect(waiting(headlineOf(known.arrivals(40, RECORDED), 1)).waitingOn.reason).toBe('busy')

    const unknown = new Fleet()
    unknown.roll(unknown.image(1, 0), 'twin-a', 'done', 4, 6)
    unknown.roll('img-1', 'client-a', 'waiting_idle', 8, 18)
    expect(waiting(headlineOf(unknown.arrivals(40), 1)).waitingOn.reason).toBe('idle_wait')
  })
})

describe('the clock', () => {
  it('starts when the change reached main when recorded, else at the queued merge commit', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0, 2, undefined, 12), 20, 22, 30)
    f.deliver(f.image(2, 40), 48, 50, 55)
    const arrivals = f.arrivals()
    const one = arrivals.find((a) => a.merge.sha === sha(1))!
    const two = arrivals.find((a) => a.merge.sha === sha(2))!
    expect(one.merge).toMatchObject({ startMs: at(12), clockFrom: 'merged' })
    expect(arrived(one.headline).split.totalMinutes).toBe(18)
    expect(two.merge).toMatchObject({ startMs: at(40), clockFrom: 'queued' })
  })
})

describe('rounding', () => {
  it('pipeline, held and busy add up to the total exactly, and pipeline is never negative', () => {
    const f = new Fleet(true)
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', -1, -0.5)
    f.hold('client-a', 0, 0.5)
    f.waited(r, 'client-a', 'done', [0.5, 1], 30)
    expect(arrived(headlineOf(f.arrivals(100, RECORDED), 1)).split).toEqual(split(1, 0, 1, 0))

    const end = 600_000
    for (let held = 0; held <= 600; held += 37) {
      for (let busy = 0; busy + held <= 600; busy += 41) {
        const row: RolloutRow = {
          releaseId: 'x',
          seat: 's',
          state: 'done',
          startedAtMs: held * 1000,
          finishedAtMs: end,
          busySeconds: busy,
        }
        const s = splitFor({
          tracks: [{ seat: 's', untilMs: Infinity }],
          fromMs: 0,
          toMs: end,
          bySeat: new Map([['s', [row]]]),
          holds: [{ seat: 's', heldAtMs: 0, liftedAtMs: held * 1000 }],
          holdHistorySinceMs: 0,
        })
        if (!s.recorded) throw new Error('expected a recorded split')
        expect(s.pipelineMinutes + s.heldMinutes + s.busyMinutes).toBe(s.totalMinutes)
        expect(s.pipelineMinutes).toBeGreaterThanOrEqual(0)
      }
    }
  })

  it('clipUnion merges overlapping stretches and clips them to the window', () => {
    expect(
      clipUnion(
        [
          [0, 10],
          [5, 20],
          [30, Infinity],
        ],
        2,
        40
      )
    ).toEqual([
      [2, 20],
      [30, 40],
    ])
  })
})
