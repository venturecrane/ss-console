/**
 * Cross-org behavior tests for the time-entries DAL.
 *
 * Defends against the #399 2026-04-17 audit finding #11: every time-entry DAL
 * primitive (get, update, delete, recalculateActualHours) must refuse to read
 * or mutate rows outside the caller's org. Even though the API route at
 * src/pages/api/admin/time-entries/[id].ts verifies the parent engagement's
 * org_id first, the DAL primitive itself must be safe for any future caller.
 *
 * The DAL is in src/lib/db/time-entries.ts. Each test seeds time entries in
 * two orgs, then attempts a cross-org mutation via the DAL directly, proving
 * the raw-ID mutation path is denied at the SQL predicate.
 *
 * Sibling to tests/time-entries.test.ts, which asserts the SQL strings and
 * signatures. This file proves end-to-end that the predicate actually fires.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import {
  getTimeEntry,
  updateTimeEntry,
  deleteTimeEntry,
  recalculateActualHours,
  listTimeEntries,
  createTimeEntry,
} from '../../src/lib/db/time-entries'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENGAGEMENT_A = 'engagement-a'
const ENGAGEMENT_B = 'engagement-b'
const ENTRY_A = 'entry-a'
const ENTRY_B = 'entry-b'

describe('time-entries DAL — cross-org behavior (#399)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Seed two organizations.
    for (const [id, name, slug] of [
      [ORG_A, 'Org A', 'org-a'],
      [ORG_B, 'Org B', 'org-b'],
    ]) {
      await db
        .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
        .bind(id, name, slug)
        .run()
    }

    // Seed entities (FK target for engagements).
    for (const [orgId, suffix] of [
      [ORG_A, 'a'],
      [ORG_B, 'b'],
    ]) {
      await db
        .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
        .bind(`entity-${suffix}`, orgId, `Entity ${suffix.toUpperCase()}`, `entity-${suffix}`)
        .run()
    }

    // Seed assessments + quotes + engagements per org.
    for (const [engagementId, orgId, suffix] of [
      [ENGAGEMENT_A, ORG_A, 'a'],
      [ENGAGEMENT_B, ORG_B, 'b'],
    ]) {
      await db
        .prepare('INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, ?)')
        .bind(`assessment-${suffix}`, orgId, `entity-${suffix}`, 'completed')
        .run()

      await db
        .prepare(
          `INSERT INTO quotes (id, org_id, entity_id, assessment_id, line_items, total_hours, rate, total_price, status)
           VALUES (?, ?, ?, ?, '[]', 10, 175, 1750, 'accepted')`
        )
        .bind(`quote-${suffix}`, orgId, `entity-${suffix}`, `assessment-${suffix}`)
        .run()

      await db
        .prepare(
          `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, actual_hours)
           VALUES (?, ?, ?, ?, 'active', 0)`
        )
        .bind(engagementId, orgId, `entity-${suffix}`, `quote-${suffix}`)
        .run()
    }

    // Seed one time entry in each org.
    for (const [id, orgId, engagementId, hours] of [
      [ENTRY_A, ORG_A, ENGAGEMENT_A, 4],
      [ENTRY_B, ORG_B, ENGAGEMENT_B, 7],
    ]) {
      await db
        .prepare(
          `INSERT INTO time_entries (id, org_id, engagement_id, date, hours, description, category)
           VALUES (?, ?, ?, '2026-04-17', ?, 'seed', 'implementation')`
        )
        .bind(id, orgId, engagementId, hours)
        .run()
    }
  })

  // ============================================================
  // getTimeEntry
  // ============================================================

  it('getTimeEntry returns null when called from org A for an org B entry', async () => {
    const entry = await getTimeEntry(db, ORG_A, ENTRY_B)
    expect(entry).toBeNull()
  })

  it('getTimeEntry returns the row when called from the owning org', async () => {
    const entry = await getTimeEntry(db, ORG_A, ENTRY_A)
    expect(entry?.id).toBe(ENTRY_A)
    expect(entry?.hours).toBe(4)
  })

  // ============================================================
  // updateTimeEntry
  // ============================================================

  it('updateTimeEntry returns null and does NOT mutate when called from org A for an org B entry', async () => {
    const result = await updateTimeEntry(db, ORG_A, ENTRY_B, { hours: 999 })
    expect(result).toBeNull()

    const row = await db
      .prepare('SELECT hours FROM time_entries WHERE id = ?')
      .bind(ENTRY_B)
      .first<{ hours: number }>()
    expect(row?.hours).toBe(7)
  })

  it('updateTimeEntry applies the change when called from the owning org', async () => {
    const result = await updateTimeEntry(db, ORG_A, ENTRY_A, { hours: 12 })
    expect(result?.hours).toBe(12)
  })

  // ============================================================
  // deleteTimeEntry
  // ============================================================

  it('deleteTimeEntry returns false and does NOT delete when called from org A for an org B entry', async () => {
    const result = await deleteTimeEntry(db, ORG_A, ENTRY_B)
    expect(result).toBe(false)

    const row = await db
      .prepare('SELECT id FROM time_entries WHERE id = ?')
      .bind(ENTRY_B)
      .first<{ id: string }>()
    expect(row).not.toBeNull()
  })

  it('deleteTimeEntry removes the row when called from the owning org', async () => {
    const result = await deleteTimeEntry(db, ORG_A, ENTRY_A)
    expect(result).toBe(true)

    const row = await db
      .prepare('SELECT id FROM time_entries WHERE id = ?')
      .bind(ENTRY_A)
      .first<{ id: string }>()
    expect(row).toBeNull()
  })

  // ============================================================
  // recalculateActualHours
  // ============================================================

  it('recalculateActualHours does NOT touch engagements outside the caller org', async () => {
    // Call from org A targeting org B's engagement. Because org_id is bound
    // into both the SUM query and the UPDATE, nothing should change for
    // either engagement.
    await recalculateActualHours(db, ORG_A, ENGAGEMENT_B)

    const rowB = await db
      .prepare('SELECT actual_hours FROM engagements WHERE id = ?')
      .bind(ENGAGEMENT_B)
      .first<{ actual_hours: number }>()
    expect(rowB?.actual_hours).toBe(0)
  })

  it('recalculateActualHours syncs engagement actual_hours for the owning org', async () => {
    await recalculateActualHours(db, ORG_A, ENGAGEMENT_A)

    const row = await db
      .prepare('SELECT actual_hours FROM engagements WHERE id = ?')
      .bind(ENGAGEMENT_A)
      .first<{ actual_hours: number }>()
    expect(row?.actual_hours).toBe(4)
  })

  // ============================================================
  // listTimeEntries
  // ============================================================

  it('listTimeEntries called from org A for an org B engagement returns empty', async () => {
    const rows = await listTimeEntries(db, ORG_A, ENGAGEMENT_B)
    expect(rows).toHaveLength(0)
  })

  it('listTimeEntries returns the entries for the owning org', async () => {
    const rows = await listTimeEntries(db, ORG_A, ENGAGEMENT_A)
    expect(rows).toHaveLength(1)
    expect(rows[0].id).toBe(ENTRY_A)
  })

  // ============================================================
  // createTimeEntry — recalc is org-scoped, so actual_hours only moves for
  // the owning engagement even though engagementId input is trusted.
  // ============================================================

  it('createTimeEntry only syncs actual_hours for the owning engagement', async () => {
    await createTimeEntry(db, ORG_A, ENGAGEMENT_A, {
      date: '2026-04-17',
      hours: 3,
      description: 'second entry',
    })

    const rowA = await db
      .prepare('SELECT actual_hours FROM engagements WHERE id = ?')
      .bind(ENGAGEMENT_A)
      .first<{ actual_hours: number }>()
    expect(rowA?.actual_hours).toBe(7)

    const rowB = await db
      .prepare('SELECT actual_hours FROM engagements WHERE id = ?')
      .bind(ENGAGEMENT_B)
      .first<{ actual_hours: number }>()
    expect(rowB?.actual_hours).toBe(0)
  })
})
