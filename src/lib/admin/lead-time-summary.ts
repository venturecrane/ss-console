/**
 * Lead time for the page: every merge's arrival per seat group, a weekly
 * summary per artifact kind, and the recent merges list (the admin Fleet
 * page's "Merge to seat" card). Pure; the measuring is `lead-time.ts`.
 *
 * Per kind, never combined: D1 cannot tell which kinds a merge changed (a merge
 * whose image build was skipped because main moved on has no image row, yet
 * its change rides the next image), so a merge's kinds are reported side by
 * side, and a kind with no row for that commit reads "no release".
 *
 * A week is the ISO week (Monday, UTC) in which the change merged. Its median
 * and slow case (p90, nearest rank) are over the pipeline minutes of arrivals
 * whose split was RECORDED; an arrival whose held and busy minutes the fleet
 * did not record counts as "not recorded" and never enters them. Held and busy
 * count every merge known to have met a hold or a busy seat (a recorded split,
 * or a merge waiting on one now); clear counts recorded arrivals with neither.
 * A week with nothing measured has no median, never a zero.
 */

import {
  arrivalOf,
  carriersOf,
  mergesOf,
  overThreshold,
  seatContext,
  type Arrival,
  type ClockFrom,
  type Merge,
  type WaitingOn,
} from './lead-time'
import { HOLD_HISTORY_RECORDED_SINCE } from './lead-time-split'
import {
  ARTIFACT_KINDS,
  WEEKS_SHOWN,
  weekStartMs,
  type ArtifactKind,
  type FleetHistory,
  type SeatGroups,
} from './merge-to-seat'

export interface MergeArrival {
  merge: Merge
  /** The headline group: client seats gated by a release twin. */
  headline: Arrival
  /** Client seats with no release twin. */
  untwinned: Arrival
}

export interface WeekStats {
  weekStart: string
  merges: number
  arrived: number
  /** Arrived, but held and busy minutes were not recorded: no split, not in the median. */
  unrecorded: number
  held: number
  busy: number
  clear: number
  medianPipeline: number | null
  p90Pipeline: number | null
  /** Median pipeline minutes of the clear arrivals only. */
  clearMedian: number | null
}

export interface KindSummary {
  kind: ArtifactKind
  headlineWeeks: WeekStats[]
  untwinnedWeeks: WeekStats[]
}

export interface RecentMerge {
  sha: string
  shortSha: string
  startMs: number
  clockFrom: ClockFrom
  /** Per kind: the arrival, or null when this commit built no release of that kind. */
  byKind: Record<ArtifactKind, MergeArrival | null>
}

export interface LeadTimeSummary {
  kinds: KindSummary[]
  recent: RecentMerge[]
}

export interface LeadTimeOptions {
  /** Overrides HOLD_HISTORY_RECORDED_SINCE (ISO UTC, or null). */
  holdHistorySince?: string | null
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000
const RECENT_SHOWN = 10

function median(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2)
}

/** The nearest-rank 90th percentile. */
export function p90(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.ceil(0.9 * sorted.length) - 1]
}

type Class = 'held' | 'busy' | 'clear' | 'unrecorded' | 'waiting' | 'none'

/** How an arrival counts in the week. */
function classify(a: Arrival): Class {
  if (a.status === 'no_seats') return 'none'
  if (a.status === 'in_progress') {
    const reason = a.waitingOn.reason
    if (reason === 'hold' || (a.split.recorded && a.split.heldMinutes > 0)) return 'held'
    if (reason === 'busy' || reason === 'idle_wait') return 'busy'
    return a.split.recorded && a.split.busyMinutes > 0 ? 'busy' : 'waiting'
  }
  if (!a.split.recorded) return 'unrecorded'
  if (a.split.heldMinutes > 0) return 'held'
  return a.split.busyMinutes > 0 ? 'busy' : 'clear'
}

function weekStats(weekStart: number, arrivals: Arrival[]): WeekStats {
  const pipeline: number[] = []
  const clear: number[] = []
  const counts = { held: 0, busy: 0, unrecorded: 0, arrived: 0 }
  for (const a of arrivals) {
    const c = classify(a)
    if (c === 'held' || c === 'busy' || c === 'unrecorded') counts[c]++
    if (a.status !== 'arrived') continue
    counts.arrived++
    if (!a.split.recorded) continue
    pipeline.push(a.split.pipelineMinutes)
    if (c === 'clear') clear.push(a.split.pipelineMinutes)
  }
  return {
    weekStart: new Date(weekStart).toISOString().slice(0, 10),
    merges: arrivals.length,
    ...counts,
    clear: clear.length,
    medianPipeline: median(pipeline),
    p90Pipeline: p90(pipeline),
    clearMedian: median(clear),
  }
}

