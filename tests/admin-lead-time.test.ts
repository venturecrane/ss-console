/**
 * Lead time, merge to client seat. Every fixture is built as the D1 rows the
 * fleet writers store (fleet_releases, fleet_rollouts, fleet_holds) with
 * invented release ids and times, parsed through the real parsers, then
 * measured through the real functions. One fixture per risk the measure must
 * survive, and one shaped like the recent real release pattern.
 */

import { describe, it, expect } from 'vitest'
import {
  REVIEW_THRESHOLD_PIPELINE_MINUTES,
  clipUnion,
  overThreshold,
  type Arrival,
} from '../src/lib/admin/lead-time'
import {
  describeArrival,
  mergeArrivals,
  p90,
  summarizeLeadTime,
  type MergeArrival,
} from '../src/lib/admin/lead-time-summary'
import {
  parseHold,
  parseRelease,
  parseRollout,
  type FleetHistory,
  type SeatGroups,
} from '../src/lib/admin/merge-to-seat'

// Monday 2026-11-09 00:00 UTC.
const T0 = Date.UTC(2026, 10, 9, 0, 0, 0)
const at = (minute: number) => T0 + minute * 60_000
const iso = (minute: number) => new Date(at(minute)).toISOString().replace(/\.\d{3}Z$/, 'Z')
const sha = (n: number) => n.toString(16).padStart(8, '0').repeat(5)

const GROUPS: SeatGroups = {
  twinned: ['client-a'],
  untwinned: ['client-b'],
  twins: { 'client-a': 'twin-a' },
}

class Fleet {
  releases: Record<string, unknown>[] = []
  rollouts: Record<string, unknown>[] = []
  holds: Record<string, unknown>[] = []

  /** A CI image built for merge `n` at minute `merged`, recorded `built` minutes later. */
  image(n: number, merged: number, built = 2, notes?: string): string {
    const id = `img-${n}`
    this.releases.push({
      release_id: id,
      kind: 'image',
      ss_console_sha: sha(n),
      notes: notes ?? `merge ${sha(n).slice(0, 8)} (CI)`,
      created_at: iso(merged + built),
      merged_at: iso(merged),
    })
    return id
  }

  bundle(n: number, published: number): string {
    const id = `bnd-${n}`
    this.releases.push({
      release_id: id,
      kind: 'bundle',
      ss_console_sha: sha(n),
      notes: `merge ${sha(n).slice(0, 8)} (CI)`,
      created_at: iso(published),
      merged_at: null,
    })
    return id
  }

  roll(release: string, seat: string, state: string, start: number, end: number | null = start) {
    this.rollouts.push({
      release_id: release,
      customer_slug: seat,
      state,
      // The SQLite space form on purpose for some rows: both forms are stored.
      started_at:
        this.rollouts.length % 2 === 0 ? iso(start) : iso(start).replace('T', ' ').slice(0, 19),
      finished_at: end === null ? null : iso(end),
    })
  }

  /** The twin then the client seat, both done. */
  deliver(release: string, twinDone: number, clientStart: number, clientDone: number) {
    this.roll(release, 'twin-a', 'done', twinDone - 3, twinDone)
    this.roll(release, 'client-a', 'done', clientStart, clientDone)
  }

  hold(seat: string, heldAt: number) {
    this.holds.push({ customer_slug: seat, held_at: iso(heldAt), reason: 'r', held_by: 'x' })
  }

  history(): FleetHistory {
    const keep = <T>(rows: unknown[], parse: (r: unknown) => T | null): T[] =>
      rows.map(parse).filter((r): r is T => r !== null)
    return {
      releases: keep(this.releases, parseRelease),
      rollouts: keep(this.rollouts, parseRollout),
      holds: keep(this.holds, parseHold),
    }
  }

  arrivals(now = 10_000): MergeArrival[] {
    return mergeArrivals(this.history(), GROUPS, at(now))
  }
}

