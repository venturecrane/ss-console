/**
 * Lead time, merge to client seat: for every merged change, when it reached
 * the LAST client seat of a group, and what it waits on until then. Pure:
 * every input is a parsed row from `merge-to-seat.ts`; the minutes split is
 * `lead-time-split.ts`.
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
 * WHICH SEATS COUNT. A client seat counts for a merge only if it has a rollout
 * row of that kind that STARTED at or before the merge's clock start: a seat
 * added mid-window is not waited for by the merges before it, and a seat with
 * no job lane, which never takes a bundle, is not waited for by bundles.
 *
 * ARRIVAL. Per seat, the first `done` rollout of any release containing M; the
 * merge arrived for a group when every counted seat has one, at the latest of
 * those (the last seat, the one a client feels). A release overtaken before a
 * seat took it is simply not a done row there; the newer release that carried
 * M is.
 *
 * THE CLOCK. It starts when the change reached main (`reachedMainAtMs`, not
 * recorded yet), else at `merged_at`, the merge commit's committer time, which
 * is when the merge queue BUILT the commit, minutes before it reached main
 * (labelled "from queued"). A bundle or skills merge takes the same commit's
 * image clock when it has one, else its own `created_at` ("from published").
 */

import { HOLD_HISTORY_RECORDED_SINCE, splitFor, type Split, type Track } from './lead-time-split'
import {
  parseUtc,
  type ArtifactKind,
  type FleetHistory,
  type HoldInterval,
  type ReleaseRow,
  type RolloutRow,
} from './merge-to-seat'

/** The review threshold: an arrival whose recorded pipeline minutes (held and busy excluded) exceed this is marked for review. */
export const REVIEW_THRESHOLD_PIPELINE_MINUTES = 45

const CI_MERGE_NOTE = /^merge ([0-9a-f]{8}) \(CI\)$/
const CI_SKILLS_NOTE = 'published by CI'

export type ClockFrom = 'merged' | 'queued' | 'published'

export interface Merge {
  sha: string
  kind: ArtifactKind
  releaseId: string
  startMs: number
  clockFrom: ClockFrom
  order: [number, number]
}

export type WaitingOn =
  | { reason: 'hold'; seat: string }
  | { reason: 'busy'; seat: string }
  | { reason: 'idle_wait'; seat: string }
  | { reason: 'twin'; seat: string }
  | { reason: 'nothing'; seat: string }

export type Arrival =
  | { status: 'arrived'; atMs: number; lastSeat: string; split: Split }
  | { status: 'in_progress'; waitingOn: WaitingOn; split: Split }
  | { status: 'no_seats' }

export interface SeatContext {
  bySeat: Map<string, RolloutRow[]>
  holds: HoldInterval[]
  twins: Record<string, string>
  holdHistorySinceMs: number | null
}

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

function imageClocks(releases: ReleaseRow[]): Map<string, { ms: number; from: ClockFrom }> {
  const clocks = new Map<string, { ms: number; from: ClockFrom }>()
  for (const r of releases) {
    if (r.kind !== 'image' || !r.sha || r.mergedAtMs === null || !onMainLine(r)) continue
    clocks.set(
      r.sha,
      r.reachedMainAtMs === null
        ? { ms: r.mergedAtMs, from: 'queued' }
        : { ms: r.reachedMainAtMs, from: 'merged' }
    )
  }
  return clocks
}

