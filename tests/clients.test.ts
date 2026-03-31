import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('clients: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/clients.ts'), 'utf-8')

  it('clients.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/clients.ts'))).toBe(true)
  })

  it('exports listClients function', () => {
    expect(source()).toContain('export async function listClients')
  })

  it('exports getClient function', () => {
    expect(source()).toContain('export async function getClient')
  })

  it('exports createClient function', () => {
    expect(source()).toContain('export async function createClient')
  })

  it('exports updateClient function', () => {
    expect(source()).toContain('export async function updateClient')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    // Ensure bind() is used for parameterized queries
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('scopes all queries to org_id', () => {
    const code = source()
    // listClients, getClient, createClient, updateClient all include org_id
    expect(code).toContain("'org_id = ?'")
    expect(code).toContain('org_id = ?')
  })

  it('supports status, vertical, and source filters in listClients', () => {
    const code = source()
    expect(code).toContain("'status = ?'")
    expect(code).toContain("'vertical = ?'")
    expect(code).toContain("'source = ?'")
  })

  it('defines all valid client verticals', () => {
    const code = source()
    expect(code).toContain('home_services')
    expect(code).toContain('professional_services')
    expect(code).toContain('contractor_trades')
    expect(code).toContain('retail_salon')
    expect(code).toContain('restaurant')
    expect(code).toContain("'other'")
  })

  it('defines all valid client statuses', () => {
    const code = source()
    expect(code).toContain("'prospect'")
    expect(code).toContain("'assessed'")
    expect(code).toContain("'quoted'")
    expect(code).toContain("'active'")
    expect(code).toContain("'completed'")
    expect(code).toContain("'dead'")
  })

  it('exports CLIENT_VERTICALS and CLIENT_STATUSES constants', () => {
    const code = source()
    expect(code).toContain('export const CLIENT_VERTICALS')
    expect(code).toContain('export const CLIENT_STATUSES')
  })

  it('exports listClientSources for filter dropdowns', () => {
    expect(source()).toContain('export async function listClientSources')
  })
})

describe('clients: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/clients/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/clients/index.ts'))).toBe(true)
  })

  it('update endpoint exists at src/pages/api/admin/clients/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/clients/[id].ts'))).toBe(true)
  })

  it('create endpoint validates required fields', () => {
    const code = readFileSync(resolve('src/pages/api/admin/clients/index.ts'), 'utf-8')
    expect(code).toContain('business_name')
    expect(code).toContain('source')
    expect(code).toContain('error=missing')
  })

  it('create endpoint calls createClient', () => {
    const code = readFileSync(resolve('src/pages/api/admin/clients/index.ts'), 'utf-8')
    expect(code).toContain('createClient')
  })

  it('update endpoint calls updateClient', () => {
    const code = readFileSync(resolve('src/pages/api/admin/clients/[id].ts'), 'utf-8')
    expect(code).toContain('updateClient')
  })

  it('create endpoint reads form data (server-rendered form submission)', () => {
    const code = readFileSync(resolve('src/pages/api/admin/clients/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('update endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/clients/[id].ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/clients/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/clients/[id].ts'), 'utf-8')
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })
})

