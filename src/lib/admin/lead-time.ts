/**
 * Lead time, merge to client seat: for every merged change, when it reached
 * the LAST client seat of a group, split into pipeline, held and busy minutes.
 * Pure: every input is a parsed row from `merge-to-seat.ts`.
 *
 * WHICH MERGES. A merge is a commit recorded on a release that CI built from
 * main: an image or bundle row whose notes read `merge <sha8> (CI)` with that
 * sha8 the start of its `ss_console_sha` (deploy.yml `fleet build --notes`,
 * ci-publish-bundle.sh `bundle-record --notes`), or a skills row whose notes
 * read `published by CI` (ci-publish-skills.sh). A hand build (a hotfix lane
 * built with SS_ALLOW_DIVERGENT_SOURCE, a laptop `fleet build`) carries other
 * notes and is neither a merge nor a carrier, so a hotfix image that lacks a
 * merge can never count as delivering it. A merge whose CI build was skipped
 * (no path of that kind changed, or main moved past it first) has no row, and
 * D1 cannot see it.
 *
 * WHICH RELEASES CONTAIN A MERGE. D1 records no parent or commit number for a
 * release, so "contains" is decided by the order of main's line among CI rows
 * of the SAME kind, never by mixing kinds:
 *   image   by `merged_at` (the merge commit's committer time `fleet build`
 *           records; main is linear and each CI build runs at its own merge),
 *           then `created_at`. An image row with no `merged_at` (built before
 *           migration 0148) cannot be placed and is left out.
 *   bundle  by `created_at` (publish-bundle runs in one concurrency group, so
 *           it records in merge order).
 *   skills  by `created_at` (publish-skills has no concurrency group; two
 *           skills merges seconds apart could record out of order).
 * A release contains merge M when its (order, created_at) is at or after M's.
 *
 * ARRIVAL. Per seat, the first `done` rollout of any release containing M; the
 * merge arrived for a group when every seat of the group has one, at the
 * latest of those (the last seat, the one a client feels). A release overtaken
 * before a seat took it is simply not a done row there; the newer release that
 * carried M is. Bundles go only to seats that author a job lane, so for a
 * bundle a seat counts only if it has any bundle rollout row in the history.
 *
 * THE SPLIT. The clock starts at the image row's `merged_at` (for a bundle or
 * skills merge, the same commit's image `merged_at` when it has one, else the
 * release's own `created_at`, labelled "from publish"). Over [start, arrival],
 * for the last seat and its release twin:
 *   held   time a current hold was in place (`fleet_holds.held_at` to now), or
 *          the seat's latest rollout row of this kind was `skipped_held`
 *   busy   time the seat's latest rollout row of this kind was `waiting_idle`,
 *          not already counted as held
 *   pipeline  the rest
 * Each is a measure of time intervals between rows, never a count of rows, so
 * a tick that writes more or fewer rows does not move them. A hold placed and
 * lifted with no rollout attempt in between leaves no row and is invisible.
 */

import type { ArtifactKind, FleetHistory, ReleaseRow, RolloutRow } from './merge-to-seat'

/** The review threshold: an arrival whose pipeline minutes (held and busy excluded) exceed this is marked for review. */
export const REVIEW_THRESHOLD_PIPELINE_MINUTES = 45

const CI_MERGE_NOTE = /^merge ([0-9a-f]{8}) \(CI\)$/
const CI_SKILLS_NOTE = 'published by CI'
const MINUTE_MS = 60_000

export interface Merge {
  sha: string
  kind: ArtifactKind
  releaseId: string
  startMs: number
  clockFrom: 'merge' | 'publish'
  order: [number, number]
}

export interface Split {
  pipelineMinutes: number
  heldMinutes: number
  busyMinutes: number
}

export type WaitingOn =
  | { reason: 'hold'; seat: string }
  | { reason: 'busy'; seat: string }
  | { reason: 'twin'; seat: string }
  | { reason: 'nothing'; seat: string }

export type Arrival =
  | { status: 'arrived'; atMs: number; lastSeat: string; totalMinutes: number; split: Split }
  | { status: 'in_progress'; waitingOn: WaitingOn; elapsedMinutes: number; split: Split }
  | { status: 'no_seats' }

type Interval = [number, number]

