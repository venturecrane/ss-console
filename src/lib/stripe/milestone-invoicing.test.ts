/**
 * Tests for completeMilestoneWithInvoicing().
 *
 * Uses vitest mocks to stub D1Database, Stripe client, and context DAL.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { completeMilestoneWithInvoicing } from './milestone-invoicing'

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('../db/invoices', () => ({
  createInvoice: vi.fn(),
  updateInvoice: vi.fn(),
  updateInvoiceStatus: vi.fn(),
}))

vi.mock('../db/context', () => ({
  appendContext: vi.fn(),
}))

vi.mock('./client', () => ({
  createStripeInvoice: vi.fn(),
  sendStripeInvoice: vi.fn(),
}))

import { createInvoice, updateInvoice, updateInvoiceStatus } from '../db/invoices'
import { appendContext } from '../db/context'
import { createStripeInvoice, sendStripeInvoice } from './client'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ORG_ID = 'org-001'
const ENGAGEMENT_ID = 'eng-001'
const ENTITY_ID = 'ent-001'
const QUOTE_ID = 'quote-001'
const MILESTONE_ID = 'ms-001'
const INVOICE_ID = 'inv-001'

function makeMilestone(overrides: Record<string, unknown> = {}) {
  return {
    id: MILESTONE_ID,
    engagement_id: ENGAGEMENT_ID,
    name: 'Phase 1',
    description: null,
    due_date: null,
    completed_at: null,
    status: 'in_progress',
    payment_trigger: 1,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeCompletedMilestone(overrides: Record<string, unknown> = {}) {
  return {
    ...makeMilestone(overrides),
    status: 'completed',
    completed_at: new Date().toISOString(),
  }
}

function makeInvoice(overrides: Record<string, unknown> = {}) {
  return {
    id: INVOICE_ID,
    org_id: ORG_ID,
    engagement_id: ENGAGEMENT_ID,
    entity_id: ENTITY_ID,
    type: 'milestone',
    amount: 1500,
    description: 'Milestone invoice — Phase 1',
    status: 'draft',
    stripe_invoice_id: null,
    stripe_hosted_url: null,
    due_date: null,
    sent_at: null,
    paid_at: null,
    payment_method: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/**
 * Build a mock D1Database with SQL-snippet-based routing.
 */
function standardQueryResults(overrides: Record<string, unknown> = {}) {
  const completedMilestone = makeCompletedMilestone()
  return {
    'SELECT * FROM engagements WHERE id': overrides['engagement'] ?? {
      id: ENGAGEMENT_ID,
      entity_id: ENTITY_ID,
      quote_id: QUOTE_ID,
      org_id: ORG_ID,
    },
    'SELECT * FROM quotes WHERE id': overrides['quote'] ?? {
      total_price: 3000,
      rate: 150,
      line_items: JSON.stringify([
        { problem: 'Phase 1', description: 'Phase 1 work', estimated_hours: 10 },
        { problem: 'Phase 2', description: 'Phase 2 work', estimated_hours: 10 },
      ]),
    },
    'SELECT * FROM milestones WHERE engagement_id': overrides['allMilestones'] ?? [
      { ...completedMilestone, sort_order: 0 },
      { ...makeMilestone({ id: 'ms-002', sort_order: 1, name: 'Phase 2' }), sort_order: 1 },
    ],
    'COALESCE(SUM(amount), 0)': overrides['paidSum'] ?? { total: 0 },
    'SELECT * FROM invoices WHERE id': overrides['finalInvoice'] ?? makeInvoice(),
  }
}

function buildMockDb(qr: Record<string, unknown>, milestoneSequence: Record<string, unknown>[]) {
  let milestoneReadCount = 0
  return {
    prepare: vi.fn().mockImplementation((sql: string) => {
      return {
        bind: vi.fn().mockReturnValue({
          first: vi.fn().mockImplementation(async () => {
            if (sql.includes('SELECT * FROM milestones WHERE id')) {
              const result =
                milestoneSequence[milestoneReadCount] ??
                milestoneSequence[milestoneSequence.length - 1]
              milestoneReadCount++
              return result
            }
            for (const [snippet, result] of Object.entries(qr)) {
              if (sql.includes(snippet)) return result
            }
            return null
          }),
          all: vi.fn().mockImplementation(async () => {
            for (const [snippet, result] of Object.entries(qr)) {
              if (sql.includes(snippet)) return { results: result }
            }
            return { results: [] }
          }),
          run: vi.fn().mockResolvedValue({}),
        }),
      }
    }),
  } as unknown as D1Database
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(createInvoice).mockResolvedValue(makeInvoice())
  vi.mocked(updateInvoice).mockResolvedValue(makeInvoice())
  vi.mocked(updateInvoiceStatus).mockResolvedValue(makeInvoice({ status: 'sent' }))
  vi.mocked(appendContext).mockResolvedValue({
    id: 'ctx-001',
    entity_id: ENTITY_ID,
    org_id: ORG_ID,
    type: 'engagement_log',
    content: '',
    source: 'system',
    source_ref: null,
    content_size: 0,
    metadata: null,
    engagement_id: ENGAGEMENT_ID,
    created_at: '2026-01-01T00:00:00Z',
  })
  vi.mocked(createStripeInvoice).mockResolvedValue({
    id: 'in_stripe_001',
    hosted_invoice_url: 'https://pay.stripe.com/inv_001',
    status: 'open',
  })
  vi.mocked(sendStripeInvoice).mockResolvedValue({
    id: 'in_stripe_001',
    hosted_invoice_url: 'https://pay.stripe.com/inv_001',
    status: 'open',
  })
})

