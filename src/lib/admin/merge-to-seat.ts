/**
 * Merge to seat: how many minutes a merged change takes to reach every paying
 * client's seat, per image release and as a weekly median (the admin Fleet
 * page's "Merge to seat" card).
 *
 * The metric is "merge to the LAST client seat done", never one named client:
 * with 10 to 20 client seats, the slowest one is the one a client feels. The
 * clock starts at `fleet_releases.merged_at` (the merge commit's committer
 * time, written by `fleet build` since migration 0148) and stops at the latest
 * `finished_at` over the client seats' `done` rollouts of that release. A
 * rollout's `finished_at` is also set for failed, rolled-back and skipped
 * states, so only `state = 'done'` counts.
 *
 * A client seat is a provisioned seat whose authored `seat.yaml` says
 * `purpose: client` (ADR 0107, through the generated SEAT_PURPOSES), never a
 * list kept here and never inferred from ring numbers. An unprovisioned client
 * seat has no Fly app to release to, so it is not waited for.
 *
 * Never invents a number (docs/style/empty-state-pattern.md): a release with no
 * merge time, or with any client seat not yet done, has no all-clients time; a
 * week with no measured release has no median; a failed query is returned as
 * an error for the page to say so.
 */

import { SEAT_PURPOSES, type SeatPurposeEntry } from '../operator/seat-purposes.generated'

/** One image release joined with one done rollout (or none: the LEFT JOIN). */
export interface MergeToSeatRow {
  releaseId: string
  ssConsoleSha: string | null
  createdAt: string
  mergedAt: string | null
  seat: string | null
  finishedAt: string | null
}

export interface ReleaseMergeToSeat {
  releaseId: string
  shortSha: string
  mergedAt: string | null
  imageBuiltAt: string
  /** When the last client seat finished; null while any client seat has no done rollout. */
  allClientsDoneAt: string | null
  minutesMergeToClients: number | null
  minutesImageToClients: number | null
}

export interface WeeklyMedian {
  /** Monday of the ISO week (UTC), YYYY-MM-DD. */
  weekStart: string
  medianMinutes: number
  releases: number
}

export interface MergeToSeatSummary {
  releases: ReleaseMergeToSeat[]
  weeks: WeeklyMedian[]
}

const ISO_Z = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/
const WEEK_MS = 7 * 24 * 60 * 60 * 1000
const WEEKS_SHOWN = 8

/** The client seats: every provisioned seat whose purpose is client, sorted. */
export function clientSeats(
  purposes: Readonly<Record<string, SeatPurposeEntry>> = SEAT_PURPOSES
): string[] {
  return Object.keys(purposes)
    .filter((seat) => purposes[seat].purpose === 'client' && purposes[seat].provisioned)
    .sort()
}

/** A D1 UTC timestamp in the `...Z` form, or null. */
function utc(value: unknown): string | null {
  return typeof value === 'string' && ISO_Z.test(value) && !Number.isNaN(Date.parse(value))
    ? value
    : null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

/** One joined row, or null when it cannot be placed (no release id or build time). */
export function parseRow(raw: unknown): MergeToSeatRow | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  const releaseId = text(r['release_id'])
  const createdAt = utc(r['created_at'])
  if (!releaseId || !createdAt) return null
  return {
    releaseId,
    ssConsoleSha: text(r['ss_console_sha']),
    createdAt,
    mergedAt: utc(r['merged_at']),
    seat: text(r['customer_slug']),
    finishedAt: utc(r['finished_at']),
  }
}

/**
 * The newest `limit` image releases, each joined with its done rollouts. The
 * seat filter is applied in `summarizeMergeToSeat`, so the SQL is static.
 */
export async function fetchMergeToSeatRows(
  db: D1Database,
  limit: number
): Promise<{ rows: MergeToSeatRow[]; error: string | null }> {
  try {
    const result = await db
      .prepare(
        'SELECT r.release_id, r.ss_console_sha, r.created_at, r.merged_at, ' +
          'o.customer_slug, o.finished_at ' +
          "FROM (SELECT * FROM fleet_releases WHERE kind = 'image' " +
          'ORDER BY created_at DESC LIMIT ?) r ' +
          "LEFT JOIN fleet_rollouts o ON o.release_id = r.release_id AND o.state = 'done' " +
          'ORDER BY r.created_at DESC'
      )
      .bind(limit)
      .all<Record<string, unknown>>()
    const rows: MergeToSeatRow[] = []
    for (const raw of result.results ?? []) {
      const row = parseRow(raw)
      if (row) rows.push(row)
    }
    return { rows, error: null }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { rows: [], error: `fleet_releases query failed: ${msg}` }
  }
}

