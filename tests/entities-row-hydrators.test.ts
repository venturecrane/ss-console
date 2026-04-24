/**
 * Tests for the prospect-row batch hydrators that gate the Log reply and
 * Send outreach actions on the entity list:
 *
 *   - getLatestOutreachDraftForEntities
 *   - getFirstContactWithEmailForEntities
 *
 * Uses @venturecrane/crane-test-harness for in-memory D1 with the real
 * migration schema so the SQL exercised here matches production.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity } from '../src/lib/db/entities'
import { appendContext, getLatestOutreachDraftForEntities } from '../src/lib/db/context'
import { createContact, getFirstContactWithEmailForEntities } from '../src/lib/db/contacts'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG_ID = 'org-test'

async function setup() {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  await db
    .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(ORG_ID, 'Test Org', 'test-org')
    .run()
  return db
}

describe('getLatestOutreachDraftForEntities', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await setup()
  })

  it('returns an empty map for an empty input', async () => {
    const result = await getLatestOutreachDraftForEntities(db, ORG_ID, [])
    expect(result.size).toBe(0)
  })

  it('returns no entry for an entity that has no outreach_draft', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'No Outreach' })
    await appendContext(db, ORG_ID, {
      entity_id: e.id,
      type: 'note',
      content: 'just a note',
      source: 'admin',
    })
    const result = await getLatestOutreachDraftForEntities(db, ORG_ID, [e.id])
    expect(result.has(e.id)).toBe(false)
  })

  it('returns the latest draft when an entity has multiple', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Has Outreach' })
    await appendContext(db, ORG_ID, {
      entity_id: e.id,
      type: 'outreach_draft',
      content: 'first draft',
      source: 'enrichment',
    })
    // Sleep just enough to ensure created_at differs at the second resolution
    await new Promise((r) => setTimeout(r, 1100))
    await appendContext(db, ORG_ID, {
      entity_id: e.id,
      type: 'outreach_draft',
      content: 'second draft',
      source: 'enrichment',
    })

    const result = await getLatestOutreachDraftForEntities(db, ORG_ID, [e.id])
    const draft = result.get(e.id)
    expect(draft?.content).toBe('second draft')
  })

  it('keys returned drafts by entity_id when multiple entities are queried', async () => {
    const a = await createEntity(db, ORG_ID, { name: 'A' })
    const b = await createEntity(db, ORG_ID, { name: 'B' })
    await appendContext(db, ORG_ID, {
      entity_id: a.id,
      type: 'outreach_draft',
      content: 'a-draft',
      source: 'enrichment',
    })
    await appendContext(db, ORG_ID, {
      entity_id: b.id,
      type: 'outreach_draft',
      content: 'b-draft',
      source: 'enrichment',
    })
    const result = await getLatestOutreachDraftForEntities(db, ORG_ID, [a.id, b.id])
    expect(result.get(a.id)?.content).toBe('a-draft')
    expect(result.get(b.id)?.content).toBe('b-draft')
  })

  it('does not leak drafts across orgs', async () => {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-other', 'Other Org', 'other')
      .run()
    const e = await createEntity(db, 'org-other', { name: 'Other Outreach' })
    await appendContext(db, 'org-other', {
      entity_id: e.id,
      type: 'outreach_draft',
      content: 'should-not-leak',
      source: 'enrichment',
    })
    // Query under a different org id; expect no result.
    const result = await getLatestOutreachDraftForEntities(db, ORG_ID, [e.id])
    expect(result.size).toBe(0)
  })
})

describe('getFirstContactWithEmailForEntities', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await setup()
  })

  it('returns an empty map for empty input', async () => {
    const result = await getFirstContactWithEmailForEntities(db, ORG_ID, [])
    expect(result.size).toBe(0)
  })

  it('returns the alphabetical-first contact whose email is non-empty', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Acme' })
    await createContact(db, ORG_ID, e.id, { name: 'Bart', email: 'bart@acme.test' })
    await createContact(db, ORG_ID, e.id, { name: 'Alice', email: 'alice@acme.test' })

    const result = await getFirstContactWithEmailForEntities(db, ORG_ID, [e.id])
    expect(result.get(e.id)?.name).toBe('Alice')
  })

  it('skips contacts with null or empty email', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Acme' })
    await createContact(db, ORG_ID, e.id, { name: 'Aaron', email: null })
    await createContact(db, ORG_ID, e.id, { name: 'Aaron2', email: '' })
    await createContact(db, ORG_ID, e.id, { name: 'Brenda', email: 'brenda@acme.test' })

    const result = await getFirstContactWithEmailForEntities(db, ORG_ID, [e.id])
    expect(result.get(e.id)?.name).toBe('Brenda')
  })

  it('omits entities that have no email-bearing contact', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Empty' })
    await createContact(db, ORG_ID, e.id, { name: 'Nobody', email: null })

    const result = await getFirstContactWithEmailForEntities(db, ORG_ID, [e.id])
    expect(result.has(e.id)).toBe(false)
  })

  it('does not leak contacts across orgs', async () => {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-other', 'Other', 'other')
      .run()
    const e = await createEntity(db, 'org-other', { name: 'Other' })
    await createContact(db, 'org-other', e.id, { name: 'Leak', email: 'leak@x.test' })

    const result = await getFirstContactWithEmailForEntities(db, ORG_ID, [e.id])
    expect(result.size).toBe(0)
  })
})
