import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('parking-lot: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/parking-lot.ts'), 'utf-8')

  it('parking-lot.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/parking-lot.ts'))).toBe(true)
  })

  it('exports listParkingLotItems function', () => {
    expect(source()).toContain('export async function listParkingLotItems')
  })

  it('exports getParkingLotItem function', () => {
    expect(source()).toContain('export async function getParkingLotItem')
  })

  it('exports createParkingLotItem function', () => {
    expect(source()).toContain('export async function createParkingLotItem')
  })

  it('exports updateParkingLotItem function', () => {
    expect(source()).toContain('export async function updateParkingLotItem')
  })

  it('exports disposeParkingLotItem function', () => {
    expect(source()).toContain('export async function disposeParkingLotItem')
  })

  it('exports linkFollowOnQuote function', () => {
    expect(source()).toContain('export async function linkFollowOnQuote')
  })

  it('exports deleteParkingLotItem function', () => {
    expect(source()).toContain('export async function deleteParkingLotItem')
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

  it('orders parking lot items by created_at DESC', () => {
    const code = source()
    expect(code).toContain('ORDER BY created_at DESC')
  })

  it('defines all valid dispositions', () => {
    const code = source()
    expect(code).toContain("'fold_in'")
    expect(code).toContain("'follow_on'")
    expect(code).toContain("'dropped'")
  })

  it('exports DISPOSITIONS constant', () => {
    expect(source()).toContain('export const DISPOSITIONS')
  })

  it('exports ParkingLotItem interface', () => {
    expect(source()).toContain('export interface ParkingLotItem')
  })

  it('exports Disposition type', () => {
    expect(source()).toContain('export type Disposition')
  })

  it('disposeParkingLotItem sets disposition, disposition_note, and reviewed_at', () => {
    const code = source()
    const disposeFnStart = code.indexOf('export async function disposeParkingLotItem')
    const disposeFnEnd = code.indexOf('export async function', disposeFnStart + 1)
    const disposeFn = code.slice(disposeFnStart, disposeFnEnd)
    expect(disposeFn).toContain('disposition')
    expect(disposeFn).toContain('disposition_note')
    expect(disposeFn).toContain('reviewed_at')
  })

  it('linkFollowOnQuote sets follow_on_quote_id', () => {
    const code = source()
    const linkFnStart = code.indexOf('export async function linkFollowOnQuote')
    const linkFnEnd = code.indexOf('export async function', linkFnStart + 1)
    const linkFn = code.slice(linkFnStart, linkFnEnd)
    expect(linkFn).toContain('follow_on_quote_id')
    expect(linkFn).toContain('quoteId')
  })

  it('ParkingLotItem interface includes follow_on_quote_id', () => {
    expect(source()).toContain('follow_on_quote_id')
  })

  it('ParkingLotItem interface includes requested_by', () => {
    expect(source()).toContain('requested_by')
  })

  it('ParkingLotItem interface includes requested_at', () => {
    expect(source()).toContain('requested_at')
  })
})

describe('parking-lot: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/parking-lot/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/parking-lot/index.ts'))).toBe(true)
  })

  it('update/dispose/delete endpoint exists at src/pages/api/admin/parking-lot/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/parking-lot/[id].ts'))).toBe(true)
  })

  it('create endpoint validates required fields', () => {
    const code = readFileSync(resolve('src/pages/api/admin/parking-lot/index.ts'), 'utf-8')
    expect(code).toContain('engagement_id')
    expect(code).toContain('description')
    expect(code).toContain('createParkingLotItem')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/parking-lot/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('update endpoint supports _method=DELETE', () => {
    const code = readFileSync(resolve('src/pages/api/admin/parking-lot/[id].ts'), 'utf-8')
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
    expect(code).toContain('deleteParkingLotItem')
  })

  it('update endpoint supports disposition action', () => {
    const code = readFileSync(resolve('src/pages/api/admin/parking-lot/[id].ts'), 'utf-8')
    expect(code).toContain("action === 'dispose'")
    expect(code).toContain('disposeParkingLotItem')
    expect(code).toContain('disposition')
    expect(code).toContain('disposition_note')
  })

  it('update endpoint validates disposition values', () => {
    const code = readFileSync(resolve('src/pages/api/admin/parking-lot/[id].ts'), 'utf-8')
    expect(code).toContain('fold_in')
    expect(code).toContain('follow_on')
    expect(code).toContain('dropped')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/parking-lot/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/parking-lot/[id].ts'), 'utf-8')
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })
})

describe('parking-lot: admin page', () => {
  const source = () =>
    readFileSync(
      resolve('src/pages/admin/clients/[id]/engagements/[engId]/parking-lot.astro'),
      'utf-8'
    )

  it('parking lot page exists', () => {
    expect(
      existsSync(resolve('src/pages/admin/clients/[id]/engagements/[engId]/parking-lot.astro'))
    ).toBe(true)
  })

  it('loads parking lot items via listParkingLotItems', () => {
    expect(source()).toContain('listParkingLotItems')
  })

  it('loads engagement via getEngagement', () => {
    expect(source()).toContain('getEngagement')
  })

  it('loads client via getClient for breadcrumb', () => {
    expect(source()).toContain('getClient')
  })

  it('form posts to /api/admin/parking-lot', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/parking-lot"')
  })

  it('includes description and requested_by fields', () => {
    const code = source()
    expect(code).toContain('name="description"')
    expect(code).toContain('name="requested_by"')
  })

  it('shows disposition controls for undispositioned items', () => {
    const code = source()
    expect(code).toContain('name="disposition"')
    expect(code).toContain('name="disposition_note"')
    expect(code).toContain('Dispose')
  })

  it('shows color coding for disposition status', () => {
    const code = source()
    expect(code).toContain('dispositionColor')
    expect(code).toContain('dispositionBadge')
    expect(code).toContain('bg-green-100') // fold_in
    expect(code).toContain('bg-blue-100') // follow_on
    expect(code).toContain('bg-amber-50') // undispositioned
  })

  it('has follow-on quote link for follow_on items', () => {
    const code = source()
    expect(code).toContain('Create Follow-on Quote')
    expect(code).toContain('follow_on_quote_id')
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin/clients')
    expect(code).toContain('client.business_name')
    expect(code).toContain('Parking Lot')
  })

  it('has delete button per item', () => {
    const code = source()
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
    expect(code).toContain('Delete')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })

  it('shows DISPOSITIONS in select dropdown', () => {
    const code = source()
    expect(code).toContain('DISPOSITIONS')
    expect(code).toContain('<select')
  })

  it('shows empty state when no items', () => {
    const code = source()
    expect(code).toContain('No parking lot items yet')
  })

  it('shows summary with total items and needs review count', () => {
    const code = source()
    expect(code).toContain('Total Items')
    expect(code).toContain('Needs Review')
    expect(code).toContain('undispositioned')
  })
})

describe('parking-lot: engagement detail integration', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/engagements/[engId].astro'), 'utf-8')

  it('engagement detail page imports listParkingLotItems', () => {
    expect(source()).toContain('listParkingLotItems')
  })

  it('engagement detail page has parking lot section', () => {
    const code = source()
    expect(code).toContain('Parking Lot')
    expect(code).toContain('parkingLotItems')
  })

  it('engagement detail page shows item count and undispositioned count', () => {
    const code = source()
    expect(code).toContain('parkingLotTotal')
    expect(code).toContain('parkingLotUndispositioned')
  })

  it('engagement detail page links to parking lot page', () => {
    const code = source()
    expect(code).toContain('/parking-lot')
    expect(code).toContain('View All')
  })
})
