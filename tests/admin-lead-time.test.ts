/**
 * Lead time, merge to client seat: arrival, kinds and empty states, and the
 * recent real release pattern. Holds, busy time, the clock and rounding are in
 * admin-lead-time-split.test.ts. Fixtures are the D1 rows the fleet writers
 * store (tests/helpers/lead-time-fleet.ts), measured through the real functions.
 */

import { describe, it, expect } from 'vitest'
import { REVIEW_THRESHOLD_PIPELINE_MINUTES, overThreshold } from '../src/lib/admin/lead-time'
import { describeArrival, p90, summarizeLeadTime } from '../src/lib/admin/lead-time-summary'
import {
  Fleet,
  GROUPS,
  RECORDED,
  arrived,
  at,
  headlineOf,
  sha,
  split,
  waiting,
} from './helpers/lead-time-fleet'

describe('arrival', () => {
  it('a clear release: arrival at the last client seat done, all of it pipeline once recorded', () => {
    const f = new Fleet(true)
    f.deliver(f.image(1, 0), 8, 10, 15)
    const a = arrived(headlineOf(f.arrivals(10_000, RECORDED), 1))
    expect(a).toMatchObject({ atMs: at(15), lastSeat: 'client-a' })
    expect(a.split).toEqual(split(15, 15, 0, 0))
  })

  it('today, with no hold history recorded, the same arrival shows its total only and is not split', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0), 8, 10, 15)
    const [m] = f.arrivals()
    expect(arrived(m.headline).split).toEqual({
      recorded: false,
      totalMinutes: 15,
      missing: 'hold history',
    })
    expect(describeArrival(m)).toEqual({
      text: '15 min (hold time not recorded)',
      marked: false,
    })
  })

  it('a release overtaken before the client took it: its merge counts against the newer release', () => {
    const f = new Fleet()
    f.roll(f.image(1, 0), 'twin-a', 'done', 5, 8)
    f.deliver(f.image(2, 10), 18, 20, 25)
    const arrivals = f.arrivals()
    expect(arrived(headlineOf(arrivals, 1))).toMatchObject({ atMs: at(25) })
    expect(arrived(headlineOf(arrivals, 1)).split.totalMinutes).toBe(25)
    expect(arrived(headlineOf(arrivals, 2)).split.totalMinutes).toBe(15)
  })

  it('two merges a minute apart carried by one release arrive together with different minutes', () => {
    const f = new Fleet(true)
    f.image(1, 0)
    f.deliver(f.image(2, 1), 12, 14, 20)
    const arrivals = f.arrivals(10_000, RECORDED)
    expect(arrived(headlineOf(arrivals, 1)).split).toEqual(split(20, 20, 0, 0))
    expect(arrived(headlineOf(arrivals, 2)).split).toEqual(split(19, 19, 0, 0))
    expect(arrived(headlineOf(arrivals, 1)).atMs).toBe(arrived(headlineOf(arrivals, 2)).atMs)
  })

  it('a hotfix release built later but off main line never delivers a main merge, and is no merge itself', () => {
    const f = new Fleet()
    f.image(1, 0)
    const hot = f.image(2, 30, 2, 'hotfix lane: client-a only')
    f.deliver(hot, 35, 36, 40)
    // A CI-shaped note whose sha does not match the commit is not main line either.
    f.deliver(f.image(3, 50, 2, `merge ${sha(9).slice(0, 8)} (CI)`), 55, 56, 60)
    const arrivals = f.arrivals(100)
    expect(waiting(headlineOf(arrivals, 1)).waitingOn).toEqual({ reason: 'twin', seat: 'twin-a' })
    expect(arrivals.map((a) => a.merge.sha)).toEqual([sha(1)])
  })

  it('a seat that ended tooling_error then done arrives at the done, with no held or busy time', () => {
    const f = new Fleet(true)
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 4, 6)
    f.roll(r, 'client-a', 'tooling_error', 8, 10)
    f.roll(r, 'client-a', 'done', 20, 25)
    const a = arrived(headlineOf(f.arrivals(10_000, RECORDED), 1))
    expect(a.atMs).toBe(at(25))
    expect(a.split).toEqual(split(25, 25, 0, 0))
  })

  it('the untwinned group is measured on its own and never moves the headline', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.deliver(r, 8, 10, 15)
    f.roll(r, 'client-b', 'done', 90, 100)
    const [m] = f.arrivals()
    expect(arrived(m.headline).split.totalMinutes).toBe(15)
    expect(arrived(m.untwinned).split.totalMinutes).toBe(100)
  })

  it('a seat first rolled a week after a merge does not become that merge last seat', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.deliver(r, 8, 10, 15)
    f.roll(r, 'client-b', 'done', 20, 25)
    const week = 7 * 24 * 60
    f.deliver(f.image(2, week), week + 8, week + 10, week + 15)
    f.roll('img-2', 'client-b', 'done', week + 20, week + 25)
    f.roll('img-2', 'client-c', 'done', week + 30, week + 40)
    const groups = { ...GROUPS, untwinned: ['client-b', 'client-c'] }
    const arrivals = f.arrivals(20_000, null, groups)
    const first = arrivals.find((a) => a.merge.sha === sha(1))!
    expect(arrived(first.untwinned)).toMatchObject({ lastSeat: 'client-b', atMs: at(25) })
  })

  it('no client seat in a group is no arrival, never a vacuous zero', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0), 8, 10, 15)
    const [m] = f.arrivals(100, null, { twinned: [], untwinned: [], twins: {} })
    expect(m.headline.status).toBe('no_seats')
  })
})

