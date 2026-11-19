/**
 * Whether the database's hold history can be trusted, and since when.
 *
 * Migration 0153 records every seat hold placed and lifted in
 * `fleet_hold_events`, by two triggers on `fleet_holds`. Hold minutes on the
 * merge-to-seat card are a fact only for windows that start after it was
 * applied, and only while the history is whole. It is read as NOT recorded,
 * with the reason in plain words, when:
 *   - the migration is not in `d1_migrations` (wrangler's ledger: `name` is the
 *     migration file name, `applied_at` its CURRENT_TIMESTAMP, `YYYY-MM-DD
 *     HH:MM:SS` UTC);
 *   - either trigger is missing from `sqlite_master` (the documented back-out
 *     is DROP TRIGGER by hand);
 *   - a seat has an open hold event but no hold in place, or a hold in place
 *     with no open event (the two tables disagree);
 *   - a history row cannot be read (no seat, an unreadable `held_at`, or a
 *     `lifted_at` that is set but unreadable): it is never dropped or read as
 *     "still in place".
 * Static SQL only; any query error is itself a reason, never a pass.
 */

import { checkHoldEvent, parseUtc } from './merge-to-seat'

export const HOLD_HISTORY_MIGRATION = '0153_fleet_hold_events.sql'
export const HOLD_TRIGGERS = ['fleet_holds_record_placed', 'fleet_holds_record_lifted'] as const

export interface HoldHistory {
  /** When hold history began (ISO UTC), or null when it is not recorded. */
  since: string | null
  /** Why it is not recorded, in plain words; null when it is. */
  why: string | null
}

const MIGRATION_SQL = 'SELECT applied_at FROM d1_migrations WHERE name = ?'
const TRIGGERS_SQL =
  "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'fleet_holds'"
const EVENTS_SQL = 'SELECT customer_slug, held_at, lifted_at FROM fleet_hold_events LIMIT ?'
/** The history is one row per hold ever placed: small. More than this is itself a reason. */
const EVENTS_CAP = 5000
const AGREEMENT_SQL =
  'SELECT ' +
  '(SELECT COUNT(*) FROM fleet_hold_events e WHERE e.lifted_at IS NULL AND NOT EXISTS ' +
  '(SELECT 1 FROM fleet_holds h WHERE h.customer_slug = e.customer_slug)) AS open_without_hold, ' +
  '(SELECT COUNT(*) FROM fleet_holds h WHERE NOT EXISTS (SELECT 1 FROM fleet_hold_events e ' +
  'WHERE e.customer_slug = h.customer_slug AND e.lifted_at IS NULL)) AS hold_without_open'

function notRecorded(why: string): HoldHistory {
  return { since: null, why }
}

/** A result row as a record, or undefined: narrowed by a check, never assumed. */
function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : undefined
}

function count(row: Record<string, unknown> | undefined, key: string): number | null {
  const v = row?.[key]
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null
}

function names(rows: unknown[]): Set<string> {
  const out = new Set<string>()
  for (const r of rows) {
    if (typeof r === 'object' && r !== null && 'name' in r && typeof r.name === 'string') {
      out.add(r.name)
    }
  }
  return out
}

async function appliedAt(db: D1Database): Promise<number | string> {
  const rows = (await db.prepare(MIGRATION_SQL).bind(HOLD_HISTORY_MIGRATION).all()).results ?? []
  if (rows.length === 0) return `migration ${HOLD_HISTORY_MIGRATION} is not applied`
  const ms = parseUtc(asRecord(rows[0])?.['applied_at'])
  return ms ?? `migration ${HOLD_HISTORY_MIGRATION} has an unreadable applied time`
}

async function disagreement(db: D1Database): Promise<string | null> {
  const row = asRecord((await db.prepare(AGREEMENT_SQL).all()).results?.[0])
  const openWithoutHold = count(row, 'open_without_hold')
  const holdWithoutOpen = count(row, 'hold_without_open')
  if (openWithoutHold === null || holdWithoutOpen === null) {
    return 'the hold history could not be checked against the holds in place'
  }
  if (openWithoutHold > 0) {
    return `${openWithoutHold} seat(s) have an open hold in the history but no hold in place`
  }
  if (holdWithoutOpen > 0) {
    return `${holdWithoutOpen} hold(s) in place have no open hold in the history`
  }
  return null
}

async function unreadableRow(db: D1Database): Promise<string | null> {
  const rows =
    (
      await db
        .prepare(EVENTS_SQL)
        .bind(EVENTS_CAP + 1)
        .all()
    ).results ?? []
  if (rows.length > EVENTS_CAP) {
    return `the hold history has more than ${EVENTS_CAP} rows, too many to check`
  }
  for (const raw of rows) {
    const checked = checkHoldEvent(raw)
    if (typeof checked === 'string') return checked
  }
  return null
}

/** Since when hold history is recorded, or why it is not. */
export async function readHoldHistory(db: D1Database): Promise<HoldHistory> {
  try {
    const applied = await appliedAt(db)
    if (typeof applied === 'string') return notRecorded(applied)
    const present = names((await db.prepare(TRIGGERS_SQL).all()).results ?? [])
    const missing = HOLD_TRIGGERS.filter((t) => !present.has(t))
    if (missing.length > 0) {
      return notRecorded(
        `the trigger ${missing.join(' and ')} is missing, so holds are no longer recorded`
      )
    }
    const problem = (await disagreement(db)) ?? (await unreadableRow(db))
    if (problem) return notRecorded(problem)
    return { since: new Date(applied).toISOString().replace(/\.\d{3}Z$/, 'Z'), why: null }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return notRecorded(`the hold history could not be read (${msg})`)
  }
}
