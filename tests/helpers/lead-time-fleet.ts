/**
 * The fixture builder for the lead-time tests: D1 rows exactly as the fleet
 * writers store them (fleet_releases, fleet_rollouts, fleet_holds), with
 * invented release ids and times, parsed through the real parsers.
 *
 * What the writers actually write shapes it: the tick never writes a rollout
 * row for a held seat (tick.py `_front` leaves it out), `fleet unhold` deletes
 * the hold row, and no writer records busy seconds or when a merge reached main
 * yet. `new Fleet(true)`, `hold(seat, at, lifted)` and `image(..., reachedMain)`
 * supply `busy_seconds`, `lifted_at` and `reached_main_at` as the follow-on
 * fleet change will; a test that uses them says so.
 */

import type { Arrival } from '../../src/lib/admin/lead-time'
import type { Split } from '../../src/lib/admin/lead-time-split'
import { mergeArrivals, type MergeArrival } from '../../src/lib/admin/lead-time-summary'
import {
  parseHold,
  parseRelease,
  parseRollout,
  type FleetHistory,
  type SeatGroups,
} from '../../src/lib/admin/merge-to-seat'

// Monday 2026-11-09 00:00 UTC.
const T0 = Date.UTC(2026, 10, 9, 0, 0, 0)
export const at = (minute: number) => T0 + Math.round(minute * 60_000)
export const iso = (minute: number) => new Date(at(minute)).toISOString().replace(/\.\d{3}Z$/, 'Z')
export const sha = (n: number) => n.toString(16).padStart(8, '0').repeat(5)
/** Hold history recorded since before every fixture: the follow-on's state. */
export const RECORDED = '2026-11-01T00:00:00Z'

export const GROUPS: SeatGroups = {
  twinned: ['client-a'],
  untwinned: ['client-b'],
  twins: { 'client-a': 'twin-a' },
}

export class Fleet {
  releases: Record<string, unknown>[] = []
  rollouts: Record<string, unknown>[] = []
  holds: Record<string, unknown>[] = []

  /** `recordBusy`: every row carries busy_seconds (0 unless given), as the follow-on writer will. */
  constructor(private readonly recordBusy = false) {
    // Every seat took a release before any merge: a hand-built baseline, never a merge itself.
    const base = this.image(0, -300, 2, 'baseline image')
    for (const seat of ['twin-a', 'client-a', 'client-b']) this.roll(base, seat, 'done', -200, -195)
  }

  image(n: number, merged: number, built = 2, notes?: string, reachedMain?: number): string {
    const id = `img-${n}`
    this.releases.push({
      release_id: id,
      kind: 'image',
      ss_console_sha: sha(n),
      notes: notes ?? `merge ${sha(n).slice(0, 8)} (CI)`,
      created_at: iso(merged + built),
      merged_at: iso(merged),
      ...(reachedMain === undefined ? {} : { reached_main_at: iso(reachedMain) }),
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
    this.waited(release, seat, state, [start, end], undefined)
  }

  /** An attempt with its recorded busy seconds (undefined: as the Fleet records busy, or not). */
  waited(
    release: string,
    seat: string,
    state: string,
    [start, end]: [number, number | null],
    busySeconds: number | undefined
  ) {
    const busy = busySeconds ?? (this.recordBusy ? 0 : undefined)
    this.rollouts.push({
      release_id: release,
      customer_slug: seat,
      state,
      // Both stored forms, alternating: the SQLite space form and the Z form.
      started_at:
        this.rollouts.length % 2 === 0 ? iso(start) : iso(start).replace('T', ' ').slice(0, 19),
      finished_at: end === null ? null : iso(end),
      ...(busy === undefined ? {} : { busy_seconds: busy }),
    })
  }

  /** The twin then the client seat, both done. */
  deliver(release: string, twinDone: number, clientStart: number, clientDone: number) {
    this.roll(release, 'twin-a', 'done', twinDone - 3, twinDone)
    this.roll(release, 'client-a', 'done', clientStart, clientDone)
  }

  /** A hold. With `lifted`, a hold-events row the follow-on records; without, a fleet_holds row in place now. */
  hold(seat: string, heldAt: number, lifted?: number) {
    this.holds.push({
      customer_slug: seat,
      held_at: iso(heldAt),
      ...(lifted === undefined ? {} : { lifted_at: iso(lifted) }),
    })
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

  arrivals(now = 10_000, since: string | null = null, groups = GROUPS): MergeArrival[] {
    return mergeArrivals(this.history(), groups, at(now), { holdHistorySince: since })
  }
}

export function headlineOf(arrivals: MergeArrival[], n: number, kind = 'image'): Arrival {
  const found = arrivals.find((a) => a.merge.sha === sha(n) && a.merge.kind === kind)
  if (!found) throw new Error(`no ${kind} merge ${n}`)
  return found.headline
}

export function arrived(a: Arrival) {
  if (a.status !== 'arrived') throw new Error(`expected arrived, got ${a.status}`)
  return a
}

export function waiting(a: Arrival) {
  if (a.status !== 'in_progress') throw new Error(`expected in_progress, got ${a.status}`)
  return a
}

export const split = (total: number, pipeline: number, held: number, busy: number): Split => ({
  recorded: true,
  totalMinutes: total,
  pipelineMinutes: pipeline,
  heldMinutes: held,
  busyMinutes: busy,
})