/** Every merge CI recorded, newest first, one per (kind, commit). */
export function mergesOf(releases: ReleaseRow[]): Merge[] {
  const clocks = imageClocks(releases)
  const seen = new Set<string>()
  const merges: Merge[] = []
  for (const r of releases) {
    const order = orderOf(r)
    if (!r.sha || !order || !onMainLine(r) || seen.has(`${r.kind}:${r.sha}`)) continue
    seen.add(`${r.kind}:${r.sha}`)
    const clock = clocks.get(r.sha) ?? { ms: r.createdAtMs, from: 'published' as const }
    merges.push({
      sha: r.sha,
      kind: r.kind,
      releaseId: r.releaseId,
      startMs: clock.ms,
      clockFrom: clock.from,
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

function rowsBySeat(history: FleetHistory, kind: ArtifactKind): Map<string, RolloutRow[]> {
  const kindOf = new Map(history.releases.map((r) => [r.releaseId, r.kind]))
  const bySeat = new Map<string, RolloutRow[]>()
  for (const row of history.rollouts) {
    if (kindOf.get(row.releaseId) !== kind) continue
    bySeat.set(row.seat, [...(bySeat.get(row.seat) ?? []), row])
  }
  for (const rows of bySeat.values()) rows.sort((a, b) => a.startedAtMs - b.startedAtMs)
  return bySeat
}

/** The per-kind seat context. `holdHistorySince` defaults to HOLD_HISTORY_RECORDED_SINCE. */
export function seatContext(
  history: FleetHistory,
  kind: ArtifactKind,
  twins: Record<string, string>,
  holdHistorySince: string | null = HOLD_HISTORY_RECORDED_SINCE
): SeatContext {
  return {
    bySeat: rowsBySeat(history, kind),
    holds: history.holds,
    twins,
    holdHistorySinceMs: holdHistorySince === null ? null : parseUtc(holdHistorySince),
  }
}

function firstDone(rows: RolloutRow[], carriers: Set<string>): number | null {
  let best: number | null = null
  for (const row of rows) {
    if (row.state !== 'done' || row.finishedAtMs === null || !carriers.has(row.releaseId)) continue
    if (best === null || row.finishedAtMs < best) best = row.finishedAtMs
  }
  return best
}

function heldNow(holds: HoldInterval[], seat: string, nowMs: number): boolean {
  return holds.some(
    (h) => h.seat === seat && h.heldAtMs <= nowMs && (h.liftedAtMs === null || h.liftedAtMs > nowMs)
  )
}

/** A seat and its twin; the twin counts only until its first done on a carrier. */
function tracks(seat: string, carriers: Set<string>, ctx: SeatContext): Track[] {
  const twin = ctx.twins[seat]
  const out: Track[] = [{ seat, untilMs: Infinity }]
  if (twin) {
    out.push({ seat: twin, untilMs: firstDone(ctx.bySeat.get(twin) ?? [], carriers) ?? Infinity })
  }
  return out
}

/** What a seat not yet done waits on: a hold, a busy seat, an idle wait, its twin, or nothing. */
function waitingFor(
  seat: string,
  carriers: Set<string>,
  ctx: SeatContext,
  nowMs: number
): WaitingOn {
  const live = tracks(seat, carriers, ctx).filter((t) => t.untilMs > nowMs)
  const held = live.find((t) => heldNow(ctx.holds, t.seat, nowMs))
  if (held) return { reason: 'hold', seat: held.seat }
  for (const t of live) {
    const last = (ctx.bySeat.get(t.seat) ?? []).at(-1)
    if (last?.state !== 'waiting_idle' || last.busySeconds === 0) continue
    return { reason: last.busySeconds === null ? 'idle_wait' : 'busy', seat: t.seat }
  }
  const twin = ctx.twins[seat]
  if (twin && firstDone(ctx.bySeat.get(twin) ?? [], carriers) === null) {
    return { reason: 'twin', seat: twin }
  }
  return { reason: 'nothing', seat }
}

const WAIT_RANK: Record<WaitingOn['reason'], number> = {
  hold: 0,
  busy: 1,
  idle_wait: 2,
  twin: 3,
  nothing: 4,
}

function seatsCounted(merge: Merge, group: string[], ctx: SeatContext): string[] {
  return group.filter((s) => (ctx.bySeat.get(s) ?? []).some((r) => r.startedAtMs <= merge.startMs))
}

function splitOver(
  seats: string[],
  merge: Merge,
  toMs: number,
  carriers: Set<string>,
  ctx: SeatContext
): Split {
  return splitFor({
    tracks: seats.flatMap((s) => tracks(s, carriers, ctx)),
    fromMs: merge.startMs,
    toMs,
    bySeat: ctx.bySeat,
    holds: ctx.holds,
    holdHistorySinceMs: ctx.holdHistorySinceMs,
  })
}

/** When the merge reached the last seat of `group`, or what it is waiting on. */
export function arrivalOf(
  merge: Merge,
  group: string[],
  carriers: Set<string>,
  ctx: SeatContext,
  nowMs: number
): Arrival {
  const seats = seatsCounted(merge, group, ctx)
  if (seats.length === 0) return { status: 'no_seats' }
  const done = seats.map((s) => ({ seat: s, at: firstDone(ctx.bySeat.get(s) ?? [], carriers) }))
  const pending = done.filter((d) => d.at === null).map((d) => d.seat)
  if (pending.length > 0) {
    const waits = pending.map((s) => waitingFor(s, carriers, ctx, nowMs))
    waits.sort((a, b) => WAIT_RANK[a.reason] - WAIT_RANK[b.reason] || a.seat.localeCompare(b.seat))
    return {
      status: 'in_progress',
      waitingOn: waits[0],
      split: splitOver(pending, merge, Math.max(merge.startMs, nowMs), carriers, ctx),
    }
  }
  const last = done.reduce((a, b) => ((b.at ?? 0) > (a.at ?? 0) ? b : a))
  const atMs = Math.max(merge.startMs, last.at ?? merge.startMs)
  return {
    status: 'arrived',
    atMs,
    lastSeat: last.seat,
    split: splitOver([last.seat], merge, atMs, carriers, ctx),
  }
}

/** Whether an arrival's RECORDED pipeline minutes exceed the review threshold; an unrecorded split is never marked. */
export function overThreshold(arrival: Arrival): boolean {
  return (
    arrival.status === 'arrived' &&
    arrival.split.recorded &&
    arrival.split.pipelineMinutes > REVIEW_THRESHOLD_PIPELINE_MINUTES
  )
}
