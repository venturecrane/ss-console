/**
 * Migration 0153: the database records every seat hold placed and lifted, and
 * the merge-to-seat reader trusts that history only while it is whole.
 *
 * Applied through the real migration chain on real SQLite, driven by the EXACT
 * statements the fleet sends:
 *   hold     operator/bin/lib/fleet/d1.py FleetStore.hold (line 152): an upsert
 *   unhold   operator/bin/lib/fleet/d1.py FleetStore.unhold (line 159)
 *   decommission  operator/bin/lib/decommission_console.py _clear_table (line 159):
 *            the same DELETE text; fleet_hold_events is retained, never deleted
 * Every value travels as `console_d1.sql_text`'s hex blob literal, as there.
 * The d1_migrations table is wrangler's own DDL (wrangler-dist
 * getCreateMigrationsTableQuery), with the row wrangler writes on apply.
 */

import { describe, it, expect } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { HOLD_HISTORY_MIGRATION, readHoldHistory } from '../src/lib/admin/hold-history'
import { fetchFleetHistory } from '../src/lib/admin/merge-to-seat'
import { readFileSync } from 'node:fs'

const MIGRATIONS = path.resolve(__dirname, '..', 'migrations')
const isNew = (f: string) => /(^|\/)0153_[^/]*\.sql$/.test(f)

/** console_d1.sql_text: a TEXT literal as a hex blob cast back to TEXT. */
const sqlText = (v: string) => `CAST(x'${Buffer.from(v, 'utf8').toString('hex')}' AS TEXT)`

/** FleetStore.hold, as d1.py:152 writes it. */
const holdSql = (seat: string, reason = 'r') =>
  'INSERT INTO fleet_holds (customer_slug, reason, held_by, held_at) VALUES ' +
  `(${sqlText(seat)}, ${sqlText(reason)}, ${sqlText('captain')}, strftime('%Y-%m-%dT%H:%M:%SZ','now')) ` +
  'ON CONFLICT(customer_slug) DO UPDATE SET reason = excluded.reason, held_by = excluded.held_by, held_at = excluded.held_at'

/** FleetStore.unhold (d1.py:159), and decommission's _clear_table DELETE: the same text. */
const deleteSql = (table: string, seat: string) =>
  `DELETE FROM ${table} WHERE customer_slug = ${sqlText(seat)}`

/** wrangler's d1_migrations DDL, verbatim. */
const WRANGLER_MIGRATIONS_DDL = `CREATE TABLE IF NOT EXISTS "d1_migrations"(
		id         INTEGER PRIMARY KEY AUTOINCREMENT,
		name       TEXT UNIQUE,
		applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);`

type Db = ReturnType<typeof createTestD1>

async function schema(opts: { before?: (db: Db) => Promise<void>; ledger?: boolean } = {}) {
  const files = discoverNumericMigrations(MIGRATIONS)
  const at = files.findIndex(isNew)
  expect(at).toBeGreaterThan(0)
  const db = createTestD1()
  await runMigrations(db, { files: files.slice(0, at) })
  await opts.before?.(db)
  await runMigrations(db, { files: files.slice(at, at + 1) })
  if (opts.ledger !== false) {
    await db.exec(WRANGLER_MIGRATIONS_DDL)
    await db
      .prepare('INSERT INTO d1_migrations (name) VALUES (?)')
      .bind(HOLD_HISTORY_MIGRATION)
      .run()
  }
  return db
}

async function events(db: Db) {
  const { results } = await db
    .prepare('SELECT customer_slug, lifted_at IS NULL AS open FROM fleet_hold_events ORDER BY id')
    .all<{ customer_slug: string; open: number }>()
  return results.map((r) => `${r.customer_slug}:${r.open ? 'open' : 'closed'}`)
}

const run = (db: Db, sql: string) => db.prepare(sql).run()

