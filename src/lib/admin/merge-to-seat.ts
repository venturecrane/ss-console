/**
 * Merge to seat, the data side: the rows the admin Fleet page's "Merge to seat"
 * card measures, read from D1 and parsed, and the seat groups it measures
 * against. The measuring itself is pure and lives in `lead-time.ts`; the page
 * summary in `lead-time-summary.ts`.
 *
 * Three reads, all static SQL:
 *   fleet_releases  every image, bundle and skills release, newest first
 *   fleet_rollouts  every rollout attempt of those releases (one row per
 *                   attempt: `start_rollout` inserts it, `update_rollout` sets
 *                   its end state and `finished_at`)
 *   fleet_holds     the holds in place NOW (`fleet unhold` deletes the row, so
 *                   a lifted hold is visible only through the `skipped_held`
 *                   rollout rows it caused)
 *
 * Timestamps: every fleet writer stores `strftime('%Y-%m-%dT%H:%M:%SZ','now')`
 * or `ids.iso` (the same form). `parseUtc` also takes the SQLite
 * `YYYY-MM-DD HH:MM:SS` form (UTC by SQLite's definition), fractional seconds
 * and an explicit `+00:00`, because `ids.parse_iso` on the writer side accepts
 * them too. Any other value is read as missing, never guessed.
 *
 * A client seat is a provisioned seat whose authored `seat.yaml` says
 * `purpose: client` (ADR 0107, through the generated SEAT_PURPOSES). An
 * unprovisioned client seat has no Fly app to release to, so it is not waited
 * for. A client seat with a release twin is in the headline group; one without
 * a twin is its own group, never mixed into the headline.
 */

import { SEAT_PURPOSES, type SeatPurposeEntry } from '../operator/seat-purposes.generated'

export type ArtifactKind = 'image' | 'bundle' | 'skills'
export const ARTIFACT_KINDS: readonly ArtifactKind[] = ['image', 'bundle', 'skills']

export interface ReleaseRow {
  releaseId: string
  kind: ArtifactKind
  sha: string | null
  notes: string | null
  createdAtMs: number
  mergedAtMs: number | null
}

export interface RolloutRow {
  releaseId: string
  seat: string
  state: string
  startedAtMs: number
  finishedAtMs: number | null
}

export interface HoldRow {
  seat: string
  heldAtMs: number
}

export interface FleetHistory {
  releases: ReleaseRow[]
  rollouts: RolloutRow[]
  holds: HoldRow[]
}

export interface SeatGroups {
  /** Client seats with a release twin: the headline. */
  twinned: string[]
  /** Client seats with no release twin: their own group. */
  untwinned: string[]
  /** Client seat -> its release twin. */
  twins: Record<string, string>
}

const UTC_FORM = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|\+00:00)?$/

/** A UTC timestamp in one of the forms the fleet writers store, as epoch ms; else null. */
export function parseUtc(value: unknown): number | null {
  if (typeof value !== 'string') return null
  const m = UTC_FORM.exec(value.trim())
  if (!m) return null
  // The `T` form must say it is UTC; the SQLite space form is UTC by definition.
  if (value.includes('T') && !m[7]) return null
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number)
  const ms = Date.UTC(y, mo - 1, d, h, mi, s)
  const back = new Date(ms)
  if (back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d || h > 23 || mi > 59 || s > 59) {
    return null
  }
  return ms
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function kind(value: unknown): ArtifactKind | null {
  return value === 'image' || value === 'bundle' || value === 'skills' ? value : null
}

function record(raw: unknown): Record<string, unknown> | null {
  return typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : null
}

/** One release row, or null when it cannot be placed (no id, kind or build time). */
export function parseRelease(raw: unknown): ReleaseRow | null {
  const r = record(raw)
  if (!r) return null
  const releaseId = text(r['release_id'])
  const k = kind(r['kind'])
  const createdAtMs = parseUtc(r['created_at'])
  if (!releaseId || !k || createdAtMs === null) return null
  return {
    releaseId,
    kind: k,
    sha: text(r['ss_console_sha']),
    notes: text(r['notes']),
    createdAtMs,
    mergedAtMs: parseUtc(r['merged_at']),
  }
}

