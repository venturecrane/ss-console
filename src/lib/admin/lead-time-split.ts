/**
 * The split of one arrival's minutes into pipeline, held and busy, or the
 * honest answer that the fleet did not record what the split needs.
 *
 * HELD comes only from hold intervals ({seat, heldAtMs, liftedAtMs}). Today the
 * only source is `fleet_holds`, which holds the holds in place now: a hold
 * placed and lifted is deleted, and the tick never writes a rollout row for a
 * held seat (tick.py `_front` leaves it out of the seats it rolls), so past
 * holds leave no trace. Until the fleet records hold events, the split of any
 * window that starts before HOLD_HISTORY_RECORDED_SINCE (or always, while it is
 * null) is not recorded.
 *
 * BUSY comes only from each attempt's `busySeconds`: the seconds it waited on a
 * client job. A rollout waits for idle inside a row that can end `done`, and a
 * `waiting_idle` row can mean another run holds the seat's lease, which is not
 * a client job; neither is recoverable from states. So any attempt in the
 * window with no recorded busy seconds makes the split not recorded. An attempt
 * that ended `waiting_idle` with busy seconds above zero was still busy when it
 * gave up: its busy stretch runs on to the seat's next attempt. One with zero
 * (a lease wait) is not busy.
 *
 * A release twin gates its client only until the twin's first `done` on a
 * carrier, so its intervals are clipped there.
 *
 * Rounding: computed in milliseconds and rounded once, cumulatively: held is
 * round(held), held+busy is round(held+busy), total is round(total), so
 * pipeline + held + busy equals the total exactly, and pipeline cannot go
 * below zero because held and busy are disjoint stretches inside the window.
 */

import type { HoldInterval, RolloutRow } from './merge-to-seat'

/**
 * The first moment the fleet records every hold placed and lifted (ISO UTC), or
 * null while it does not. The follow-on change that adds hold events sets it.
 */
export const HOLD_HISTORY_RECORDED_SINCE: string | null = null

const MINUTE_MS = 60_000

export type Split =
  | {
      recorded: true
      totalMinutes: number
      pipelineMinutes: number
      heldMinutes: number
      busyMinutes: number
    }
  | { recorded: false; totalMinutes: number; missing: 'hold history' | 'busy time' }

/** One seat whose holds and busy waits count, up to `untilMs` (a twin: its first done). */
export interface Track {
  seat: string
  untilMs: number
}

export interface SplitInput {
  tracks: Track[]
  fromMs: number
  toMs: number
  bySeat: Map<string, RolloutRow[]>
  holds: HoldInterval[]
  holdHistorySinceMs: number | null
}

type Interval = [number, number]

/** The union of `intervals` clipped to [from, to], as sorted disjoint intervals. */
export function clipUnion(intervals: Interval[], from: number, to: number): Interval[] {
  const clipped = intervals
    .map(([s, e]): Interval => [Math.max(s, from), Math.min(e, to)])
    .filter(([s, e]) => e > s)
    .sort((a, b) => a[0] - b[0])
  const out: Interval[] = []
  for (const [s, e] of clipped) {
    const last = out.at(-1)
    if (last && s <= last[1]) last[1] = Math.max(last[1], e)
    else out.push([s, e])
  }
  return out
}

function measure(intervals: Interval[]): number {
  return intervals.reduce((sum, [s, e]) => sum + (e - s), 0)
}

function overlap(a: Interval[], b: Interval[]): number {
  let total = 0
  for (const [as, ae] of a) {
    for (const [bs, be] of b) total += Math.max(0, Math.min(ae, be) - Math.max(as, bs))
  }
  return total
}

/** Busy stretches of one seat's attempts overlapping [from, to], or null when one has none recorded. */
function busyIntervals(rows: RolloutRow[], from: number, to: number): Interval[] | null {
  const out: Interval[] = []
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    const next = rows[i + 1]?.startedAtMs ?? Infinity
    const stillBusy = row.state === 'waiting_idle'
    const rowEnd = stillBusy ? next : (row.finishedAtMs ?? next)
    if (row.startedAtMs >= to || rowEnd <= from) continue
    if (row.busySeconds === null) return null
    if (row.busySeconds === 0) continue
    out.push([row.startedAtMs, stillBusy ? next : row.startedAtMs + row.busySeconds * 1000])
  }
  return out
}

function minutes(ms: number): number {
  return Math.round(ms / MINUTE_MS)
}

/** Pipeline, held and busy minutes over [from, to], or why they are not recorded. */
export function splitFor(input: SplitInput): Split {
  const { fromMs, toMs } = input
  const totalMinutes = minutes(Math.max(0, toMs - fromMs))
  if (input.holdHistorySinceMs === null || fromMs < input.holdHistorySinceMs) {
    return { recorded: false, totalMinutes, missing: 'hold history' }
  }
  const held: Interval[] = []
  const busy: Interval[] = []
  for (const track of input.tracks) {
    const end = Math.min(toMs, track.untilMs)
    for (const h of input.holds) {
      if (h.seat === track.seat) {
        held.push(...clipUnion([[h.heldAtMs, h.liftedAtMs ?? Infinity]], fromMs, end))
      }
    }
    const waits = busyIntervals(input.bySeat.get(track.seat) ?? [], fromMs, end)
    if (waits === null) return { recorded: false, totalMinutes, missing: 'busy time' }
    busy.push(...clipUnion(waits, fromMs, end))
  }
  const heldU = clipUnion(held, fromMs, toMs)
  const busyU = clipUnion(busy, fromMs, toMs)
  const heldMs = measure(heldU)
  const busyMs = measure(busyU) - overlap(busyU, heldU)
  const heldMinutes = minutes(heldMs)
  const heldAndBusy = minutes(heldMs + busyMs)
  return {
    recorded: true,
    totalMinutes,
    heldMinutes,
    busyMinutes: heldAndBusy - heldMinutes,
    pipelineMinutes: totalMinutes - heldAndBusy,
  }
}