describe('migration 0153: hold history by triggers', () => {
  it('hold then unhold gives one closed interval', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    expect(await events(db)).toEqual(['acme:open'])
    await run(db, deleteSql('fleet_holds', 'acme'))
    expect(await events(db)).toEqual(['acme:closed'])
  })

  it('re-holding a held seat (the upsert conflict path) keeps one open interval', async () => {
    const db = await schema()
    await run(db, holdSql('acme', 'first'))
    await run(db, holdSql('acme', 'second'))
    const held = await db.prepare('SELECT reason FROM fleet_holds').all<{ reason: string }>()
    expect(held.results).toEqual([{ reason: 'second' }])
    expect(await events(db)).toEqual(['acme:open'])
  })

  it('an INSERT OR REPLACE of a held seat (no DELETE trigger fires) is held off by the guard', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    await run(
      db,
      "INSERT OR REPLACE INTO fleet_holds (customer_slug, reason, held_by, held_at) VALUES ('acme', 'r', 'x', '2026-11-18T00:00:00Z')"
    )
    expect(await events(db)).toEqual(['acme:open'])
  })

  it('hold, unhold, hold gives two intervals, the first closed', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    await run(db, deleteSql('fleet_holds', 'acme'))
    await run(db, holdSql('acme'))
    expect(await events(db)).toEqual(['acme:closed', 'acme:open'])
  })

  it('a decommission delete closes the interval and the history is kept (retained, like rollouts)', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    await run(db, holdSql('other'))
    // The only DELETE decommission sends here: fleet_hold_events is in RETAINED_SEAT_TABLES.
    await run(db, deleteSql('fleet_holds', 'acme'))
    expect(await events(db)).toEqual(['acme:closed', 'other:open'])
  })

  it('a hold in place before the migration is backfilled as an open interval with its own time', async () => {
    const db = await schema({
      before: async (d) => {
        await d
          .prepare(
            "INSERT INTO fleet_holds (customer_slug, reason, held_by, held_at) VALUES ('acme', 'r', 'x', '2026-11-01T10:00:00Z')"
          )
          .run()
      },
    })
    const rows = await db
      .prepare('SELECT customer_slug, held_at, lifted_at FROM fleet_hold_events')
      .all()
    expect(rows.results).toEqual([
      { customer_slug: 'acme', held_at: '2026-11-01T10:00:00Z', lifted_at: null },
    ])
  })
})

describe('readHoldHistory on the real schema', () => {
  it('recorded: since is when wrangler applied the migration, and the reader reads the intervals', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    await run(db, deleteSql('fleet_holds', 'acme'))
    const history = await readHoldHistory(db)
    expect(history.why).toBeNull()
    expect(history.since).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/)
    const { history: fleet, error } = await fetchFleetHistory(db, Date.now(), true)
    expect(error).toBeNull()
    expect(fleet.holds).toHaveLength(1)
    expect(fleet.holds[0].liftedAtMs).not.toBeNull()
  })

  it('not recorded when the migration row is absent', async () => {
    const db = await schema({ ledger: false })
    await db.exec(WRANGLER_MIGRATIONS_DDL)
    const history = await readHoldHistory(db)
    expect(history).toEqual({
      since: null,
      why: `migration ${HOLD_HISTORY_MIGRATION} is not applied`,
    })
  })

  it('not recorded when the migrations ledger cannot be read at all', async () => {
    const db = await schema({ ledger: false })
    const history = await readHoldHistory(db)
    expect(history.since).toBeNull()
    expect(history.why).toMatch(/^the hold history could not be read/)
  })

  it('not recorded when a trigger is missing (the documented back-out)', async () => {
    const db = await schema()
    await db.exec('DROP TRIGGER fleet_holds_record_placed')
    const history = await readHoldHistory(db)
    expect(history.since).toBeNull()
    expect(history.why).toBe(
      'the trigger fleet_holds_record_placed is missing, so holds are no longer recorded'
    )
  })

  it('not recorded when an open interval has no hold in place', async () => {
    const db = await schema()
    await run(
      db,
      "INSERT INTO fleet_hold_events (customer_slug, held_at) VALUES ('ghost', '2026-11-18T00:00:00Z')"
    )
    const history = await readHoldHistory(db)
    expect(history.why).toBe('1 seat(s) have an open hold in the history but no hold in place')
  })

  it('not recorded when a hold in place has no open interval', async () => {
    const db = await schema()
    await run(db, holdSql('acme'))
    await run(db, "UPDATE fleet_hold_events SET lifted_at = '2026-11-18T00:00:00Z'")
    const history = await readHoldHistory(db)
    expect(history.why).toBe('1 hold(s) in place have no open hold in the history')
  })

  it('FALSIFIER: a lifted trigger that no longer closes the interval reads as not recorded', async () => {
    const db = await schema()
    // Broken, not dropped: the name is still there, so only the history itself can tell.
    await db.exec('DROP TRIGGER fleet_holds_record_lifted')
    await db.exec(
      'CREATE TRIGGER fleet_holds_record_lifted AFTER DELETE ON fleet_holds BEGIN SELECT 1; END'
    )
    await run(db, holdSql('acme'))
    expect((await readHoldHistory(db)).why).toBeNull()
    await run(db, deleteSql('fleet_holds', 'acme'))
    const history = await readHoldHistory(db)
    expect(history.since).toBeNull()
    expect(history.why).toBe('1 seat(s) have an open hold in the history but no hold in place')
  })
})

