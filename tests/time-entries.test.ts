import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('time-entries: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/time-entries.ts'), 'utf-8')

  it('time-entries.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/time-entries.ts'))).toBe(true)
  })

  it('exports listTimeEntries function', () => {
    expect(source()).toContain('export async function listTimeEntries')
  })

  it('exports getTimeEntry function', () => {
    expect(source()).toContain('export async function getTimeEntry')
  })

  it('exports createTimeEntry function', () => {
    expect(source()).toContain('export async function createTimeEntry')
  })

  it('exports updateTimeEntry function', () => {
    expect(source()).toContain('export async function updateTimeEntry')
  })

  it('exports deleteTimeEntry function', () => {
    expect(source()).toContain('export async function deleteTimeEntry')
  })

  it('exports recalculateActualHours function', () => {
    expect(source()).toContain('export async function recalculateActualHours')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('orders time entries by date DESC', () => {
    const code = source()
    expect(code).toContain('ORDER BY date DESC')
  })

  it('recalculateActualHours sums hours from time_entries', () => {
    const code = source()
    expect(code).toContain('SUM(hours)')
    expect(code).toContain('UPDATE engagements SET actual_hours')
  })

  it('createTimeEntry calls recalculateActualHours', () => {
    const code = source()
    // Find the createTimeEntry function and verify it calls recalculate
    const createFnStart = code.indexOf('export async function createTimeEntry')
    const createFnEnd = code.indexOf('export async function', createFnStart + 1)
    const createFn = code.slice(createFnStart, createFnEnd)
    expect(createFn).toContain('recalculateActualHours')
  })

  it('updateTimeEntry calls recalculateActualHours', () => {
    const code = source()
    const updateFnStart = code.indexOf('export async function updateTimeEntry')
    const updateFnEnd = code.indexOf('export async function', updateFnStart + 1)
    const updateFn = code.slice(updateFnStart, updateFnEnd)
    expect(updateFn).toContain('recalculateActualHours')
  })

  it('deleteTimeEntry calls recalculateActualHours', () => {
    const code = source()
    const deleteFnStart = code.indexOf('export async function deleteTimeEntry')
    const deleteFn = code.slice(deleteFnStart)
    expect(deleteFn).toContain('recalculateActualHours')
  })

  it('defines all valid time entry categories', () => {
    const code = source()
    expect(code).toContain("'solution_design'")
    expect(code).toContain("'implementation'")
    expect(code).toContain("'training'")
    expect(code).toContain("'admin'")
    expect(code).toContain("'other'")
  })

  it('every DAL function requires orgId as a parameter (#399)', () => {
    const code = source()
    // All public functions must accept orgId — no raw-ID primitives that
    // could be used to read or mutate rows outside the caller's org.
    const fnNames = [
      'listTimeEntries',
      'getTimeEntry',
      'createTimeEntry',
      'updateTimeEntry',
      'deleteTimeEntry',
      'recalculateActualHours',
    ]
    for (const name of fnNames) {
      const match = code.match(new RegExp(`export async function ${name}\\([^)]+\\)`, 's'))
      expect(match, `${name} signature`).toBeTruthy()
      expect(match![0], `${name} should accept orgId`).toContain('orgId: string')
    }
  })

  it('every SQL read/write against time_entries is scoped by org_id (#399)', () => {
    const code = source()
    // Enumerate the exact SQL statements and assert each is org-scoped.
    expect(code).toContain(
      'SELECT * FROM time_entries WHERE engagement_id = ? AND org_id = ? ORDER BY date DESC'
    )
    expect(code).toContain('SELECT * FROM time_entries WHERE id = ? AND org_id = ?')
    expect(code).toContain(
      'SELECT COALESCE(SUM(hours), 0) as total FROM time_entries WHERE engagement_id = ? AND org_id = ?'
    )
    expect(code).toContain('DELETE FROM time_entries WHERE id = ? AND org_id = ?')
    // UPDATE uses a dynamic SET clause but always appends WHERE id = ? AND org_id = ?
    expect(code).toContain(
      "UPDATE time_entries SET ${fields.join(', ')} WHERE id = ? AND org_id = ?"
    )
    // engagements UPDATE in recalculateActualHours must also be org-scoped.
    expect(code).toContain(
      "UPDATE engagements SET actual_hours = ?, updated_at = datetime('now') WHERE id = ? AND org_id = ?"
    )
  })

  it('exports TIME_ENTRY_CATEGORIES constant', () => {
    expect(source()).toContain('export const TIME_ENTRY_CATEGORIES')
  })

  it('exports TimeEntry interface', () => {
    expect(source()).toContain('export interface TimeEntry')
  })

  it('exports TimeEntryCategory type', () => {
    expect(source()).toContain('export type TimeEntryCategory')
  })

  it('recalculate uses COALESCE for zero-entry case', () => {
    expect(source()).toContain('COALESCE(SUM(hours), 0)')
  })
})

describe('time-entries: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/time-entries/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/time-entries/index.ts'))).toBe(true)
  })

  it('update/delete endpoint exists at src/pages/api/admin/time-entries/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/time-entries/[id].ts'))).toBe(true)
  })

  it('create endpoint validates required fields', () => {
    const code = readFileSync(resolve('src/pages/api/admin/time-entries/index.ts'), 'utf-8')
    expect(code).toContain('engagement_id')
    expect(code).toContain('date')
    expect(code).toContain('hours')
    expect(code).toContain('createTimeEntry')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/time-entries/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('update/delete endpoint supports _method=DELETE', () => {
    const code = readFileSync(resolve('src/pages/api/admin/time-entries/[id].ts'), 'utf-8')
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
    expect(code).toContain('deleteTimeEntry')
  })

  it('update/delete endpoint supports update', () => {
    const code = readFileSync(resolve('src/pages/api/admin/time-entries/[id].ts'), 'utf-8')
    expect(code).toContain('updateTimeEntry')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/time-entries/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/time-entries/[id].ts'), 'utf-8')
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })
})
