/**
 * Behavioral coverage for POST /api/admin/contacts/[id] (2026-08-14 code
 * review, Testing #2 — org-scoped update/delete with a _method=DELETE
 * override and null-vs-undefined sparse-update semantics, previously
 * untested).
 *
 * Real migrated D1, real route handler. The only fake is locals.session
 * (the admin-session shim's output — the same seam every admin route
 * trusts after middleware).
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

import { ORG_ID } from '../src/lib/constants'
import { POST } from '../src/pages/api/admin/contacts/[id]'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ENTITY_ID = 'entity-contacts-route'
const CONTACT_ID = 'contact-under-test'

function adminLocals(): App.Locals {
  return {
    session: {
      userId: 'u-admin-test',
      orgId: ORG_ID,
      role: 'admin',
      email: 'admin@example.com',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    },
  } as unknown as App.Locals
}

async function callRoute(opts: {
  contactId?: string
  fields?: Record<string, string>
  locals?: App.Locals
}): Promise<Response> {
  const form = new FormData()
  for (const [k, v] of Object.entries(opts.fields ?? {})) form.set(k, v)
  const request = new Request('https://admin.smd.services/api/admin/contacts/x', {
    method: 'POST',
    body: form,
  })
  return POST({
    request,
    locals: opts.locals ?? adminLocals(),
    params: { id: opts.contactId },
    redirect: (path: string, status?: number) =>
      new Response(null, { status: status ?? 302, headers: { Location: path } }),
  } as unknown as Parameters<typeof POST>[0])
}

async function readContact(db: D1Database, id: string) {
  return db.prepare('SELECT * FROM contacts WHERE id = ?').bind(id).first<{
    id: string
    org_id: string
    entity_id: string
    name: string
    email: string | null
    phone: string | null
    title: string | null
    role: string | null
  }>()
}

describe('POST /api/admin/contacts/[id]', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(ENTITY_ID, ORG_ID, 'Contacts Route Biz', 'contacts-route-biz')
      .run()
    await db
      .prepare(
        `INSERT INTO contacts (id, org_id, entity_id, name, email, phone, title, role)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        CONTACT_ID,
        ORG_ID,
        ENTITY_ID,
        'Dana Owner',
        'dana@example.com',
        '602-555-0100',
        'Owner',
        'decision_maker'
      )
      .run()
  })

  it('rejects a request with no admin session (401, no mutation)', async () => {
    const res = await callRoute({
      contactId: CONTACT_ID,
      fields: { _method: 'DELETE' },
      locals: { session: undefined } as unknown as App.Locals,
    })
    expect(res.status).toBe(401)
    expect(await readContact(db, CONTACT_ID)).not.toBeNull()
  })

  it('400s when the id param is missing', async () => {
    const res = await callRoute({ contactId: undefined, fields: { name: 'X' } })
    expect(res.status).toBe(400)
  })

  it('redirects to not_found for an unknown contact', async () => {
    const res = await callRoute({ contactId: 'nope', fields: { name: 'X' } })
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toBe('/admin/entities?error=not_found')
  })

  it('org scoping: a contact in another org reads as not_found and is not mutated', async () => {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-other', 'Other Org', 'other-org')
      .run()
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind('entity-other', 'org-other', 'Other Biz', 'other-biz')
      .run()
    await db
      .prepare(
        `INSERT INTO contacts (id, org_id, entity_id, name) VALUES (?, 'org-other', 'entity-other', 'Foreign Contact')`
      )
      .bind('contact-foreign')
      .run()

    const res = await callRoute({
      contactId: 'contact-foreign',
      fields: { _method: 'DELETE' },
    })
    expect(res.headers.get('Location')).toBe('/admin/entities?error=not_found')
    expect(await readContact(db, 'contact-foreign')).not.toBeNull()
  })

  it('_method=DELETE deletes the contact and redirects to the entity page', async () => {
    const res = await callRoute({ contactId: CONTACT_ID, fields: { _method: 'DELETE' } })
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toBe(`/admin/entities/${ENTITY_ID}?contact_deleted=1`)
    expect(await readContact(db, CONTACT_ID)).toBeNull()
  })

  it('sparse update: only the posted fields change; absent fields keep their values', async () => {
    const res = await callRoute({
      contactId: CONTACT_ID,
      fields: { email: 'dana.new@example.com' },
    })
    expect(res.headers.get('Location')).toBe(`/admin/entities/${ENTITY_ID}?contact_updated=1`)

    const row = await readContact(db, CONTACT_ID)
    expect(row?.email).toBe('dana.new@example.com')
    // Absent from the form -> untouched (undefined semantics).
    expect(row?.phone).toBe('602-555-0100')
    expect(row?.title).toBe('Owner')
    expect(row?.role).toBe('decision_maker')
    expect(row?.name).toBe('Dana Owner')
  })

  it('a posted-but-blank field CLEARS to null (null semantics)', async () => {
    await callRoute({ contactId: CONTACT_ID, fields: { phone: '   ' } })
    const row = await readContact(db, CONTACT_ID)
    expect(row?.phone).toBeNull()
    expect(row?.email).toBe('dana@example.com') // untouched
  })

  it('a blank name is rejected with missing_name and mutates nothing', async () => {
    const res = await callRoute({
      contactId: CONTACT_ID,
      fields: { name: '  ', email: 'should-not-land@example.com' },
    })
    expect(res.headers.get('Location')).toBe(`/admin/entities/${ENTITY_ID}?error=missing_name`)
    const row = await readContact(db, CONTACT_ID)
    expect(row?.name).toBe('Dana Owner')
    expect(row?.email).toBe('dana@example.com')
  })

  it('updates name and trims it', async () => {
    await callRoute({ contactId: CONTACT_ID, fields: { name: '  Dana O.  ' } })
    const row = await readContact(db, CONTACT_ID)
    expect(row?.name).toBe('Dana O.')
  })
})