describe('clients: pipeline view page', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/index.astro'), 'utf-8')

  it('pipeline page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/index.astro'))).toBe(true)
  })

  it('uses session data from auth middleware', () => {
    expect(source()).toContain('Astro.locals.session')
  })

  it('calls listClients to load client data', () => {
    expect(source()).toContain('listClients')
  })

  it('provides status filter control', () => {
    const code = source()
    expect(code).toContain('filter-status')
    expect(code).toContain('name="status"')
  })

  it('provides vertical filter control', () => {
    const code = source()
    expect(code).toContain('filter-vertical')
    expect(code).toContain('name="vertical"')
  })

  it('provides source filter control', () => {
    const code = source()
    expect(code).toContain('filter-source')
    expect(code).toContain('name="source"')
  })

  it('groups clients by status for pipeline view', () => {
    const code = source()
    expect(code).toContain('grouped')
    expect(code).toContain('statusOrder')
  })

  it('includes Add Client button linking to new page', () => {
    const code = source()
    expect(code).toContain('/admin/clients/new')
    expect(code).toContain('Add Client')
  })

  it('links each client to detail page', () => {
    expect(source()).toContain('/admin/clients/${client.id}')
  })

  it('displays business_name, vertical, employee_count, source', () => {
    const code = source()
    expect(code).toContain('client.business_name')
    expect(code).toContain('client.employee_count')
    expect(code).toContain('client.source')
    expect(code).toContain('verticalLabel')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('clients: create form page', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/new.astro'), 'utf-8')

  it('create page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/new.astro'))).toBe(true)
  })

  it('form posts to /api/admin/clients', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/clients"')
  })

  it('includes all client fields', () => {
    const code = source()
    expect(code).toContain('name="business_name"')
    expect(code).toContain('name="vertical"')
    expect(code).toContain('name="employee_count"')
    expect(code).toContain('name="years_in_business"')
    expect(code).toContain('name="source"')
    expect(code).toContain('name="referred_by"')
    expect(code).toContain('name="status"')
    expect(code).toContain('name="notes"')
  })

  it('marks business_name as required', () => {
    const code = source()
    // Check that business_name input has required attribute
    expect(code).toMatch(/id="business_name"[\s\S]*?required/)
  })

  it('marks source as required (OQ-002)', () => {
    const code = source()
    // Check that source input has required attribute
    expect(code).toMatch(/id="source"[\s\S]*?required/)
  })

  it('includes buy box warning for employee count (OQ-001)', () => {
    const code = source()
    expect(code).toContain('buy-box-warning')
    expect(code).toContain('Outside typical buy box of 10-25 employees')
  })

  it('has client-side buy box warning logic', () => {
    const code = source()
    expect(code).toContain('val < 10 || val > 25')
  })

  it('uses vertical dropdown with CHECK values', () => {
    const code = source()
    expect(code).toContain('CLIENT_VERTICALS')
    expect(code).toContain('Select a vertical')
  })

  it('defaults status to prospect', () => {
    const code = source()
    expect(code).toContain("s.value === 'prospect'")
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('clients: detail/edit page', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/[id].astro'), 'utf-8')

  it('detail page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id].astro'))).toBe(true)
  })

  it('loads client via getClient', () => {
    expect(source()).toContain('getClient')
  })

  it('redirects to clients list if client not found', () => {
    const code = source()
    expect(code).toContain("Astro.redirect('/admin/clients?error=not_found')")
  })

  it('form posts to /api/admin/clients/:id', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('/api/admin/clients/${client.id}')
  })

  it('pre-populates form fields with client data', () => {
    const code = source()
    expect(code).toContain('value={client.business_name}')
    expect(code).toContain('client.vertical === v.value')
    expect(code).toContain('client.employee_count')
    expect(code).toContain('client.source')
  })

  it('shows status progression indicator', () => {
    const code = source()
    expect(code).toContain('statusOrder')
    expect(code).toContain('getProgressionColor')
  })

  it('shows success message after save', () => {
    const code = source()
    expect(code).toContain("get('saved')")
    expect(code).toContain('Client updated successfully')
  })

  it('shows buy box warning when employee count outside 10-25', () => {
    const code = source()
    expect(code).toContain('outsideBuyBox')
    expect(code).toContain('Outside typical buy box of 10-25 employees')
  })

  it('displays created_at and updated_at metadata', () => {
    const code = source()
    expect(code).toContain('client.created_at')
    expect(code).toContain('client.updated_at')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('clients: admin dashboard integration', () => {
  it('admin dashboard links to clients page', () => {
    const code = readFileSync(resolve('src/pages/admin/index.astro'), 'utf-8')
    expect(code).toContain('/admin/clients')
    expect(code).toContain('Clients')
  })
})
