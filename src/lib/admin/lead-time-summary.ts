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
 * whose split was RECORDED (hold history covered the window); an earlier one
 * counts as "not recorded" and never enters them. Held counts every merge known
 * to have met a hold (a recorded split, or a merge waiting on one now); clear
 * counts recorded arrivals with no hold and no recorded busy wait. Busy waits
 * are not recorded yet, so a week's busy count is null rather than a zero it
 * cannot vouch for, and pipeline includes any wait on a client job.
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
  /** Arrived before hold history was recorded: total only, not in the median. */
  unrecorded: number
  held: number
  /** Null when busy waits were not recorded for some merge of the week. */
  busy: number | null
  /** Recorded arrivals with no hold and no recorded busy wait. */
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
  /** When hold history began (ISO UTC; `readHoldHistory`), or null/absent when it is not recorded. */
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

function busyOf(a: Arrival): number | null {
  return a.status !== 'no_seats' && a.split.recorded ? a.split.busyMinutes : null
}

/** How an arrival counts in the week. An unknown busy wait never makes a merge "busy". */
function classify(a: Arrival): Class {
  if (a.status === 'no_seats') return 'none'
  const held = a.split.recorded && a.split.heldMinutes > 0
  const busy = (busyOf(a) ?? 0) > 0
  if (a.status === 'in_progress') {
    if (a.waitingOn.reason === 'hold' || held) return 'held'
    return a.waitingOn.reason === 'busy' || busy ? 'busy' : 'waiting'
  }
  if (!a.split.recorded) return 'unrecorded'
  if (held) return 'held'
  return busy ? 'busy' : 'clear'
}

function weekStats(weekStart: number, arrivals: Arrival[]): WeekStats {
  const pipeline: number[] = []
  const clear: number[] = []
  const counts = { held: 0, busy: 0, unrecorded: 0, arrived: 0 }
  // Busy is a count only when some split was recorded and every recorded one carries busy minutes.
  let sawRecorded = false
  let busyKnown = true
  for (const a of arrivals) {
    const c = classify(a)
    if (c === 'held' || c === 'busy' || c === 'unrecorded') counts[c]++
    if (a.status !== 'no_seats' && a.split.recorded) {
      sawRecorded = true
      if (a.split.busyMinutes === null) busyKnown = false
    }
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
    busy: sawRecorded && busyKnown ? counts.busy : null,
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
  const since = options.holdHistorySince ?? null
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
  idle_wait: (seat) => `waiting for the seat to be free (${seat}: a client job or another deploy)`,
  twin: (seat) => `waiting on the release twin (${seat})`,
  nothing: () => 'in the pipeline',
}

/** One cell of the recent merges list, in words; `marked` when over the review threshold. */
export function describeArrival(a: MergeArrival | null): { text: string; marked: boolean } {
  if (a === null) return { text: 'no release', marked: false }
  const arrival = a.headline
  if (arrival.status === 'no_seats') return { text: 'no client seat takes it', marked: false }
  const split = arrival.split
  const parts = !split.recorded
    ? 'hold time not recorded'
    : split.busyMinutes === null
      ? `${split.pipelineMinutes} pipeline, including any wait on a client job; ${split.heldMinutes} held`
      : `${split.pipelineMinutes} pipeline, ${split.heldMinutes} held, ${split.busyMinutes} busy`
  if (arrival.status === 'in_progress') {
    const wait = WAIT_TEXT[arrival.waitingOn.reason](arrival.waitingOn.seat)
    return { text: `${wait}, ${split.totalMinutes} min so far (${parts})`, marked: false }
  }
  return { text: `${split.totalMinutes} min (${parts})`, marked: overThreshold(arrival) }
}
