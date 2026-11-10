import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { acquireHold, releaseHold, toSqliteDatetime } from '../../src/lib/booking/holds'
import { migratedDb, seedOrg } from '../_stubs/behavioural'

/**
 * Holds DAL, run against a migrated D1: the upsert-when-expired claim, the
 * live-hold refusal, expired-row reclaim, idempotent release, and the
 * SQLite datetime format the expiry comparison depends on.
 */

const ORG = 'org-holds'
const SLOT = '2026-05-01T17:00:00.000Z'

async function holdRow(db: D1Database, slot = SLOT) {
  return db
    .prepare(
      'SELECT id, expires_at, guest_email FROM booking_holds WHERE org_id = ? AND slot_start_utc = ?'
    )
    .bind(ORG, slot)
    .first<{ id: string; expires_at: string; guest_email: string | null }>()
}

describe('holds DAL against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG)
  })

  it('claims a free slot with a fresh id and a five-minute expiry', async () => {
    const before = Date.now()
    const result = await acquireHold(db, ORG, SLOT, 'jamie@example.com')
    expect(result.acquired).toBe(true)
    const row = await holdRow(db)
    expect(row?.id).toBe(result.id)
    expect(row?.guest_email).toBe('jamie@example.com')
    const expiresMs = Date.parse(`${row?.expires_at.replace(' ', 'T')}Z`)
    expect(expiresMs - before).toBeGreaterThan(4 * 60_000)
    expect(expiresMs - before).toBeLessThanOrEqual(5 * 60_000 + 1_000)
  })

  it('refuses a slot whose hold is still live, leaving the holder in place', async () => {
    const first = await acquireHold(db, ORG, SLOT, 'jamie@example.com')
    const second = await acquireHold(db, ORG, SLOT, 'dana@example.com')
    expect(second).toEqual({ acquired: false, reason: 'slot_taken' })
    expect((await holdRow(db))?.id).toBe(first.id)
  })

  it('reclaims a slot whose hold has expired (a crashed request does not block it)', async () => {
    await db
      .prepare(
        `INSERT INTO booking_holds (id, org_id, slot_start_utc, expires_at, guest_email, created_at)
         VALUES ('stale', ?, ?, '2020-01-01 00:00:00', 'old@example.com', '2020-01-01 00:00:00')`
      )
      .bind(ORG, SLOT)
      .run()
    const result = await acquireHold(db, ORG, SLOT, 'dana@example.com')
    expect(result.acquired).toBe(true)
    const row = await holdRow(db)
    expect(row?.id).toBe(result.id)
    expect(row?.id).not.toBe('stale')
    expect(row?.guest_email).toBe('dana@example.com')
  })

  it('gives each claim a distinct id', async () => {
    const a = await acquireHold(db, ORG, SLOT, null)
    const b = await acquireHold(db, ORG, '2026-05-01T18:00:00.000Z', null)
    expect(a.id).toBeTruthy()
    expect(b.id).toBeTruthy()
    expect(a.id).not.toBe(b.id)
  })

  it('releaseHold frees the slot and is idempotent', async () => {
    const first = await acquireHold(db, ORG, SLOT, null)
    await releaseHold(db, first.id as string)
    await releaseHold(db, first.id as string)
    expect(await holdRow(db)).toBeNull()
    expect((await acquireHold(db, ORG, SLOT, null)).acquired).toBe(true)
  })
})

describe('holds DAL: SQL safety', () => {
  it('uses parameterized queries (no string interpolation)', () => {
    const code = readFileSync(resolve('src/lib/booking/holds.ts'), 'utf-8')
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

describe('toSqliteDatetime', () => {
  it('produces YYYY-MM-DD HH:MM:SS format (no T separator, no milliseconds, no Z)', () => {
    const result = toSqliteDatetime(new Date('2026-04-13T16:30:45.123Z'))
    expect(result).toBe('2026-04-13 16:30:45')
  })

  it('matches the format returned by SQLite datetime() function', () => {
    const result = toSqliteDatetime(new Date())
    // SQLite datetime format: exactly 'YYYY-MM-DD HH:MM:SS'
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })

  it('does not contain ISO-8601 artifacts (T or Z)', () => {
    const result = toSqliteDatetime(new Date())
    expect(result).not.toContain('T')
    expect(result).not.toContain('Z')
    expect(result).not.toContain('.')
  })
})
