/**
 * Tests for the outreach send wrapper. Exercises the dev-mode branch
 * (no RESEND_API_KEY) to avoid network calls — the wrapper still records
 * the synthetic 'sent' row in that mode so dev/test flows produce
 * realistic telemetry.
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
import { sendOutreachEmail } from './resend'
import { findSentByMessageId, type OutreachEvent } from '../db/outreach-events'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../../../migrations')

const ORG_ID = 'org-outreach-send'
const ENTITY_ID = 'ent-outreach-send'

async function eventsForEntity(db: D1Database, entityId: string): Promise<OutreachEvent[]> {
  const result = await db
    .prepare(
      `SELECT * FROM outreach_events
       WHERE entity_id = ?
       ORDER BY created_at DESC`
    )
    .bind(entityId)
    .all<OutreachEvent>()
  return result.results ?? []
}

describe('sendOutreachEmail', () => {
  let db: D1Database

  beforeAll(() => {
    const files = discoverNumericMigrations(migrationsDir)
    expect(files.length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    db = createTestD1()
    const files = discoverNumericMigrations(migrationsDir)
    await runMigrations(db, { files })

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
         VALUES (?, ?, 'Biz', 'biz', 'signal', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(ENTITY_ID, ORG_ID)
      .run()
  })

  it('records a sent event with entity attribution in dev mode (no api key)', async () => {
    const result = await sendOutreachEmail(
      undefined, // dev mode
      {
        to: 'prospect@example.com',
        subject: 'Hello',
        html: '<p>Hi</p>',
      },
      { db, orgId: ORG_ID, entityId: ENTITY_ID }
    )
    expect(result.success).toBe(true)
    expect(result.id).toBe('dev-mode')
    expect(result.outreach_event_id).toBeDefined()

    const sent = await findSentByMessageId(db, 'dev-mode')
    expect(sent).not.toBeNull()
    expect(sent!.entity_id).toBe(ENTITY_ID)
    expect(sent!.org_id).toBe(ORG_ID)
    expect(sent!.event_type).toBe('sent')
  })

  it('still records when entityId is omitted (org-scoped)', async () => {
    const result = await sendOutreachEmail(
      undefined,
      { to: 't@e.com', subject: 's', html: 'h' },
      { db, orgId: ORG_ID }
    )
    expect(result.success).toBe(true)
    expect(result.outreach_event_id).toBeDefined()
  })

  it('dedupes multiple dev-mode sends to the same entity (PR-2b behavior)', async () => {
    // Pre-PR-2b: two sends with the same message_id produced two
    // outreach_events rows. The PR-2b dedup at recordEvent (keyed on
    // (org_id, message_id) for synthetic 'sent' rows) collapses them
    // to one. In dev mode every call returns id='dev-mode', so the
    // dedup fires here. In production each Resend call returns a
    // unique message_id, so distinct sends still produce distinct rows
    // — covered by the next test.
    await sendOutreachEmail(
      undefined,
      { to: 'a@e.com', subject: 'A', html: 'a' },
      { db, orgId: ORG_ID, entityId: ENTITY_ID }
    )
    await sendOutreachEmail(
      undefined,
      { to: 'a@e.com', subject: 'B', html: 'b' },
      { db, orgId: ORG_ID, entityId: ENTITY_ID }
    )

    const events = await eventsForEntity(db, ENTITY_ID)
    expect(events.length).toBe(1)
    expect(events[0].event_type).toBe('sent')
    expect(events[0].message_id).toBe('dev-mode')
  })

  it('records distinct rows for distinct Resend message_ids', async () => {
    // Mock global fetch so we can return distinct ids per call.
    const originalFetch = globalThis.fetch
    let i = 0
    globalThis.fetch = async () => {
      i++
      return new Response(JSON.stringify({ id: `resend-msg-${i}` }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    try {
      await sendOutreachEmail(
        'rk_test',
        { to: 'a@e.com', subject: 'A', html: 'a' },
        { db, orgId: ORG_ID, entityId: ENTITY_ID }
      )
      await sendOutreachEmail(
        'rk_test',
        { to: 'a@e.com', subject: 'B', html: 'b' },
        { db, orgId: ORG_ID, entityId: ENTITY_ID }
      )

      const events = await eventsForEntity(db, ENTITY_ID)
      expect(events.length).toBe(2)
      expect(events.every((e) => e.event_type === 'sent')).toBe(true)
      expect(new Set(events.map((e) => e.message_id)).size).toBe(2)
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