function headlineOf(arrivals: MergeArrival[], n: number, kind = 'image'): Arrival {
  const found = arrivals.find((a) => a.merge.sha === sha(n) && a.merge.kind === kind)
  if (!found) throw new Error(`no ${kind} merge ${n}`)
  return found.headline
}

function arrived(a: Arrival) {
  if (a.status !== 'arrived') throw new Error(`expected arrived, got ${a.status}`)
  return a
}

function waiting(a: Arrival) {
  if (a.status !== 'in_progress') throw new Error(`expected in_progress, got ${a.status}`)
  return a
}

describe('arrival', () => {
  it('a clear release: arrival at the last client seat done, all of it pipeline', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0), 8, 10, 15)
    const a = arrived(headlineOf(f.arrivals(), 1))
    expect(a).toMatchObject({ atMs: at(15), lastSeat: 'client-a', totalMinutes: 15 })
    expect(a.split).toEqual({ pipelineMinutes: 15, heldMinutes: 0, busyMinutes: 0 })
  })

  it('a release overtaken before the client took it: its merge counts against the newer release', () => {
    const f = new Fleet()
    const r1 = f.image(1, 0)
    f.roll(r1, 'twin-a', 'done', 5, 8)
    f.deliver(f.image(2, 10), 18, 20, 25)
    const arrivals = f.arrivals()
    expect(arrived(headlineOf(arrivals, 1))).toMatchObject({ atMs: at(25), totalMinutes: 25 })
    expect(arrived(headlineOf(arrivals, 2))).toMatchObject({ atMs: at(25), totalMinutes: 15 })
  })

  it('two merges a minute apart carried by one release arrive together with different pipeline minutes', () => {
    const f = new Fleet()
    f.image(1, 0)
    f.deliver(f.image(2, 1), 12, 14, 20)
    const arrivals = f.arrivals()
    expect(arrived(headlineOf(arrivals, 1)).split.pipelineMinutes).toBe(20)
    expect(arrived(headlineOf(arrivals, 2)).split.pipelineMinutes).toBe(19)
    expect(arrived(headlineOf(arrivals, 1)).atMs).toBe(arrived(headlineOf(arrivals, 2)).atMs)
  })

  it('a hotfix release built later but off main line never delivers a main merge, and is no merge itself', () => {
    const f = new Fleet()
    f.image(1, 0)
    // Built by hand from a hotfix branch, later in time than merge 1, without it.
    const hot = f.image(2, 30, 2, 'hotfix lane: client-a only')
    f.deliver(hot, 35, 36, 40)
    // A CI-shaped note whose sha does not match the commit is not main line either.
    f.deliver(f.image(3, 50, 2, `merge ${sha(9).slice(0, 8)} (CI)`), 55, 56, 60)
    const arrivals = f.arrivals(100)
    expect(waiting(headlineOf(arrivals, 1)).waitingOn).toEqual({ reason: 'twin', seat: 'twin-a' })
    expect(arrivals.map((a) => a.merge.sha)).toEqual([sha(1)])
  })

  it('a seat that ended tooling_error then done arrives at the done, with no held or busy time', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 4, 6)
    f.roll(r, 'client-a', 'tooling_error', 8, 10)
    f.roll(r, 'client-a', 'done', 20, 25)
    const a = arrived(headlineOf(f.arrivals(), 1))
    expect(a.atMs).toBe(at(25))
    expect(a.split).toEqual({ pipelineMinutes: 25, heldMinutes: 0, busyMinutes: 0 })
  })

  it('the untwinned group is measured on its own and never moves the headline', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.deliver(r, 8, 10, 15)
    f.roll(r, 'client-b', 'done', 90, 100)
    const [m] = f.arrivals()
    expect(arrived(m.headline).totalMinutes).toBe(15)
    expect(arrived(m.untwinned).totalMinutes).toBe(100)
  })

  it('no client seat in a group is no arrival, never a vacuous zero', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0), 8, 10, 15)
    const [m] = mergeArrivals(f.history(), { twinned: [], untwinned: [], twins: {} }, at(100))
    expect(m.headline.status).toBe('no_seats')
  })
})