function minutesBetween(from: string | null, to: string | null): number | null {
  if (!from || !to) return null
  return Math.round((Date.parse(to) - Date.parse(from)) / 60000)
}

/** Monday 00:00 UTC of the ISO week holding `ms`. */
function weekStartMs(ms: number): number {
  const d = new Date(ms)
  const sinceMonday = (d.getUTCDay() + 6) % 7
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - sinceMonday)
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2)
}

/** Latest done time per client seat for one release's rows. */
function lastDonePerSeat(rows: MergeToSeatRow[], seats: Set<string>): Map<string, string> {
  const done = new Map<string, string>()
  for (const row of rows) {
    if (!row.seat || !row.finishedAt || !seats.has(row.seat)) continue
    const prior = done.get(row.seat)
    if (!prior || row.finishedAt > prior) done.set(row.seat, row.finishedAt)
  }
  return done
}

function summarizeRelease(rows: MergeToSeatRow[], seats: Set<string>): ReleaseMergeToSeat {
  const first = rows[0]
  const done = lastDonePerSeat(rows, seats)
  const allDone = seats.size > 0 && done.size === seats.size
  const allClientsDoneAt = allDone ? ([...done.values()].sort().at(-1) ?? null) : null
  return {
    releaseId: first.releaseId,
    shortSha: (first.ssConsoleSha ?? first.releaseId.split('-').at(-1) ?? '').slice(0, 8),
    mergedAt: first.mergedAt,
    imageBuiltAt: first.createdAt,
    allClientsDoneAt,
    minutesMergeToClients: minutesBetween(first.mergedAt, allClientsDoneAt),
    minutesImageToClients: minutesBetween(first.createdAt, allClientsDoneAt),
  }
}

function weeklyMedians(releases: ReleaseMergeToSeat[], now: Date): WeeklyMedian[] {
  const oldest = weekStartMs(now.getTime()) - (WEEKS_SHOWN - 1) * WEEK_MS
  const byWeek = new Map<number, number[]>()
  for (const r of releases) {
    if (r.minutesMergeToClients === null || !r.allClientsDoneAt) continue
    const week = weekStartMs(Date.parse(r.allClientsDoneAt))
    if (week < oldest) continue
    byWeek.set(week, [...(byWeek.get(week) ?? []), r.minutesMergeToClients])
  }
  return [...byWeek.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([week, values]) => ({
      weekStart: new Date(week).toISOString().slice(0, 10),
      medianMinutes: median(values),
      releases: values.length,
    }))
}

/**
 * Per release (newest first) and per ISO week of the last client done time
 * (newest first, the last 8 weeks, only weeks with a measured release).
 */
export function summarizeMergeToSeat(
  rows: MergeToSeatRow[],
  seats: string[],
  now: Date = new Date()
): MergeToSeatSummary {
  const seatSet = new Set(seats)
  const byRelease = new Map<string, MergeToSeatRow[]>()
  for (const row of rows) {
    byRelease.set(row.releaseId, [...(byRelease.get(row.releaseId) ?? []), row])
  }
  const releases = [...byRelease.values()]
    .map((group) => summarizeRelease(group, seatSet))
    .sort((a, b) =>
      a.imageBuiltAt < b.imageBuiltAt ? 1 : a.imageBuiltAt > b.imageBuiltAt ? -1 : 0
    )
  return { releases, weeks: weeklyMedians(releases, now) }
}

const PHOENIX_TIME = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Phoenix',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

const WEEK_DAY = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  month: 'short',
  day: 'numeric',
})

/** A UTC timestamp as Phoenix (MST) wall time, e.g. "Nov 4, 9:14 PM". */
export function phoenixTime(value: string): string {
  return PHOENIX_TIME.format(new Date(value))
}

/** A week's Monday as "Nov 2". */
export function weekLabel(weekStart: string): string {
  return WEEK_DAY.format(new Date(`${weekStart}T00:00:00Z`))
}