describe('a bad history row is never read as fact', () => {
  const insertEvent = (db: Db, held: string, lifted: string | null) =>
    db
      .prepare('INSERT INTO fleet_hold_events (customer_slug, held_at, lifted_at) VALUES (?, ?, ?)')
      .bind('acme', held, lifted)
      .run()

  it('an unreadable held_at: hold time not recorded, and the events read fails with the reason', async () => {
    const db = await schema()
    await insertEvent(db, 'last tuesday', '2026-11-18T00:00:00Z')
    expect(await readHoldHistory(db)).toEqual({
      since: null,
      why: 'a hold history row for acme has an unreadable held_at',
    })
    const { error } = await fetchFleetHistory(db, Date.now(), true)
    expect(error).toBe('fleet_hold_events: a hold history row for acme has an unreadable held_at')
  })

  it('a set but unreadable lifted_at is never read as "still in place"', async () => {
    const db = await schema()
    await insertEvent(db, '2026-11-18T00:00:00Z', 'yesterday')
    expect(await readHoldHistory(db)).toEqual({
      since: null,
      why: 'a hold history row for acme has an unreadable lifted_at',
    })
    const { error, history } = await fetchFleetHistory(db, Date.now(), true)
    expect(error).toBe('fleet_hold_events: a hold history row for acme has an unreadable lifted_at')
    expect(history.holds).toEqual([])
  })
})

describe('the whole migration chain keeps the table and both triggers', () => {
  async function wholeChain() {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(MIGRATIONS) })
    return db
  }

  it('after every migration, fleet_hold_events and both triggers exist and record a hold', async () => {
    const db = await wholeChain()
    const objects = await db
      .prepare(
        "SELECT type, name FROM sqlite_master WHERE name IN ('fleet_hold_events', 'fleet_holds_record_placed', 'fleet_holds_record_lifted') ORDER BY name"
      )
      .all<{ type: string; name: string }>()
    expect(objects.results).toEqual([
      { type: 'table', name: 'fleet_hold_events' },
      { type: 'trigger', name: 'fleet_holds_record_lifted' },
      { type: 'trigger', name: 'fleet_holds_record_placed' },
    ])
    await run(db, holdSql('acme'))
    await run(db, deleteSql('fleet_holds', 'acme'))
    expect(await events(db)).toEqual(['acme:closed'])
  })

  it('the warning holds: a table dropped under its triggers breaks every hold, and the rollback file mends it', async () => {
    const db = await wholeChain()
    await db.exec('DROP TABLE fleet_hold_events')
    await expect((async () => run(db, holdSql('acme')))()).rejects.toThrow(
      /no such table: (main\.)?fleet_hold_events/
    )
    await db.exec(
      readFileSync(path.join(MIGRATIONS, 'rollbacks', '0153_fleet_hold_events_down.sql'), 'utf8')
    )
    await run(db, holdSql('acme'))
    const held = await db.prepare('SELECT customer_slug FROM fleet_holds').all()
    expect(held.results).toEqual([{ customer_slug: 'acme' }])
  })
})