describe('the split', () => {
  it('a hold placed and lifted twice during one arrival counts both stretches', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 1, 3)
    f.roll(r, 'client-a', 'skipped_held', 5, 5)
    f.roll(r, 'client-a', 'tooling_error', 20, 22)
    f.roll(r, 'client-a', 'skipped_held', 30, 30)
    f.roll(r, 'client-a', 'done', 50, 55)
    const a = arrived(headlineOf(f.arrivals(), 1))
    expect(a.split).toEqual({ pipelineMinutes: 20, heldMinutes: 35, busyMinutes: 0 })
  })

  it('a hold still in place now: in progress, waiting on the hold, held time running to now', () => {
    const f = new Fleet()
    f.roll(f.image(1, 0), 'twin-a', 'done', 5, 8)
    f.hold('client-a', 10)
    const a = waiting(headlineOf(f.arrivals(100), 1))
    expect(a.waitingOn).toEqual({ reason: 'hold', seat: 'client-a' })
    expect(a.elapsedMinutes).toBe(100)
    expect(a.split.heldMinutes).toBe(90)
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

  it('a seat busy at the first visit and done at a later one: busy is the wait, not a count of rows', () => {
    const once = new Fleet()
    const r1 = once.image(1, 0)
    once.roll(r1, 'twin-a', 'done', 4, 6)
    once.roll(r1, 'client-a', 'waiting_idle', 8, 18)
    once.roll(r1, 'client-a', 'done', 30, 35)

    const often = new Fleet()
    const r2 = often.image(1, 0)
    often.roll(r2, 'twin-a', 'done', 4, 6)
    for (const start of [8, 13, 18, 23])
      often.roll(r2, 'client-a', 'waiting_idle', start, start + 4)
    often.roll(r2, 'client-a', 'done', 30, 35)

    const expected = { pipelineMinutes: 13, heldMinutes: 0, busyMinutes: 22 }
    expect(arrived(headlineOf(once.arrivals(), 1)).split).toEqual(expected)
    expect(arrived(headlineOf(often.arrivals(), 1)).split).toEqual(expected)
  })

  it('a seat still busy now is what the merge waits on', () => {
    const f = new Fleet()
    const r = f.image(1, 0)
    f.roll(r, 'twin-a', 'done', 4, 6)
    f.roll(r, 'client-a', 'waiting_idle', 8, 18)
    const a = waiting(headlineOf(f.arrivals(40), 1))
    expect(a.waitingOn).toEqual({ reason: 'busy', seat: 'client-a' })
    expect(a.split.busyMinutes).toBe(32)
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

describe('kinds and empty states', () => {
  it('a merge that built no image reads "no release"; one that built nothing at all is not listed', () => {
    const f = new Fleet()
    const b = f.bundle(5, 3)
    f.roll(b, 'client-a', 'done', 6, 9)
    f.roll(b, 'twin-a', 'done', 4, 5)
    const { recent } = summarizeLeadTime(f.history(), GROUPS, at(100))
    expect(recent).toHaveLength(1)
    expect(describeArrival(recent[0].byKind.image)).toEqual({ text: 'no release', marked: false })
    expect(recent[0].clockFrom).toBe('publish')
    expect(describeArrival(recent[0].byKind.bundle).text).toBe('6 min (6 pipeline, 0 held, 0 busy)')
  })

  it('a bundle is not waited for on a seat that has never taken a bundle; its clock is the image merge when the commit built one', () => {
    const f = new Fleet()
    f.deliver(f.image(1, 0), 8, 10, 15)
    const b = f.bundle(1, 4)
    f.roll(b, 'client-a', 'done', 12, 20)
    const arrivals = f.arrivals()
    const bundle = arrivals.find((a) => a.merge.kind === 'bundle')
    expect(bundle?.merge.clockFrom).toBe('merge')
    expect(arrived(bundle!.headline).totalMinutes).toBe(20)
    expect(bundle!.untwinned.status).toBe('no_seats')
  })

  it('an arrival over the review threshold of pipeline time is marked; held time does not count toward it', () => {
    const slow = new Fleet()
    slow.deliver(slow.image(1, 0), 40, 45, REVIEW_THRESHOLD_PIPELINE_MINUTES + 5)
    const slowA = slow.arrivals()
    expect(overThreshold(headlineOf(slowA, 1))).toBe(true)
    expect(describeArrival(slowA[0]).marked).toBe(true)

    const held = new Fleet()
    const r = held.image(1, 0)
    held.roll(r, 'twin-a', 'done', 2, 4)
    held.roll(r, 'client-a', 'skipped_held', 5, 5)
    held.roll(r, 'client-a', 'done', 200, 205)
    expect(overThreshold(headlineOf(held.arrivals(), 1))).toBe(false)
  })

  it('p90 is the nearest rank; nothing measured has no p90', () => {
    expect(p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])).toBe(9)
    expect(p90([])).toBeNull()
  })
})

describe('the recent real pattern: 61 image releases over three days', () => {
  // 40 meet a held client seat, 2 a busy one, 16 are clear near 15 minutes, 3 still wait on the twin.
  function realPattern(): Fleet {
    const f = new Fleet()
    const step = 70
    const m = (k: number) => k * step
    for (let k = 0; k < 16; k++)
      f.deliver(f.image(k + 1, m(k)), m(k) + 8, m(k) + 10, m(k) + 13 + (k % 5))
    for (let k = 16; k < 18; k++) {
      const r = f.image(k + 1, m(k))
      f.roll(r, 'twin-a', 'done', m(k) + 5, m(k) + 8)
      f.roll(r, 'client-a', 'waiting_idle', m(k) + 8, m(k) + 18)
      f.roll(r, 'client-a', 'done', m(k) + 30, m(k) + 35)
    }
    // A hold from merge 18's rollout until after merge 57: the 39 releases between are overtaken.
    let held = ''
    for (let k = 18; k < 58; k++) {
      held = f.image(k + 1, m(k))
      f.roll(held, 'twin-a', 'done', m(k) + 5, m(k) + 8)
      if (k === 18) f.roll(held, 'client-a', 'skipped_held', m(k) + 8, m(k) + 8)
    }
    f.roll(held, 'client-a', 'done', m(57) + 25, m(57) + 30)
    for (let k = 58; k < 61; k++) f.image(k + 1, m(k))
    for (let k = 0; k < 61; k++) f.roll(`img-${k + 1}`, 'client-b', 'done', m(k) + 15, m(k) + 20)
    return f
  }

  it('reports 40 held, 2 busy, 16 clear and a clear-case median near 15 minutes', () => {
    const f = realPattern()
    const { kinds, recent } = summarizeLeadTime(f.history(), GROUPS, at(61 * 70 + 30))
    const image = kinds.find((k) => k.kind === 'image')!
    const totals = image.headlineWeeks.reduce(
      (t, w) => ({
        merges: t.merges + w.merges,
        held: t.held + w.held,
        busy: t.busy + w.busy,
        clear: t.clear + w.clear,
      }),
      { merges: 0, held: 0, busy: 0, clear: 0 }
    )
    expect(totals).toEqual({ merges: 61, held: 40, busy: 2, clear: 16 })
    expect(image.headlineWeeks).toHaveLength(1)
    expect(image.headlineWeeks[0].clearMedian).toBe(15)
    expect(image.untwinnedWeeks[0]).toMatchObject({ merges: 61, clear: 61, medianPipeline: 20 })
    expect(kinds.find((k) => k.kind === 'bundle')!.headlineWeeks).toEqual([])
    expect(recent).toHaveLength(10)
    expect(describeArrival(recent[0].byKind.image).text).toMatch(
      /^waiting on the release twin \(twin-a\)/
    )
  })
})