describe('completeMilestoneWithInvoicing', () => {
  it('creates invoice and calls Stripe when payment_trigger=true', async () => {
    const qr = standardQueryResults()
    const db = buildMockDb(qr, [
      makeMilestone(), // first read: in_progress (validation)
      makeCompletedMilestone(), // second read: completed (after UPDATE)
    ])

    const result = await completeMilestoneWithInvoicing({
      db,
      orgId: ORG_ID,
      milestoneId: MILESTONE_ID,
      stripeApiKey: 'sk_test_123',
      customerEmail: 'client@example.com',
    })

    expect(result.milestone.status).toBe('completed')
    expect(result.invoice).not.toBeNull()

    expect(createInvoice).toHaveBeenCalledWith(
      db,
      ORG_ID,
      expect.objectContaining({
        entity_id: ENTITY_ID,
        engagement_id: ENGAGEMENT_ID,
        type: 'milestone',
      })
    )

    expect(createStripeInvoice).toHaveBeenCalledWith(
      'sk_test_123',
      expect.objectContaining({ customer_email: 'client@example.com' })
    )
    expect(sendStripeInvoice).toHaveBeenCalledWith('sk_test_123', 'in_stripe_001')

    expect(appendContext).toHaveBeenCalledWith(
      db,
      ORG_ID,
      expect.objectContaining({
        type: 'engagement_log',
        source: 'system',
      })
    )
  })

  it('does not create invoice when payment_trigger=false', async () => {
    const db = buildMockDb({}, [
      makeMilestone({ payment_trigger: 0 }),
      makeCompletedMilestone({ payment_trigger: 0 }),
    ])

    const result = await completeMilestoneWithInvoicing({
      db,
      orgId: ORG_ID,
      milestoneId: MILESTONE_ID,
      stripeApiKey: 'sk_test_123',
      customerEmail: 'client@example.com',
    })

    expect(result.milestone.status).toBe('completed')
    expect(result.invoice).toBeNull()
    expect(createInvoice).not.toHaveBeenCalled()
    expect(createStripeInvoice).not.toHaveBeenCalled()
  })

  it('calculates completion invoice as remaining balance', async () => {
    const singleMilestone = makeCompletedMilestone({ sort_order: 0 })
    const qr = standardQueryResults({
      allMilestones: [singleMilestone],
      paidSum: { total: 1500 },
      quote: {
        total_price: 3000,
        rate: 150,
        line_items: JSON.stringify([{ estimated_hours: 20 }]),
      },
    })

    const db = buildMockDb(qr, [makeMilestone({ sort_order: 0 }), singleMilestone])

    await completeMilestoneWithInvoicing({
      db,
      orgId: ORG_ID,
      milestoneId: MILESTONE_ID,
      stripeApiKey: 'sk_test_123',
      customerEmail: 'client@example.com',
    })

    // amount = 3000 - 1500 = 1500
    expect(createInvoice).toHaveBeenCalledWith(
      db,
      ORG_ID,
      expect.objectContaining({
        type: 'completion',
        amount: 1500,
      })
    )
  })

  it('calculates milestone invoice as pro-rata from line item hours', async () => {
    // First of two milestones; line item[0] has 10 hours at 150/hr = 1500
    const qr = standardQueryResults()
    const db = buildMockDb(qr, [
      makeMilestone({ sort_order: 0 }),
      makeCompletedMilestone({ sort_order: 0 }),
    ])

    await completeMilestoneWithInvoicing({
      db,
      orgId: ORG_ID,
      milestoneId: MILESTONE_ID,
      stripeApiKey: 'sk_test_123',
      customerEmail: 'client@example.com',
    })

    // 10 hours * 150/hr = 1500
    expect(createInvoice).toHaveBeenCalledWith(
      db,
      ORG_ID,
      expect.objectContaining({
        type: 'milestone',
        amount: 1500,
      })
    )
  })

  it('leaves invoice at draft when STRIPE_API_KEY is missing', async () => {
    const qr = standardQueryResults()
    const db = buildMockDb(qr, [makeMilestone(), makeCompletedMilestone()])

    const result = await completeMilestoneWithInvoicing({
      db,
      orgId: ORG_ID,
      milestoneId: MILESTONE_ID,
      stripeApiKey: undefined,
      customerEmail: 'client@example.com',
    })

    expect(createInvoice).toHaveBeenCalled()
    expect(createStripeInvoice).not.toHaveBeenCalled()
    expect(sendStripeInvoice).not.toHaveBeenCalled()
    expect(updateInvoiceStatus).not.toHaveBeenCalled()
    expect(result.invoice).not.toBeNull()
    expect(result.invoice!.status).toBe('draft')
  })
})