/** Whether CI built this release from main (see the module header). */
export function onMainLine(r: ReleaseRow): boolean {
  if (!r.sha) return false
  if (r.kind === 'skills') return r.notes === CI_SKILLS_NOTE
  const m = CI_MERGE_NOTE.exec(r.notes ?? '')
  return m !== null && r.sha.startsWith(m[1])
}

function orderOf(r: ReleaseRow): [number, number] | null {
  if (r.kind === 'image') return r.mergedAtMs === null ? null : [r.mergedAtMs, r.createdAtMs]
  return [r.createdAtMs, r.createdAtMs]
}

function atOrAfter(a: [number, number], b: [number, number]): boolean {
  return a[0] > b[0] || (a[0] === b[0] && a[1] >= b[1])
}

/** Every merge CI recorded, newest first, one per (kind, commit). */
export function mergesOf(releases: ReleaseRow[]): Merge[] {
  const imageMerged = new Map<string, number>()
  for (const r of releases) {
    if (r.kind === 'image' && r.sha && r.mergedAtMs !== null && onMainLine(r)) {
      imageMerged.set(r.sha, r.mergedAtMs)
    }
  }
  const seen = new Set<string>()
  const merges: Merge[] = []
  for (const r of releases) {
    const order = orderOf(r)
    if (!r.sha || !order || !onMainLine(r) || seen.has(`${r.kind}:${r.sha}`)) continue
    seen.add(`${r.kind}:${r.sha}`)
    const merged = imageMerged.get(r.sha)
    merges.push({
      sha: r.sha,
      kind: r.kind,
      releaseId: r.releaseId,
      startMs: merged ?? r.createdAtMs,
      clockFrom: merged === undefined ? 'publish' : 'merge',
      order,
    })
  }
  return merges.sort((a, b) => b.startMs - a.startMs)
}

/** The CI releases of the merge's kind that contain it. */
export function carriersOf(merge: Merge, releases: ReleaseRow[]): Set<string> {
  const out = new Set<string>()
  for (const r of releases) {
    const order = orderOf(r)
    if (r.kind === merge.kind && order && onMainLine(r) && atOrAfter(order, merge.order)) {
      out.add(r.releaseId)
    }
  }
  return out
}

/** Each seat's rollout rows of one kind, oldest attempt first. */
export function rowsBySeat(history: FleetHistory, kind: ArtifactKind): Map<string, RolloutRow[]> {
  const kindOf = new Map(history.releases.map((r) => [r.releaseId, r.kind]))
  const bySeat = new Map<string, RolloutRow[]>()
  for (const row of history.rollouts) {
    if (kindOf.get(row.releaseId) !== kind) continue
    bySeat.set(row.seat, [...(bySeat.get(row.seat) ?? []), row])
  }
  for (const rows of bySeat.values()) rows.sort((a, b) => a.startedAtMs - b.startedAtMs)
  return bySeat
}

function firstDone(rows: RolloutRow[], carriers: Set<string>): number | null {
  let best: number | null = null
  for (const row of rows) {
    if (row.state !== 'done' || row.finishedAtMs === null || !carriers.has(row.releaseId)) continue
    if (best === null || row.finishedAtMs < best) best = row.finishedAtMs
  }
  return best
}

/** The intervals during which the seat's latest row was in `state`, each running to the next row's start. */
function latestRowIntervals(rows: RolloutRow[], state: string): Interval[] {
  const out: Interval[] = []
  rows.forEach((row, i) => {
    if (row.state === state) out.push([row.startedAtMs, rows[i + 1]?.startedAtMs ?? Infinity])
  })
  return out
}

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

interface SeatContext {
  bySeat: Map<string, RolloutRow[]>
  holds: Map<string, number>
  twins: Record<string, string>
}