describe('kinds and empty states', () => {
  it('a merge that built no image reads "no release"; one that built nothing at all is not listed', () => {
    const f = new Fleet()
    f.roll(f.bundle(4, -100), 'client-a', 'done', -90, -85)
    const b = f.bundle(5, 3)
    f.roll(b, 'client-a', 'done', 6, 9)
    const { recent } = summarizeLeadTime(f.history(), GROUPS, at(100))
    expect(recent.map((m) => m.shortSha)).toEqual([sha(5).slice(0, 8), sha(4).slice(0, 8)])
    expect(describeArrival(recent[0].byKind.image)).toEqual({ text: 'no release', marked: false })
    expect(recent[0].clockFrom).toBe('published')
    expect(describeArrival(recent[0].byKind.bundle).text).toBe('6 min (hold time not recorded)')
  })

  it('a bundle is not waited for on a seat that never took one; its clock is the same commit image clock', () => {
    const f = new Fleet()
    f.roll(f.bundle(9, -100), 'client-a', 'done', -90, -85)
    f.deliver(f.image(1, 0), 8, 10, 15)
    f.roll(f.bundle(1, 4), 'client-a', 'done', 12, 20)
    const bundle = f.arrivals().find((a) => a.merge.kind === 'bundle' && a.merge.sha === sha(1))!
    expect(bundle.merge.clockFrom).toBe('queued')
    expect(arrived(bundle.headline).split.totalMinutes).toBe(20)
    expect(bundle.untwinned.status).toBe('no_seats')
  })

  it('a recorded arrival over the review threshold of pipeline time is marked; held time does not count toward it', () => {
    const slow = new Fleet(true)
    slow.deliver(slow.image(1, 0), 40, 45, REVIEW_THRESHOLD_PIPELINE_MINUTES + 5)
    const slowA = slow.arrivals(10_000, RECORDED)
    expect(overThreshold(headlineOf(slowA, 1))).toBe(true)
    expect(describeArrival(slowA[0]).marked).toBe(true)

    const held = new Fleet(true)
    held.roll(held.image(1, 0), 'twin-a', 'done', 2, 4)
    held.hold('client-a', 5, 200)
    held.roll('img-1', 'client-a', 'done', 200, 205)
    expect(overThreshold(headlineOf(held.arrivals(10_000, RECORDED), 1))).toBe(false)
  })

  it('p90 is the nearest rank; nothing measured has no p90', () => {
    expect(p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])).toBe(9)
    expect(p90([])).toBeNull()
  })
})

