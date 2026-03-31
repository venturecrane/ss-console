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

describe('time-entries: admin page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/engagements/[engId]/time.astro'), 'utf-8')

  it('time tracking page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/engagements/[engId]/time.astro'))).toBe(
      true
    )
  })

  it('loads time entries via listTimeEntries', () => {
    expect(source()).toContain('listTimeEntries')
  })

  it('loads engagement via getEngagement', () => {
    expect(source()).toContain('getEngagement')
  })

  it('loads client via getClient for breadcrumb', () => {
    expect(source()).toContain('getClient')
  })

  it('form posts to /api/admin/time-entries', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/time-entries"')
  })

  it('includes date, hours, category, and description fields', () => {
    const code = source()
    expect(code).toContain('name="date"')
    expect(code).toContain('name="hours"')
    expect(code).toContain('name="category"')
    expect(code).toContain('name="description"')
  })

  it('shows estimated vs actual hours comparison', () => {
    const code = source()
    expect(code).toContain('Hours Summary')
    expect(code).toContain('Estimated')
    expect(code).toContain('Actual')
    expect(code).toContain('Variance')
  })

  it('shows progress bar for hours', () => {
    const code = source()
    expect(code).toContain('Progress')
    expect(code).toContain('percentage')
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin/clients')
    expect(code).toContain('client.business_name')
    expect(code).toContain('Time Tracking')
  })

  it('has delete button per entry', () => {
    const code = source()
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
    expect(code).toContain('Delete')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })

  it('displays TIME_ENTRY_CATEGORIES in select', () => {
    const code = source()
    expect(code).toContain('TIME_ENTRY_CATEGORIES')
    expect(code).toContain('<select')
  })

  it('shows empty state when no entries', () => {
    const code = source()
    expect(code).toContain('No time entries yet')
  })
})

describe('time-entries: engagement detail integration', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/engagements/[engId].astro'), 'utf-8')

  it('engagement detail page imports listTimeEntries', () => {
    expect(source()).toContain('listTimeEntries')
  })

  it('engagement detail page has time tracking section', () => {
    const code = source()
    expect(code).toContain('Time Tracking')
    expect(code).toContain('timeEntries')
  })

  it('engagement detail page shows hours summary', () => {
    const code = source()
    expect(code).toContain('estimatedHours')
    expect(code).toContain('totalLoggedHours')
    expect(code).toContain('hoursVariance')
  })

  it('engagement detail page links to time tracking page', () => {
    const code = source()
    expect(code).toContain('/time')
    expect(code).toContain('View All')
  })
})
