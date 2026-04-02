import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('contacts: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/contacts.ts'), 'utf-8')

  it('contacts.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/contacts.ts'))).toBe(true)
  })

  it('exports listContacts function', () => {
    expect(source()).toContain('export async function listContacts')
  })

  it('exports getContact function', () => {
    expect(source()).toContain('export async function getContact')
  })

  it('exports createContact function', () => {
    expect(source()).toContain('export async function createContact')
  })

  it('exports updateContact function', () => {
    expect(source()).toContain('export async function updateContact')
  })

  it('exports deleteContact function', () => {
    expect(source()).toContain('export async function deleteContact')
  })

  it('Contact interface includes role field', () => {
    const code = source()
    expect(code).toContain('role: string | null')
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

  it('scopes contact queries to org_id', () => {
    const code = source()
    expect(code).toContain('org_id = ?')
  })

  it('CreateContactData includes optional role field', () => {
    const code = source()
    expect(code).toContain('role?: string | null')
  })

  it('UpdateContactData includes optional role field', () => {
    const code = source()
    expect(code).toContain('role?: string | null')
  })

  it('updateContact handles role field updates', () => {
    const code = source()
    expect(code).toContain("fields.push('role = ?')")
  })

  it('createContact stores role in INSERT', () => {
    const code = source()
    expect(code).toContain('data.role ?? null')
  })
})

describe('contacts: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/clients/[id]/contacts/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'))).toBe(true)
  })

  it('update endpoint exists at src/pages/api/admin/clients/[id]/contacts/[contactId].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'))).toBe(
      true
    )
  })

  it('create endpoint validates required name field', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'),
      'utf-8'
    )
    expect(code).toContain("'name'")
    expect(code).toContain('error=missing')
  })

  it('create endpoint calls createContact', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'),
      'utf-8'
    )
    expect(code).toContain('createContact')
  })

  it('update endpoint calls updateContact', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'),
      'utf-8'
    )
    expect(code).toContain('updateContact')
  })

  it('update endpoint supports DELETE via _method override', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'),
      'utf-8'
    )
    expect(code).toContain('_method')
    expect(code).toContain("'DELETE'")
    expect(code).toContain('deleteContact')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'),
      'utf-8'
    )
    expect(code).toContain('request.formData()')
  })

  it('update endpoint reads form data', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'),
      'utf-8'
    )
    expect(code).toContain('request.formData()')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'),
      'utf-8'
    )
    const updateCode = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'),
      'utf-8'
    )
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })

  it('create endpoint verifies client exists before creating contact', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/index.ts'),
      'utf-8'
    )
    expect(code).toContain('getClient')
  })

  it('update endpoint verifies client exists before updating contact', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/clients/[id]/contacts/[contactId].ts'),
      'utf-8'
    )
    expect(code).toContain('getClient')
  })
})

describe('contacts: create form page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/contacts/new.astro'), 'utf-8')

  it('create page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/contacts/new.astro'))).toBe(true)
  })

  it('form posts to /api/admin/clients/:id/contacts', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('/api/admin/clients/${client.id}/contacts')
  })

  it('includes all contact fields', () => {
    const code = source()
    expect(code).toContain('name="name"')
    expect(code).toContain('name="email"')
    expect(code).toContain('name="phone"')
    expect(code).toContain('name="title"')
    expect(code).toContain('name="notes"')
  })

  it('marks name as required', () => {
    const code = source()
    expect(code).toMatch(/id="name"[\s\S]*?required/)
  })

  it('uses session data from auth middleware', () => {
    expect(source()).toContain('Astro.locals.session')
  })

  it('loads client via getClient for context', () => {
    expect(source()).toContain('getClient')
  })

  it('shows breadcrumb with client name', () => {
    const code = source()
    expect(code).toContain('client.business_name')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('contacts: detail/edit page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/contacts/[contactId].astro'), 'utf-8')

  it('detail page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/contacts/[contactId].astro'))).toBe(
      true
    )
  })

  it('loads contact via getContact', () => {
    expect(source()).toContain('getContact')
  })

  it('redirects if contact not found', () => {
    const code = source()
    expect(code).toContain('error=contact_not_found')
  })

  it('form posts to /api/admin/clients/:id/contacts/:contactId', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('/api/admin/clients/${client.id}/contacts/${contact.id}')
  })

  it('pre-populates form fields with contact data', () => {
    const code = source()
    expect(code).toContain('value={contact.name}')
    expect(code).toContain('contact.email')
    expect(code).toContain('contact.phone')
    expect(code).toContain('contact.title')
  })

  it('shows success message after save', () => {
    const code = source()
    expect(code).toContain("get('saved')")
    expect(code).toContain('Contact updated successfully')
  })

  it('supports delete via hidden form', () => {
    const code = source()
    expect(code).toContain('delete-form')
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
  })

  it('displays created_at metadata', () => {
    const code = source()
    expect(code).toContain('contact.created_at')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('contacts: client detail page integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/[id].astro'), 'utf-8')

  it('client detail page imports listContacts', () => {
    expect(source()).toContain('listContacts')
  })

  it('client detail page displays contacts section', () => {
    const code = source()
    expect(code).toContain('Contacts')
  })

  it('client detail page links to add contact form', () => {
    const code = source()
    expect(code).toContain('/contacts/new')
    expect(code).toContain('Add Contact')
  })

  it('client detail page links to contact detail pages', () => {
    const code = source()
    expect(code).toContain('/contacts/${contact.id}')
  })

  it('client detail page shows contact name, title, email, phone', () => {
    const code = source()
    expect(code).toContain('contact.name')
    expect(code).toContain('contact.title')
    expect(code).toContain('contact.email')
    expect(code).toContain('contact.phone')
  })

  it('shows contact saved success message', () => {
    const code = source()
    expect(code).toContain('contacts_saved')
    expect(code).toContain('Contact added successfully')
  })

  it('shows contact deleted success message', () => {
    const code = source()
    expect(code).toContain('contact_deleted')
    expect(code).toContain('Contact deleted')
  })
})