/** One rollout row, or null when it cannot be placed (no release, seat, state or start). */
export function parseRollout(raw: unknown): RolloutRow | null {
  const r = record(raw)
  if (!r) return null
  const releaseId = text(r['release_id'])
  const seat = text(r['customer_slug'])
  const state = text(r['state'])
  const startedAtMs = parseUtc(r['started_at'])
  if (!releaseId || !seat || !state || startedAtMs === null) return null
  return { releaseId, seat, state, startedAtMs, finishedAtMs: parseUtc(r['finished_at']) }
}

/** One current hold, or null when it cannot be placed. */
export function parseHold(raw: unknown): HoldRow | null {
  const r = record(raw)
  if (!r) return null
  const seat = text(r['customer_slug'])
  const heldAtMs = parseUtc(r['held_at'])
  return seat && heldAtMs !== null ? { seat, heldAtMs } : null
}

const RELEASES_SQL =
  'SELECT release_id, kind, ss_console_sha, notes, created_at, merged_at FROM fleet_releases ' +
  "WHERE kind IN ('image', 'bundle', 'skills') ORDER BY created_at DESC LIMIT ?"

const ROLLOUTS_SQL =
  'SELECT o.release_id, o.customer_slug, o.state, o.started_at, o.finished_at ' +
  'FROM fleet_rollouts o JOIN (SELECT release_id FROM fleet_releases ' +
  "WHERE kind IN ('image', 'bundle', 'skills') ORDER BY created_at DESC LIMIT ?) r " +
  'ON r.release_id = o.release_id'

const HOLDS_SQL = 'SELECT customer_slug, held_at FROM fleet_holds'

function parseAll<T>(rows: unknown[] | undefined, parse: (raw: unknown) => T | null): T[] {
  const out: T[] = []
  for (const raw of rows ?? []) {
    const row = parse(raw)
    if (row) out.push(row)
  }
  return out
}

async function read<T>(
  name: string,
  run: () => Promise<{ results?: unknown[] }>,
  parse: (raw: unknown) => T | null
): Promise<T[]> {
  try {
    return parseAll((await run()).results, parse)
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    throw new Error(`${name} query failed: ${msg}`, { cause: e })
  }
}

/** The newest `limit` releases of every kind, their rollouts, and the holds in place now. */
export async function fetchFleetHistory(
  db: D1Database,
  limit: number
): Promise<{ history: FleetHistory; error: string | null }> {
  try {
    const [releases, rollouts, holds] = await Promise.all([
      read('fleet_releases', () => db.prepare(RELEASES_SQL).bind(limit).all(), parseRelease),
      read('fleet_rollouts', () => db.prepare(ROLLOUTS_SQL).bind(limit).all(), parseRollout),
      read('fleet_holds', () => db.prepare(HOLDS_SQL).all(), parseHold),
    ])
    return { history: { releases, rollouts, holds }, error: null }
  } catch (e) {
    return {
      history: { releases: [], rollouts: [], holds: [] },
      error: e instanceof Error ? e.message : String(e),
    }
  }
}

/** The client seats: every provisioned seat whose purpose is client, sorted. */
export function clientSeats(
  purposes: Readonly<Record<string, SeatPurposeEntry>> = SEAT_PURPOSES
): string[] {
  return Object.keys(purposes)
    .filter((seat) => purposes[seat].purpose === 'client' && purposes[seat].provisioned)
    .sort()
}

/** The client seats split by whether a release twin gates them (ADR 0107). */
export function seatGroups(
  purposes: Readonly<Record<string, SeatPurposeEntry>> = SEAT_PURPOSES
): SeatGroups {
  const twins: Record<string, string> = {}
  for (const [seat, entry] of Object.entries(purposes)) {
    if (entry.purpose === 'release-twin' && entry.twinOf) twins[entry.twinOf] = seat
  }
  const clients = clientSeats(purposes)
  return {
    twinned: clients.filter((s) => s in twins),
    untwinned: clients.filter((s) => !(s in twins)),
    twins: Object.fromEntries(clients.filter((s) => s in twins).map((s) => [s, twins[s]])),
  }
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

/** Epoch ms as Phoenix (MST) wall time, e.g. "Nov 4, 9:14 PM". */
export function phoenixTime(ms: number): string {
  return PHOENIX_TIME.format(new Date(ms))
}

/** A week's Monday (YYYY-MM-DD) as "Nov 2". */
export function weekLabel(weekStart: string): string {
  return WEEK_DAY.format(new Date(`${weekStart}T00:00:00Z`))
}
