/**
 * Behavioral coverage for SOW outbox job handlers in
 * src/lib/sow/service-finalize.ts — the 2026-06-30 code-review PR 3 fixes:
 *
 *   3a (C2, content policy): the deposit-invoice description must come from the
 *       quote's AUTHORED engagement_overview, never the old hardcoded scope
 *       phrase; neutral 'Deposit invoice' fallback when unauthored.
 *   3b (H): a Resend failure in the SOW-signed / portal-invitation email jobs
 *       must THROW (so the outbox marks the job failed + retries) rather than be
 *       silently discarded and marked completed.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import path from 'node:path'

// Mock Stripe + Resend so nothing hits the network and args are capturable.
const createStripeInvoiceMock = vi.fn(
  async (_apiKey?: unknown, _params?: { description: string; [k: string]: unknown }) => ({
    id: 'in_test',
    hosted_invoice_url: 'https://stripe.test/inv',
    status: 'open',
  })
)
const sendStripeInvoiceMock = vi.fn(async (_apiKey?: unknown, _id?: unknown) => ({
  id: 'in_test',
  hosted_invoice_url: 'https://stripe.test/inv',
  status: 'open',
}))
vi.mock('../src/lib/stripe/client', () => ({
  createStripeInvoice: (...args: unknown[]) =>
    createStripeInvoiceMock(...(args as Parameters<typeof createStripeInvoiceMock>)),
  sendStripeInvoice: (...args: unknown[]) =>
    sendStripeInvoiceMock(...(args as Parameters<typeof sendStripeInvoiceMock>)),
}))

const sendEmailMock = vi.fn()
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: (...args: unknown[]) => sendEmailMock(...args),
}))

import {
  handleDepositInvoiceJob,
  handleSignedEmailJob,
  handlePortalInvitationJob,
} from '../src/lib/sow/outbox-jobs'
import type { OutboxJob } from '../src/lib/sow/store'
import { createContact } from '../src/lib/db/contacts'

installWorkerdPolyfills()
const migrationsDir = path.resolve(__dirname, '../migrations')

const ORG_ID = 'org-sow-001'
const ENTITY_ID = 'entity-sow-001'
const ASSESSMENT_ID = 'assess-sow-001'

function job(payload: Record<string, unknown>): OutboxJob {
  return { payload_json: JSON.stringify(payload) } as unknown as OutboxJob
}

async function seedQuote(
  db: D1Database,
  quoteId: string,
  engagementOverview: string | null
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, version, line_items, total_hours, rate, total_price, deposit_pct, deposit_amount, status, engagement_overview, created_at, updated_at)
       VALUES (?, ?, ?, ?, 1, '[]', 10, 150, 1500, 0.5, 750, 'accepted', ?, datetime('now'), datetime('now'))`
    )
    .bind(quoteId, ORG_ID, ENTITY_ID, ASSESSMENT_ID, engagementOverview)
    .run()
}

let db: D1Database

beforeEach(async () => {
  db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Org', 'org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'Biz', 'biz', 'engaged', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_ID, ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO assessments (id, org_id, entity_id, status, created_at)
       VALUES (?, ?, ?, 'completed', datetime('now'))`
    )
    .bind(ASSESSMENT_ID, ORG_ID, ENTITY_ID)
    .run()
  await createContact(db, ORG_ID, ENTITY_ID, {
    name: 'Owner',
    email: 'owner@biz.test',
    title: 'Owner',
  })
  vi.clearAllMocks()
})

describe('handleDepositInvoiceJob — invoice description sourcing (3a)', () => {
  it('uses the authored engagement_overview as the Stripe invoice description', async () => {
    await seedQuote(db, 'quote-authored', 'Fractional ops support for Q3 hiring ramp')
    await handleDepositInvoiceJob(
      db,
      ORG_ID,
      'sk-test',
      job({
        entity_id: ENTITY_ID,
        quote_id: 'quote-authored',
        engagement_id: 'eng-1',
        invoice_id: 'inv-1',
        amount: 750,
      })
    )
    expect(createStripeInvoiceMock).toHaveBeenCalledTimes(1)
    const params = createStripeInvoiceMock.mock.calls[0][1] as { description: string }
    expect(params.description).toBe('Fractional ops support for Q3 hiring ramp')
  })

  it('falls back to a neutral "Deposit invoice" label when engagement_overview is null', async () => {
    await seedQuote(db, 'quote-blank', null)
    await handleDepositInvoiceJob(
      db,
      ORG_ID,
      'sk-test',
      job({
        entity_id: ENTITY_ID,
        quote_id: 'quote-blank',
        engagement_id: 'eng-1',
        invoice_id: 'inv-1',
        amount: 750,
      })
    )
    const params = createStripeInvoiceMock.mock.calls[0][1] as { description: string }
    expect(params.description).toBe('Deposit invoice')
    // Never the old fabricated scope phrase.
    expect(params.description).not.toContain('Operations Cleanup')
  })
})

describe('SOW outbox email jobs — fail loud on Resend rejection (3b)', () => {
  it('handleSignedEmailJob throws when the email send fails', async () => {
    sendEmailMock.mockResolvedValue({ success: false, error: 'suppressed' })
    await expect(
      handleSignedEmailJob(db, ORG_ID, 're-key', job({ entity_id: ENTITY_ID }))
    ).rejects.toThrow(/SOW-signed email send failed/)
  })

  it('handleSignedEmailJob resolves when the email send succeeds', async () => {
    sendEmailMock.mockResolvedValue({ success: true, id: 'e1' })
    await expect(
      handleSignedEmailJob(db, ORG_ID, 're-key', job({ entity_id: ENTITY_ID }))
    ).resolves.toBeUndefined()
  })

  it('handlePortalInvitationJob throws when the email send fails', async () => {
    sendEmailMock.mockResolvedValue({ success: false, error: 'bounced' })
    await expect(
      handlePortalInvitationJob(
        db,
        ORG_ID,
        're-key',
        'https://smd.services',
        job({ user_email: 'owner@biz.test', user_name: 'Owner' })
      )
    ).rejects.toThrow(/Portal-invitation email send failed/)
  })
})
