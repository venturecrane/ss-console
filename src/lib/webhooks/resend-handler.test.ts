/**
 * Tests for the Resend webhook handler — event-type mapping, entity
 * re-attribution from sent rows, and idempotent dedupe.
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
import { handleResendEvent, mapResendEventType } from './resend-handler'
import { recordEvent } from '../db/outreach-events'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../../../migrations')

const ORG_ID = 'org-resend-test'
const ENTITY_ID = 'ent-resend-test'
const FALLBACK_ORG = 'org-fallback'

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
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Fallback Org', 'fallback-org', datetime('now'), datetime('now'))`
    )
    .bind(FALLBACK_ORG)
    .run()

  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'Test Biz', 'test-biz', 'signal', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_ID, ORG_ID)
    .run()
}

describe('mapResendEventType', () => {
  it('maps every supported Resend event type', () => {
    expect(mapResendEventType('email.sent')).toBe('sent')
    expect(mapResendEventType('email.delivered')).toBe('sent')
    expect(mapResendEventType('email.opened')).toBe('open')
    expect(mapResendEventType('email.clicked')).toBe('click')
    expect(mapResendEventType('email.bounced')).toBe('bounce')
    expect(mapResendEventType('email.complained')).toBe('bounce')
  })

  it('returns null for events we do not record', () => {
    expect(mapResendEventType('email.delivery_delayed')).toBeNull()
    expect(mapResendEventType('contact.created')).toBeNull()
    expect(mapResendEventType('domain.created')).toBeNull()
    expect(mapResendEventType('unknown.thing')).toBeNull()
  })
})

describe('handleResendEvent', () => {
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

  it('re-attributes opens to the originating entity via the sent row', async () => {
    // Send wrapper would have written this row.
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-001',
    })

    const result = await handleResendEvent(db, {
      providerEventId: 'svix-evt-1',
      payload: {
        type: 'email.opened',
        data: { email_id: 'rs-msg-001' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })

    expect(result.recorded).toBe(true)
    expect(result.eventType).toBe('open')
    expect(result.entityId).toBe(ENTITY_ID)

    const row = await db
      .prepare(
        `SELECT entity_id, org_id, event_type FROM outreach_events
         WHERE provider_event_id = ?`
      )
      .bind('svix-evt-1')
      .first<{ entity_id: string; org_id: string; event_type: string }>()
    expect(row!.entity_id).toBe(ENTITY_ID)
    expect(row!.org_id).toBe(ORG_ID)
    expect(row!.event_type).toBe('open')
  })

  it('dedupes Svix retries on provider_event_id', async () => {
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-dedupe',
    })

    const first = await handleResendEvent(db, {
      providerEventId: 'svix-dedupe-1',
      payload: {
        type: 'email.opened',
        data: { email_id: 'rs-msg-dedupe' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(first.recorded).toBe(true)

    const retry = await handleResendEvent(db, {
      providerEventId: 'svix-dedupe-1',
      payload: {
        type: 'email.opened',
        data: { email_id: 'rs-msg-dedupe' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(retry.recorded).toBe(false)
    expect(retry.reason).toBe('deduped')

    const count = await db
      .prepare(
        `SELECT COUNT(*) AS n FROM outreach_events
         WHERE provider_event_id = ?`
      )
      .bind('svix-dedupe-1')
      .first<{ n: number }>()
    expect(count!.n).toBe(1)
  })

  it('skips events with unmapped types', async () => {
    const result = await handleResendEvent(db, {
      providerEventId: 'svix-skip-1',
      payload: { type: 'email.delivery_delayed', data: { email_id: 'rs-msg-skip' } },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(result.recorded).toBe(false)
    expect(result.reason).toBe('unhandled_event_type')
  })

  it('skips events that lack an email_id', async () => {
    const result = await handleResendEvent(db, {
      providerEventId: 'svix-noid-1',
      payload: { type: 'email.opened', data: {} },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(result.recorded).toBe(false)
    expect(result.reason).toBe('no_message_id')
  })

  it('falls back to fallbackOrgId and null entity_id when no sent row exists', async () => {
    const result = await handleResendEvent(db, {
      providerEventId: 'svix-orphan-1',
      payload: {
        type: 'email.bounced',
        data: { email_id: 'rs-msg-orphan' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(result.recorded).toBe(true)
    expect(result.eventType).toBe('bounce')
    expect(result.entityId).toBeNull()

    const row = await db
      .prepare(
        `SELECT entity_id, org_id FROM outreach_events
         WHERE provider_event_id = ?`
      )
      .bind('svix-orphan-1')
      .first<{ entity_id: string | null; org_id: string }>()
    expect(row!.entity_id).toBeNull()
    expect(row!.org_id).toBe(FALLBACK_ORG)
  })

  it('email.delivered collapses to the sent event type', async () => {
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'rs-msg-delivered',
    })

    const result = await handleResendEvent(db, {
      providerEventId: 'svix-delivered-1',
      payload: {
        type: 'email.delivered',
        data: { email_id: 'rs-msg-delivered' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(result.recorded).toBe(true)
    expect(result.eventType).toBe('sent')
    expect(result.entityId).toBe(ENTITY_ID)
  })

  it('email.complained maps to bounce', async () => {
    const result = await handleResendEvent(db, {
      providerEventId: 'svix-comp-1',
      payload: {
        type: 'email.complained',
        data: { email_id: 'rs-msg-complained' },
      },
      fallbackOrgId: FALLBACK_ORG,
    })
    expect(result.recorded).toBe(true)
    expect(result.eventType).toBe('bounce')
  })
})
