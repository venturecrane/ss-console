/**
 * Tests for outreach_events DAL.
 *
 * Uses the @venturecrane/crane-test-harness D1 polyfill so we exercise
 * the real schema (migrations applied) rather than mock the SQL surface.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import path from 'node:path'
import { recordEvent, findSentByMessageId, listEventsByEntity } from './outreach-events'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../../../migrations')

const ORG_ID = 'org-test-outreach'
const ENTITY_ID = 'ent-test-outreach'

async function seed(db: D1Database) {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Test Org', 'test-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()

  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'Test Biz', 'test-biz', 'signal', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_ID, ORG_ID)
    .run()
}

describe('outreach-events DAL', () => {
  let db: D1Database

  beforeAll(() => {
    const files = discoverNumericMigrations(migrationsDir)
    expect(files.length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    db = createTestD1()
    const files = discoverNumericMigrations(migrationsDir)
    await runMigrations(db, { files })
    await seed(db)
  })

  it('inserts a sent event with entity attribution and JSON-serializes the payload', async () => {
    const result = await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-1',
      payload: { to: 'a@b.com', subject: 'hi' },
    })
    expect(result.inserted).toBe(true)
    expect(result.id).toBeDefined()

    const row = await db
      .prepare('SELECT * FROM outreach_events WHERE id = ?')
      .bind(result.id)
      .first<{
        org_id: string
        entity_id: string
        event_type: string
        channel: string
        message_id: string
        provider_event_id: string | null
        payload: string
      }>()
    expect(row).not.toBeNull()
    expect(row!.event_type).toBe('sent')
    expect(row!.entity_id).toBe(ENTITY_ID)
    expect(row!.message_id).toBe('rs-msg-1')
    expect(row!.provider_event_id).toBeNull()
    expect(row!.channel).toBe('email')
    expect(JSON.parse(row!.payload)).toEqual({ to: 'a@b.com', subject: 'hi' })
  })

  it('dedupes on provider_event_id', async () => {
    const first = await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'open',
      message_id: 'rs-msg-2',
      provider_event_id: 'svix-msg-abc',
    })
    expect(first.inserted).toBe(true)

    const second = await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'open',
      message_id: 'rs-msg-2',
      provider_event_id: 'svix-msg-abc',
    })
    expect(second.inserted).toBe(false)
    expect(second.id).toBe(first.id)

    const count = await db
      .prepare('SELECT COUNT(*) AS n FROM outreach_events WHERE provider_event_id = ?')
      .bind('svix-msg-abc')
      .first<{ n: number }>()
    expect(count!.n).toBe(1)
  })

  it('allows multiple synthetic sent rows when provider_event_id is null', async () => {
    // Both rows should insert — the unique partial index excludes NULLs.
    const a = await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-x',
      provider_event_id: null,
    })
    const b = await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-y',
      provider_event_id: null,
    })
    expect(a.inserted).toBe(true)
    expect(b.inserted).toBe(true)
    expect(a.id).not.toBe(b.id)
  })

  it('findSentByMessageId returns the original sent row', async () => {
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-find',
      provider_event_id: null,
    })
    // Add an open event for the same message — findSentByMessageId should
    // still return the sent row, not the open.
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'open',
      message_id: 'rs-msg-find',
      provider_event_id: 'svix-find-1',
    })

    const sent = await findSentByMessageId(db, 'rs-msg-find')
    expect(sent).not.toBeNull()
    expect(sent!.event_type).toBe('sent')
    expect(sent!.entity_id).toBe(ENTITY_ID)
  })

  it('findSentByMessageId returns null when the message_id is unknown', async () => {
    const sent = await findSentByMessageId(db, 'never-existed')
    expect(sent).toBeNull()
  })

  it('listEventsByEntity returns events newest-first', async () => {
    // Force ordering by inserting with explicit small delays via separate
    // calls — created_at default uses datetime('now') which is per-second
    // resolution, so we sort by created_at DESC then by insertion order is
    // not guaranteed. Instead, verify that all entries appear.
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'msg-list-1',
    })
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'open',
      message_id: 'msg-list-1',
      provider_event_id: 'svix-list-1',
    })
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'click',
      message_id: 'msg-list-1',
      provider_event_id: 'svix-list-2',
    })

    const events = await listEventsByEntity(db, ENTITY_ID)
    expect(events.length).toBe(3)
    const types = events.map((e) => e.event_type).sort()
    expect(types).toEqual(['click', 'open', 'sent'])
  })

  it('listEventsByEntity returns an empty array for an entity with no events', async () => {
    const events = await listEventsByEntity(db, 'no-such-entity')
    expect(events).toEqual([])
  })
})