/** Held, busy and pipeline minutes over [from, to] for these seats (a seat and its twin). */
export function splitFor(seats: string[], from: number, to: number, ctx: SeatContext): Split {
  const held: Interval[] = []
  const busy: Interval[] = []
  for (const seat of seats) {
    const rows = ctx.bySeat.get(seat) ?? []
    held.push(...latestRowIntervals(rows, 'skipped_held'))
    busy.push(...latestRowIntervals(rows, 'waiting_idle'))
    const heldAt = ctx.holds.get(seat)
    if (heldAt !== undefined) held.push([heldAt, Infinity])
  }
  const heldU = clipUnion(held, from, to)
  const busyU = clipUnion(busy, from, to)
  const heldMs = measure(heldU)
  const busyMs = measure(busyU) - overlap(busyU, heldU)
  const total = Math.max(0, to - from)
  const heldMinutes = Math.round(heldMs / MINUTE_MS)
  const busyMinutes = Math.round(busyMs / MINUTE_MS)
  return {
    heldMinutes,
    busyMinutes,
    pipelineMinutes: Math.max(0, Math.round(total / MINUTE_MS) - heldMinutes - busyMinutes),
  }
}

function track(seat: string, twins: Record<string, string>): string[] {
  return twins[seat] ? [seat, twins[seat]] : [seat]
}

/** What a seat not yet done waits on: a hold, a busy seat, its twin, or nothing. */
function waitingFor(seat: string, carriers: Set<string>, ctx: SeatContext): WaitingOn {
  const seats = track(seat, ctx.twins)
  const held = seats.find((s) => ctx.holds.has(s))
  if (held) return { reason: 'hold', seat: held }
  const busy = seats.find((s) => (ctx.bySeat.get(s) ?? []).at(-1)?.state === 'waiting_idle')
  if (busy) return { reason: 'busy', seat: busy }
  const twin = ctx.twins[seat]
  if (twin && firstDone(ctx.bySeat.get(twin) ?? [], carriers) === null) {
    return { reason: 'twin', seat: twin }
  }
  return { reason: 'nothing', seat }
}

const WAIT_RANK: Record<WaitingOn['reason'], number> = { hold: 0, busy: 1, twin: 2, nothing: 3 }

function seatsTaking(kind: ArtifactKind, group: string[], ctx: SeatContext): string[] {
  return kind === 'bundle' ? group.filter((s) => (ctx.bySeat.get(s) ?? []).length > 0) : group
}

/** When the merge reached the last seat of `group`, or what it is waiting on. */
export function arrivalOf(
  merge: Merge,
  group: string[],
  carriers: Set<string>,
  ctx: SeatContext,
  nowMs: number
): Arrival {
  const seats = seatsTaking(merge.kind, group, ctx)
  if (seats.length === 0) return { status: 'no_seats' }
  const done = seats.map((s) => ({ seat: s, at: firstDone(ctx.bySeat.get(s) ?? [], carriers) }))
  const pending = done.filter((d) => d.at === null).map((d) => d.seat)
  if (pending.length > 0) {
    const waits = pending.map((s) => waitingFor(s, carriers, ctx))
    waits.sort((a, b) => WAIT_RANK[a.reason] - WAIT_RANK[b.reason] || a.seat.localeCompare(b.seat))
    const tracked = pending.flatMap((s) => track(s, ctx.twins))
    return {
      status: 'in_progress',
      waitingOn: waits[0],
      elapsedMinutes: Math.max(0, Math.round((nowMs - merge.startMs) / MINUTE_MS)),
      split: splitFor(tracked, merge.startMs, nowMs, ctx),
    }
  }
  const last = done.reduce((a, b) => ((b.at ?? 0) > (a.at ?? 0) ? b : a))
  const atMs = last.at ?? merge.startMs
  return {
    status: 'arrived',
    atMs,
    lastSeat: last.seat,
    totalMinutes: Math.max(0, Math.round((atMs - merge.startMs) / MINUTE_MS)),
    split: splitFor(track(last.seat, ctx.twins), merge.startMs, atMs, ctx),
  }
}

/** The per-kind seat context: rows by seat, holds in place now, twins. */
export function seatContext(
  history: FleetHistory,
  kind: ArtifactKind,
  twins: Record<string, string>
): SeatContext {
  return {
    bySeat: rowsBySeat(history, kind),
    holds: new Map(history.holds.map((h) => [h.seat, h.heldAtMs])),
    twins,
  }
}

/** Whether an arrival's pipeline minutes exceed the review threshold. */
export function overThreshold(arrival: Arrival): boolean {
  return (
    arrival.status === 'arrived' &&
    arrival.split.pipelineMinutes > REVIEW_THRESHOLD_PIPELINE_MINUTES
  )
}
