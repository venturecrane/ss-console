/**
 * Every condition the pager can emit is admitted by fleet_alert_state's CHECK
 * after the whole migration chain has run.
 *
 * Why this test exists (2026-09-11): the outside-in edge poll (#2775, migration
 * 0115) opened its alert under a new condition, 'edge_down', and 0115 never
 * widened the CHECK on fleet_alert_state.condition. The worker sends the email
 * first and records the open row second, so the third failed probe paged, the
 * INSERT was rejected, nothing was recorded, and the next tick would have paged
 * again every two minutes with no recovery notice ever. The worker's suite runs
 * against a fake database; the edge-poll suite applies 0115 alone. Neither
 * touched the constraint. Migration 0116 fixed the vocabulary; this test makes
 * the class fail at merge time instead of at the third tick.
 *
 * The instrument is falsifiable: the last case inserts a name no migration has
 * ever admitted and expects the rejection, so a CHECK that had been dropped or
 * loosened to accept anything would fail here too.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import {
  CONDITION_PREFIXES,
  EDGE_DOWN_CONDITION,
  UNPREFIXED_CONDITIONS,
} from '../workers/fleet-alerts/src/conditions'

const migrationsDir = path.resolve(__dirname, '../migrations')

async function insertCondition(db: D1Database, condition: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
       VALUES (?, ?, 'open', datetime('now'), NULL, NULL, datetime('now'))`
    )
    .bind('guard-seat', condition)
    .run()
}

describe('fleet_alert_state admits every condition the pager can write', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  })

  it('has a non-empty condition list to check (an empty list would pass vacuously)', () => {
    expect(UNPREFIXED_CONDITIONS.length).toBeGreaterThanOrEqual(14)
    expect(UNPREFIXED_CONDITIONS).toContain(EDGE_DOWN_CONDITION)
    expect(CONDITION_PREFIXES.length).toBeGreaterThanOrEqual(4)
  })

  it('accepts every unprefixed condition, edge_down included', async () => {
    const rejected: string[] = []
    for (const condition of UNPREFIXED_CONDITIONS) {
      try {
        await insertCondition(db, condition)
      } catch (err) {
        rejected.push(`${condition}: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    expect(
      rejected,
      'a condition the worker can emit is rejected by the CHECK; add a widening migration (template: 0116)'
    ).toEqual([])
    const { results } = await db
      .prepare(
        `SELECT condition FROM fleet_alert_state WHERE customer_slug = 'guard-seat' ORDER BY condition`
      )
      .all<{ condition: string }>()
    expect(results.map((r) => r.condition).sort()).toEqual([...UNPREFIXED_CONDITIONS].sort())
  })

  it('accepts every prefixed class with a payload', async () => {
    for (const prefix of CONDITION_PREFIXES) {
      await insertCondition(db, `${prefix}payload`)
    }
    const row = await db
      .prepare(`SELECT COUNT(*) AS n FROM fleet_alert_state WHERE customer_slug = 'guard-seat'`)
      .first<{ n: number }>()
    expect(row?.n).toBe(CONDITION_PREFIXES.length)
  })

  it('rejects a name no migration admits (the constraint is real)', async () => {
    await expect(insertCondition(db, 'condition_nobody_migrated')).rejects.toThrow(/CHECK/i)
  })
})