describe('the recent real pattern: 61 image releases over three days', () => {
  // 40 meet a held client seat, 2 a busy one, 16 are clear near 15 minutes, 3 still wait on the twin.
  // Written as the tick writes it: no row for the held seat while held.
  function realPattern(busyRecorded: boolean, holdsRecorded: boolean): Fleet {
    const f = new Fleet(busyRecorded)
    const m = (k: number) => k * 70
    for (let k = 0; k < 16; k++) {
      f.deliver(f.image(k + 1, m(k)), m(k) + 8, m(k) + 10, m(k) + 13 + (k % 5))
    }
    for (let k = 16; k < 18; k++) {
      const r = f.image(k + 1, m(k))
      f.roll(r, 'twin-a', 'done', m(k) + 5, m(k) + 8)
      f.waited(r, 'client-a', 'waiting_idle', [m(k) + 8, m(k) + 18], busyRecorded ? 600 : undefined)
      f.roll(r, 'client-a', 'done', m(k) + 30, m(k) + 35)
    }
    // client-a held from merge 18's twin done until after merge 57: 39 releases overtaken.
    let last = ''
    for (let k = 18; k < 58; k++) {
      last = f.image(k + 1, m(k))
      f.roll(last, 'twin-a', 'done', m(k) + 5, m(k) + 8)
    }
    if (holdsRecorded) f.hold('client-a', m(18) + 8, m(57) + 25)
    f.roll(last, 'client-a', 'done', m(57) + 25, m(57) + 30)
    for (let k = 58; k < 61; k++) f.image(k + 1, m(k))
    for (let k = 0; k < 61; k++) f.roll(`img-${k + 1}`, 'client-b', 'done', m(k) + 15, m(k) + 20)
    return f
  }

  function totals(f: Fleet, since: string | null) {
    const { kinds } = summarizeLeadTime(f.history(), GROUPS, at(61 * 70 + 30), {
      holdHistorySince: since,
    })
    const image = kinds.find((k) => k.kind === 'image')!
    expect(image.headlineWeeks).toHaveLength(1)
    return { week: image.headlineWeeks[0], untwinned: image.untwinnedWeeks[0] }
  }

  it('once holds and busy seconds are recorded: 40 held, 2 busy, 16 clear, clear-case median near 15', () => {
    const { week, untwinned } = totals(realPattern(true, true), RECORDED)
    expect(week).toMatchObject({ merges: 61, held: 40, busy: 2, clear: 16, unrecorded: 0 })
    expect(week.clearMedian).toBe(15)
    expect(untwinned).toMatchObject({ merges: 61, clear: 61, medianPipeline: 20 })
  })

  it('with hold events recorded and busy not yet: 40 held, busy not recorded, clear-case median near 15', () => {
    // The two busy merges' waits stay inside pipeline: they count as clear (18), at 35 minutes each.
    const { week } = totals(realPattern(false, true), RECORDED)
    expect(week).toMatchObject({ merges: 61, arrived: 58, held: 40, busy: null, clear: 18 })
    expect(week.unrecorded).toBe(0)
    expect(week.clearMedian).toBe(15)
    expect(week.medianPipeline).not.toBeNull()
  })

  it('before hold history is recorded: every arrival is not recorded, so no median is claimed', () => {
    const { week } = totals(realPattern(false, false), null)
    expect(week).toMatchObject({
      merges: 61,
      arrived: 58,
      unrecorded: 58,
      held: 0,
      busy: null,
      clear: 0,
      medianPipeline: null,
      p90Pipeline: null,
    })
  })
})
