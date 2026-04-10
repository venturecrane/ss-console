import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import { handleDocumentCompleted } from './signwell-handler'
import type { SignWellWebhookPayload } from '../signwell/types'
import type { LineItem } from '../db/quotes'
import path from 'node:path'

// ---------------------------------------------------------------------------
// Mock external services — SignWell PDF download and Resend email
// ---------------------------------------------------------------------------

vi.mock('../signwell/client', () => ({
  getSignedPdf: vi.fn().mockResolvedValue(new Uint8Array([0x25, 0x50, 0x44, 0x46])),
}))

vi.mock('../email/resend', () => ({
  sendEmail: vi.fn().mockResolvedValue({ success: true, id: 'test-email-id' }),
}))

// ---------------------------------------------------------------------------
// Fake R2 bucket
// ---------------------------------------------------------------------------

function createFakeR2(): R2Bucket {
  const store = new Map<string, unknown>()
  return {
    put: vi.fn(async (key: string, value: unknown) => {
      store.set(key, value)
      return {} as R2Object
    }),
    get: vi.fn(),
    head: vi.fn(),
    delete: vi.fn(),
    list: vi.fn(),
    createMultipartUpload: vi.fn(),
    resumeMultipartUpload: vi.fn(),
  } as unknown as R2Bucket
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ORG_ID = 'org-test-001'
const ENTITY_ID = 'entity-test-001'
const ASSESSMENT_ID = 'assessment-test-001'
const QUOTE_ID = 'quote-test-001'
const SIGNWELL_DOC_ID = 'sw-doc-001'

const LINE_ITEMS: LineItem[] = [
  { problem: 'Owner bottleneck', description: 'Document key processes', estimated_hours: 8 },
  { problem: 'Lead leakage', description: 'Set up CRM and pipeline', estimated_hours: 12 },
  { problem: 'Scheduling chaos', description: 'Configure scheduling tool', estimated_hours: 6 },
  { problem: 'Manual communication', description: 'Build email templates', estimated_hours: 4 },
]

function makePayload(): SignWellWebhookPayload {
  return {
    event: 'document_completed',
    data: {
      id: SIGNWELL_DOC_ID,
      name: 'SOW - Test Business',
      status: 'completed',
      signers: [
        {
          id: 's1',
          name: 'Test Owner',
          email: 'owner@test.com',
          signed_at: new Date().toISOString(),
        },
      ],
      completed_at: new Date().toISOString(),
    },
  }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../../../migrations')

describe('handleDocumentCompleted — milestone creation', () => {
  let db: D1Database

  beforeAll(async () => {
    const files = discoverNumericMigrations(migrationsDir)
    expect(files.length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    db = createTestD1()
    const files = discoverNumericMigrations(migrationsDir)
    await runMigrations(db, { files })

    // Seed org
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'Test Org', 'test-org', datetime('now'), datetime('now'))`
      )
      .bind(ORG_ID)
      .run()

    // Seed entity at 'proposing' stage (the stage before 'engaged')
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
         VALUES (?, ?, 'Test Business', 'test-business', 'proposing', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(ENTITY_ID, ORG_ID)
      .run()

    // Seed assessment
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status, created_at)
         VALUES (?, ?, ?, 'completed', datetime('now'))`
      )
      .bind(ASSESSMENT_ID, ORG_ID, ENTITY_ID)
      .run()

    // Seed quote in 'sent' status with line items
    const totalHours = LINE_ITEMS.reduce((sum, item) => sum + item.estimated_hours, 0)
    const rate = 150
    const totalPrice = totalHours * rate
    const depositAmount = totalPrice * 0.5

    await db
      .prepare(
        `INSERT INTO quotes (id, org_id, entity_id, assessment_id, version, line_items, total_hours, rate, total_price, deposit_pct, deposit_amount, status, signwell_doc_id, sent_at, expires_at, created_at, updated_at)
         VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 0.5, ?, 'sent', ?, datetime('now'), datetime('now', '+5 days'), datetime('now'), datetime('now'))`
      )
      .bind(
        QUOTE_ID,
        ORG_ID,
        ENTITY_ID,
        ASSESSMENT_ID,
        JSON.stringify(LINE_ITEMS),
        totalHours,
        rate,
        totalPrice,
        depositAmount,
        SIGNWELL_DOC_ID
      )
      .run()
  })

  it('creates milestones from quote line items with correct fields', async () => {
    const r2 = createFakeR2()
    const res = await handleDocumentCompleted(
      db,
      r2,
      'fake-api-key',
      undefined,
      undefined,
      makePayload()
    )

    expect(res.status).toBe(200)

    const milestones = await db
      .prepare('SELECT * FROM milestones ORDER BY sort_order ASC')
      .all<Record<string, unknown>>()

    expect(milestones.results).toHaveLength(4)

    for (let i = 0; i < LINE_ITEMS.length; i++) {
      const m = milestones.results[i]
      expect(m.name).toBe(LINE_ITEMS[i].problem)
      expect(m.description).toBe(LINE_ITEMS[i].description)
      expect(m.status).toBe('pending')
      expect(m.sort_order).toBe(i)
    }
  })

  it('sets payment_trigger = true only on the last milestone', async () => {
    const r2 = createFakeR2()
    await handleDocumentCompleted(db, r2, 'fake-api-key', undefined, undefined, makePayload())

    const milestones = await db
      .prepare('SELECT * FROM milestones ORDER BY sort_order ASC')
      .all<Record<string, unknown>>()

    expect(milestones.results).toHaveLength(4)

    for (let i = 0; i < 3; i++) {
      expect(milestones.results[i].payment_trigger).toBe(0)
    }

    expect(milestones.results[3].payment_trigger).toBe(1)
  })

  it('preserves sort_order matching line item index', async () => {
    const r2 = createFakeR2()
    await handleDocumentCompleted(db, r2, 'fake-api-key', undefined, undefined, makePayload())

    const milestones = await db
      .prepare('SELECT sort_order, name FROM milestones ORDER BY sort_order ASC')
      .all<{ sort_order: number; name: string }>()

    expect(milestones.results.map((m) => m.sort_order)).toEqual([0, 1, 2, 3])
    expect(milestones.results.map((m) => m.name)).toEqual(LINE_ITEMS.map((li) => li.problem))
  })

  it('writes a stage_change context entry', async () => {
    const r2 = createFakeR2()
    await handleDocumentCompleted(db, r2, 'fake-api-key', undefined, undefined, makePayload())

    const contextEntries = await db
      .prepare("SELECT * FROM context WHERE entity_id = ? AND type = 'stage_change'")
      .bind(ENTITY_ID)
      .all<Record<string, unknown>>()

    expect(contextEntries.results).toHaveLength(1)

    const entry = contextEntries.results[0]
    expect(entry.source).toBe('signwell-webhook')
    expect(entry.content).toContain('engaged')

    const metadata = JSON.parse(entry.metadata as string)
    expect(metadata.from).toBe('proposing')
    expect(metadata.to).toBe('engaged')
    expect(metadata.engagement_id).toBeDefined()
    expect(metadata.quote_id).toBe(QUOTE_ID)
  })

  it('links milestones to the created engagement', async () => {
    const r2 = createFakeR2()
    await handleDocumentCompleted(db, r2, 'fake-api-key', undefined, undefined, makePayload())

    const engagement = await db
      .prepare('SELECT id FROM engagements WHERE quote_id = ?')
      .bind(QUOTE_ID)
      .first<{ id: string }>()

    expect(engagement).not.toBeNull()

    const milestones = await db
      .prepare('SELECT engagement_id FROM milestones')
      .all<{ engagement_id: string }>()

    for (const m of milestones.results) {
      expect(m.engagement_id).toBe(engagement!.id)
    }
  })

  it('is idempotent — processing the same webhook twice does not duplicate milestones', async () => {
    const r2 = createFakeR2()
    const payload = makePayload()

    const res1 = await handleDocumentCompleted(
      db,
      r2,
      'fake-api-key',
      undefined,
      undefined,
      payload
    )
    expect(res1.status).toBe(200)

    const res2 = await handleDocumentCompleted(
      db,
      r2,
      'fake-api-key',
      undefined,
      undefined,
      payload
    )
    expect(res2.status).toBe(200)

    const milestones = await db.prepare('SELECT * FROM milestones').all<Record<string, unknown>>()

    expect(milestones.results).toHaveLength(4)

    const engagements = await db.prepare('SELECT * FROM engagements').all<Record<string, unknown>>()

    expect(engagements.results).toHaveLength(1)
  })

  it('creates all records atomically in a single batch', async () => {
    const r2 = createFakeR2()
    await handleDocumentCompleted(db, r2, 'fake-api-key', undefined, undefined, makePayload())

    const quote = await db
      .prepare('SELECT status FROM quotes WHERE id = ?')
      .bind(QUOTE_ID)
      .first<{ status: string }>()
    expect(quote?.status).toBe('accepted')

    const entity = await db
      .prepare('SELECT stage FROM entities WHERE id = ?')
      .bind(ENTITY_ID)
      .first<{ stage: string }>()
    expect(entity?.stage).toBe('engaged')

    const engagements = await db
      .prepare('SELECT * FROM engagements WHERE quote_id = ?')
      .bind(QUOTE_ID)
      .all()
    expect(engagements.results).toHaveLength(1)

    const invoices = await db.prepare("SELECT * FROM invoices WHERE type = 'deposit'").all()
    expect(invoices.results).toHaveLength(1)

    const milestones = await db.prepare('SELECT * FROM milestones').all()
    expect(milestones.results).toHaveLength(4)

    const context = await db.prepare("SELECT * FROM context WHERE type = 'stage_change'").all()
    expect(context.results).toHaveLength(1)
  })
})
