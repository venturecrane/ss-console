/**
 * Tests for the meetings-stage batch hydrators that the entity list uses
 * to compute per-row sub-state, next-meeting date, and draftable-meeting
 * presence:
 *
 *   - getMeetingsForEntities
 *   - getQuotesForEntities
 *
 * Both follow the same pattern as the prospect-row hydrators: keyed by
 * org + entity_id IN (json_each(?)), returns Map<entityId, list>. The
 * tests use the real D1 schema so the SQL exercised here matches prod.
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
import { createMeeting, getMeetingsForEntities } from '../src/lib/db/meetings'
import { getQuotesForEntities } from '../src/lib/db/quotes'

/**
 * Insert a quote row directly, bypassing createQuote's assessment FK
 * chain. The hydrator only reads from `quotes`; we don't need a real
 * assessments / clients graph behind it for this batch-helper contract
 * test. We disable foreign_keys for the duration of the insert so the
 * `assessment_id` column doesn't reject the synthetic id.
 */
async function insertQuoteRaw(
  db: D1Database,
  orgId: string,
  entityId: string,
  status: string
): Promise<void> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()
  await db.prepare('PRAGMA foreign_keys = OFF').run()
  await db
    .prepare(
      `INSERT INTO quotes (
         id, org_id, entity_id, assessment_id, meeting_id, version,
         line_items, total_hours, rate, total_price, deposit_pct,
         deposit_amount, status, created_at, updated_at
       ) VALUES (?, ?, ?, ?, ?, 1, '[]', 0, 0, 0, 0.5, 0, ?, ?, ?)`
    )
    .bind(id, orgId, entityId, crypto.randomUUID(), null, status, now, now)
    .run()
  await db.prepare('PRAGMA foreign_keys = ON').run()
}

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

describe('getMeetingsForEntities', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await setup()
  })

  it('returns an empty map for empty input', async () => {
    const result = await getMeetingsForEntities(db, ORG_ID, [])
    expect(result.size).toBe(0)
  })

  it('groups meetings by entity_id', async () => {
    const a = await createEntity(db, ORG_ID, { name: 'A' })
    const b = await createEntity(db, ORG_ID, { name: 'B' })
    await createMeeting(db, ORG_ID, a.id, { meeting_type: 'discovery' })
    await createMeeting(db, ORG_ID, a.id, { meeting_type: 'follow_up' })
    await createMeeting(db, ORG_ID, b.id, { meeting_type: 'review' })

    const result = await getMeetingsForEntities(db, ORG_ID, [a.id, b.id])
    expect(result.get(a.id)?.length).toBe(2)
    expect(result.get(b.id)?.length).toBe(1)
  })

  it('orders by COALESCE(scheduled_at, created_at) DESC within each entity', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Acme' })
    await createMeeting(db, ORG_ID, e.id, {
      scheduled_at: '2026-05-01T00:00:00Z',
      meeting_type: 'newer',
    })
    await createMeeting(db, ORG_ID, e.id, {
      scheduled_at: '2026-04-01T00:00:00Z',
      meeting_type: 'older',
    })
    const result = await getMeetingsForEntities(db, ORG_ID, [e.id])
    const list = result.get(e.id) ?? []
    expect(list.map((m) => m.meeting_type)).toEqual(['newer', 'older'])
  })

  it('does not leak meetings across orgs', async () => {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-other', 'Other', 'other')
      .run()
    const e = await createEntity(db, 'org-other', { name: 'Leak' })
    await createMeeting(db, 'org-other', e.id, { meeting_type: 'leak' })

    const result = await getMeetingsForEntities(db, ORG_ID, [e.id])
    expect(result.size).toBe(0)
  })
})

describe('getQuotesForEntities', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await setup()
  })

  it('returns an empty map for empty input', async () => {
    const result = await getQuotesForEntities(db, ORG_ID, [])
    expect(result.size).toBe(0)
  })

  it('returns all quotes (any status) keyed by entity_id', async () => {
    const e = await createEntity(db, ORG_ID, { name: 'Quotes' })
    await insertQuoteRaw(db, ORG_ID, e.id, 'draft')
    await insertQuoteRaw(db, ORG_ID, e.id, 'declined')

    const result = await getQuotesForEntities(db, ORG_ID, [e.id])
    const quotes = result.get(e.id) ?? []
    expect(quotes.length).toBe(2)
    expect(quotes.every((q) => q.entity_id === e.id)).toBe(true)
    // Includes terminal-status quotes too — that's the point versus
    // getActiveQuotesForEntities, which filters them out.
    expect(quotes.map((q) => q.status).sort()).toEqual(['declined', 'draft'])
  })

  it('does not leak quotes across orgs', async () => {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-other', 'Other', 'other')
      .run()
    const e = await createEntity(db, 'org-other', { name: 'Leak' })
    await insertQuoteRaw(db, 'org-other', e.id, 'draft')

    const result = await getQuotesForEntities(db, ORG_ID, [e.id])
    expect(result.size).toBe(0)
  })
})