/** Per ISO week of the merge (newest first, the weeks shown, only weeks with a merge seen). */
function weeklyStats(rows: { merge: Merge; arrival: Arrival }[], nowMs: number): WeekStats[] {
  const oldest = weekStartMs(nowMs) - (WEEKS_SHOWN - 1) * WEEK_MS
  const byWeek = new Map<number, Arrival[]>()
  for (const { merge, arrival } of rows) {
    const week = weekStartMs(merge.startMs)
    if (week < oldest || arrival.status === 'no_seats') continue
    byWeek.set(week, [...(byWeek.get(week) ?? []), arrival])
  }
  return [...byWeek.entries()].sort((a, b) => b[0] - a[0]).map(([w, list]) => weekStats(w, list))
}

/** Every merge of every kind with its arrival in both seat groups, newest first. */
export function mergeArrivals(
  history: FleetHistory,
  groups: SeatGroups,
  nowMs: number,
  options: LeadTimeOptions = {}
): MergeArrival[] {
  const since =
    options.holdHistorySince === undefined ? HOLD_HISTORY_RECORDED_SINCE : options.holdHistorySince
  const out: MergeArrival[] = []
  // All kinds at once: a bundle or skills merge takes its clock from the same commit's image row.
  const merges = mergesOf(history.releases)
  for (const kind of ARTIFACT_KINDS) {
    const ctx = seatContext(history, kind, groups.twins, since)
    const releases = history.releases.filter((r) => r.kind === kind)
    for (const merge of merges.filter((m) => m.kind === kind)) {
      const carriers = carriersOf(merge, releases)
      out.push({
        merge,
        headline: arrivalOf(merge, groups.twinned, carriers, ctx, nowMs),
        untwinned: arrivalOf(merge, groups.untwinned, carriers, ctx, nowMs),
      })
    }
  }
  return out.sort((a, b) => b.merge.startMs - a.merge.startMs)
}

function recentMerges(arrivals: MergeArrival[]): RecentMerge[] {
  const bySha = new Map<string, RecentMerge>()
  for (const a of arrivals) {
    let entry = bySha.get(a.merge.sha)
    if (!entry) {
      entry = {
        sha: a.merge.sha,
        shortSha: a.merge.sha.slice(0, 8),
        startMs: a.merge.startMs,
        clockFrom: a.merge.clockFrom,
        byKind: { image: null, bundle: null, skills: null },
      }
      bySha.set(a.merge.sha, entry)
    }
    entry.byKind[a.merge.kind] = a
  }
  return [...bySha.values()].sort((a, b) => b.startMs - a.startMs).slice(0, RECENT_SHOWN)
}

/** The whole card: weekly stats per kind and group, and the recent merges. */
export function summarizeLeadTime(
  history: FleetHistory,
  groups: SeatGroups,
  nowMs: number,
  options: LeadTimeOptions = {}
): LeadTimeSummary {
  const arrivals = mergeArrivals(history, groups, nowMs, options)
  const kinds = ARTIFACT_KINDS.map((kind) => {
    const mine = arrivals.filter((a) => a.merge.kind === kind)
    const pick = (group: 'headline' | 'untwinned') =>
      weeklyStats(
        mine.map((a) => ({ merge: a.merge, arrival: a[group] })),
        nowMs
      )
    return { kind, headlineWeeks: pick('headline'), untwinnedWeeks: pick('untwinned') }
  })
  return { kinds, recent: recentMerges(arrivals) }
}

const WAIT_TEXT: Record<WaitingOn['reason'], (seat: string) => string> = {
  hold: (seat) => `waiting on a hold (${seat})`,
  busy: (seat) => `waiting on a busy seat (${seat})`,
  idle_wait: (seat) => `waiting for ${seat} to be free (a client job or another deploy)`,
  twin: (seat) => `waiting on the release twin (${seat})`,
  nothing: () => 'in the pipeline',
}

/** One cell of the recent merges list, in words; `marked` when over the review threshold. */
export function describeArrival(a: MergeArrival | null): { text: string; marked: boolean } {
  if (a === null) return { text: 'no release', marked: false }
  const arrival = a.headline
  if (arrival.status === 'no_seats') return { text: 'no client seat takes it', marked: false }
  const split = arrival.split
  const parts = split.recorded
    ? `${split.pipelineMinutes} pipeline, ${split.heldMinutes} held, ${split.busyMinutes} busy`
    : 'held and busy minutes not recorded'
  if (arrival.status === 'in_progress') {
    const wait = WAIT_TEXT[arrival.waitingOn.reason](arrival.waitingOn.seat)
    return { text: `${wait}, ${split.totalMinutes} min so far (${parts})`, marked: false }
  }
  return { text: `${split.totalMinutes} min (${parts})`, marked: overThreshold(arrival) }
}
