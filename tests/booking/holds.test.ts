import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { toSqliteDatetime } from '../../src/lib/booking/holds'

/**
 * Holds DAL — structural source-string tests + format validation.
 *
 * Full behavioral verification of the upsert-when-expired pattern requires
 * a D1 instance and is covered by integration tests against local D1.
 * This file ensures the source contains the critical SQL pieces and that
 * datetime formatting matches SQLite's expectations.
 */

const source = () => readFileSync(resolve('src/lib/booking/holds.ts'), 'utf-8')

describe('holds DAL: structural', () => {
  it('exports acquireHold, releaseHold, cleanupExpiredHolds', () => {
    const code = source()
    expect(code).toContain('export async function acquireHold')
    expect(code).toContain('export async function releaseHold')
    expect(code).toContain('export async function cleanupExpiredHolds')
  })

  it('acquireHold uses upsert-when-expired pattern (ON CONFLICT … DO UPDATE … WHERE expires_at < now)', () => {
    const code = source()
    expect(code).toContain('ON CONFLICT(org_id, slot_start_utc) DO UPDATE')
    expect(code).toContain("WHERE booking_holds.expires_at < datetime('now')")
  })

  it('acquireHold returns the new id via RETURNING and verifies it matches', () => {
    const code = source()
    expect(code).toContain('RETURNING id')
    expect(code).toContain('result.id !== id')
  })

  it('uses parameterized queries (no string interpolation)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs via crypto.randomUUID()', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('hold TTL is 5 minutes (matches the plan and reserve flow)', () => {
    expect(source()).toContain('HOLD_TTL_MINUTES = 5')
  })

  it('cleanupExpiredHolds deletes rows older than 1 hour past expiry (safety margin)', () => {
    expect(source()).toContain("expires_at < datetime('now', '-1 hour')")
  })

  it('releaseHold is idempotent — DELETE WHERE id matches', () => {
    const code = source()
    expect(code).toContain('DELETE FROM booking_holds WHERE id = ?')
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
