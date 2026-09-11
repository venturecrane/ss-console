/**
 * Behavioral tests for the Stripe webhook money path — real D1 via the
 * crane-test-harness with the full migration set applied.
 *
 * Replaces the source-mirror describe blocks 'invoices: stripe webhook
 * handler' and 'invoices: stripe webhook route' that previously lived in
 * tests/invoices.test.ts. Signature verification itself is covered
 * behaviorally in tests/webhooks/stripe-verify.test.ts; this file covers
 * the post-verify business logic plus the route's event dispatch.
 *
 * Covers:
 * - handleInvoicePaid Phase 1 two-phase batch: invoice -> paid, and the
 *   deposit-only engagement activation (scheduled -> active)
 * - non-deposit invoices never touch the engagement
 * - idempotency (replayed webhook does not double-apply)
 * - unknown Stripe invoice id acks 200 without writes
 * - Phase 2 best-effort confirmation email (sent, skipped, and failing)
 * - handleInvoicePaymentFailed leaves the invoice as 'sent'
 * - POST /api/webhooks/stripe dispatches signed invoice.paid /
 *   invoice.payment_failed events into the handlers against env.DB
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import path from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import { handleInvoicePaid, handleInvoicePaymentFailed } from './stripe-handler'
import { POST } from '../../pages/api/webhooks/stripe'
import type { StripeInvoice, StripeWebhookEvent } from '../stripe/types'
import { sendEmail } from '../email/resend'

vi.mock('../email/resend', () => ({
  sendEmail: vi.fn().mockResolvedValue({ success: true, id: 'test-email-id' }),
}))

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../../../migrations')

const ORG_ID = 'org-stripe-test'
const ENTITY_ID = 'entity-stripe-test'
const ASSESSMENT_ID = 'assessment-stripe-test'
const QUOTE_ID = 'quote-stripe-test'
const ENGAGEMENT_ID = 'engagement-stripe-test'
const DEPOSIT_INVOICE_ID = 'invoice-deposit-test'
const COMPLETION_INVOICE_ID = 'invoice-completion-test'
const STRIPE_DEPOSIT_ID = 'in_stripe_deposit_001'
const STRIPE_COMPLETION_ID = 'in_stripe_completion_001'
const CONTACT_EMAIL = 'owner@stripe-test.com'

function makeStripeInvoiceObject(id: string): StripeInvoice {
  return {
    id,
    object: 'invoice',
    status: 'paid',
    amount_due: 350000,
    amount_paid: 350000,
    currency: 'usd',
    customer: 'cus_test_001',
    customer_email: CONTACT_EMAIL,
    description: null,
    hosted_invoice_url: `https://invoice.stripe.com/i/${id}`,
    invoice_pdf: null,
    collection_method: 'send_invoice',
    status_transitions: {
      paid_at: Math.floor(Date.now() / 1000),
      finalized_at: Math.floor(Date.now() / 1000),
      voided_at: null,
    },
    metadata: {},
    created: Math.floor(Date.now() / 1000),
    due_date: null,
  }
}

function makeEvent(stripeInvoiceId: string, type = 'invoice.paid'): StripeWebhookEvent {
  return {
    id: 'evt_test_001',
    object: 'event',
    type,
    data: { object: makeStripeInvoiceObject(stripeInvoiceId) },
    created: Math.floor(Date.now() / 1000),
  }
}

async function getInvoiceRow(db: D1Database, id: string) {
  return db
    .prepare('SELECT status, paid_at, payment_method, updated_at FROM invoices WHERE id = ?')
    .bind(id)
    .first<{
      status: string
      paid_at: string | null
      payment_method: string | null
      updated_at: string
    }>()
}

async function getEngagementRow(db: D1Database) {
  return db
    .prepare('SELECT status, start_date FROM engagements WHERE id = ?')
    .bind(ENGAGEMENT_ID)
    .first<{ status: string; start_date: string | null }>()
}

let db: D1Database

beforeAll(() => {
  const files = discoverNumericMigrations(migrationsDir)
  expect(files.length).toBeGreaterThan(0)
})

beforeEach(async () => {
  vi.mocked(sendEmail).mockClear()
  vi.mocked(sendEmail).mockResolvedValue({ success: true, id: 'test-email-id' })
  vi.spyOn(console, 'log').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})

  db = createTestD1()
  const files = discoverNumericMigrations(migrationsDir)
  await runMigrations(db, { files })

  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Test Org', 'stripe-test-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()

  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'Stripe Test Business', 'stripe-test-business', 'engaged', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_ID, ORG_ID)
    .run()

  await db
    .prepare(
      `INSERT INTO contacts (id, org_id, entity_id, name, email, created_at)
       VALUES ('contact-stripe-test', ?, ?, 'Test Owner', ?, datetime('now'))`
    )
    .bind(ORG_ID, ENTITY_ID, CONTACT_EMAIL)
    .run()

  await db
    .prepare(
      `INSERT INTO assessments (id, org_id, entity_id, status, created_at)
       VALUES (?, ?, ?, 'completed', datetime('now'))`
    )
    .bind(ASSESSMENT_ID, ORG_ID, ENTITY_ID)
    .run()

  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, version, line_items, total_hours, rate, total_price, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, 1, '[]', 40, 175, 7000, 'accepted', datetime('now'), datetime('now'))`
    )
    .bind(QUOTE_ID, ORG_ID, ENTITY_ID, ASSESSMENT_ID)
    .run()

  await db
    .prepare(
      `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'scheduled', datetime('now'), datetime('now'))`
    )
    .bind(ENGAGEMENT_ID, ORG_ID, ENTITY_ID, QUOTE_ID)
    .run()

  // Deposit invoice in 'sent' linked to the engagement, plus a non-deposit
  // completion invoice — both already pushed to Stripe.
  await db
    .prepare(
      `INSERT INTO invoices (id, org_id, entity_id, engagement_id, type, amount, status, stripe_invoice_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'deposit', 3500, 'sent', ?, datetime('now'), datetime('now'))`
    )
    .bind(DEPOSIT_INVOICE_ID, ORG_ID, ENTITY_ID, ENGAGEMENT_ID, STRIPE_DEPOSIT_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO invoices (id, org_id, entity_id, engagement_id, type, amount, status, stripe_invoice_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'completion', 3500, 'sent', ?, datetime('now'), datetime('now'))`
    )
    .bind(COMPLETION_INVOICE_ID, ORG_ID, ENTITY_ID, ENGAGEMENT_ID, STRIPE_COMPLETION_ID)
    .run()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// handleInvoicePaid — Phase 1 batch
// ---------------------------------------------------------------------------

describe('handleInvoicePaid — deposit invoice', () => {
  it('marks the invoice paid with paid_at and payment_method stripe', async () => {
    const res = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))
    expect(res.status).toBe(200)

    const row = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(row?.status).toBe('paid')
    expect(row?.paid_at).toBeTruthy()
    expect(row?.payment_method).toBe('stripe')
  })

  it('activates the scheduled engagement and stamps start_date', async () => {
    await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))

    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('active')
    expect(engagement?.start_date).toBeTruthy()
  })

  it('does not activate an engagement that is no longer scheduled (status guard)', async () => {
    // Document the WHERE status = 'scheduled' guard: a cancelled engagement
    // stays cancelled even when its deposit invoice is paid.
    await db
      .prepare(`UPDATE engagements SET status = 'cancelled' WHERE id = ?`)
      .bind(ENGAGEMENT_ID)
      .run()

    const res = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('cancelled')
    expect(engagement?.start_date).toBeNull()
  })

  it('skips the engagement statement when the deposit invoice has no engagement_id', async () => {
    await db
      .prepare('UPDATE invoices SET engagement_id = NULL WHERE id = ?')
      .bind(DEPOSIT_INVOICE_ID)
      .run()

    const res = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('scheduled')
  })
})

describe('handleInvoicePaid — non-deposit invoice', () => {
  it('marks the invoice paid without touching the engagement', async () => {
    const res = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_COMPLETION_ID))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, COMPLETION_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
    expect(invoice?.payment_method).toBe('stripe')

    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('scheduled')
    expect(engagement?.start_date).toBeNull()
  })
})

describe('handleInvoicePaid — idempotency', () => {
  it('a replayed webhook returns 200 without double-applying', async () => {
    const res1 = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))
    expect(res1.status).toBe(200)

    const afterFirst = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)

    const res2 = await handleInvoicePaid(db, undefined, makeEvent(STRIPE_DEPOSIT_ID))
    expect(res2.status).toBe(200)

    const afterSecond = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(afterSecond?.status).toBe('paid')
    expect(afterSecond?.paid_at).toBe(afterFirst?.paid_at)
    expect(afterSecond?.updated_at).toBe(afterFirst?.updated_at)

    // Phase 2 also runs only once — the replay short-circuits before email.
    // One run sends two: the client thank-you and the team@ alert.
    expect(vi.mocked(sendEmail)).toHaveBeenCalledTimes(2)
  })
})

describe('handleInvoicePaid — unknown Stripe invoice', () => {
  it('acks 200 with ok:true and writes nothing', async () => {
    const res = await handleInvoicePaid(db, undefined, makeEvent('in_unknown_999'))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true })

    const paid = await db
      .prepare(`SELECT COUNT(*) AS n FROM invoices WHERE status = 'paid'`)
      .first<{ n: number }>()
    expect(paid?.n).toBe(0)
    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('scheduled')
    expect(vi.mocked(sendEmail)).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// handleInvoicePaid — Phase 2 side effects
// ---------------------------------------------------------------------------

describe('handleInvoicePaid — confirmation email', () => {
  it('sends the payment confirmation to the primary contact with the formatted amount', async () => {
    await handleInvoicePaid(db, 'resend-key', makeEvent(STRIPE_DEPOSIT_ID))

    expect(vi.mocked(sendEmail)).toHaveBeenCalledTimes(2)
    const [apiKey, payload] = vi.mocked(sendEmail).mock.calls[0]
    expect(apiKey).toBe('resend-key')
    expect(payload.to).toBe(CONTACT_EMAIL)
    expect(payload.subject).toBe('Payment received — thank you')
    expect(payload.html).toContain('$3,500.00')
    expect(payload.html).toContain('Stripe Test Business')

    // The money is also announced to team@ — a paid invoice must never be
    // discovered later in the Stripe dashboard (#2737).
    const [, alert] = vi.mocked(sendEmail).mock.calls[1]
    expect(alert.to).toBe('team@smd.services')
    expect(alert.subject).toBe('Payment received — Stripe Test Business, $3,500.00')
    expect(alert.html).toContain(STRIPE_DEPOSIT_ID)
    expect(alert.html).toContain('$3,500.00')
  })

  it('skips the client email when the entity has no contact, but still alerts team@', async () => {
    await db.prepare('DELETE FROM contacts WHERE entity_id = ?').bind(ENTITY_ID).run()

    const res = await handleInvoicePaid(db, 'resend-key', makeEvent(STRIPE_DEPOSIT_ID))
    expect(res.status).toBe(200)

    // The thank-you is conditional on a contact address; the team@ alert is
    // not. A missing client contact must not silence the money signal.
    expect(vi.mocked(sendEmail)).toHaveBeenCalledTimes(1)
    const [, alert] = vi.mocked(sendEmail).mock.calls[0]
    expect(alert.to).toBe('team@smd.services')
    expect(alert.subject).toContain('Payment received —')
    expect(alert.html).toContain('$3,500.00')

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
  })

  it('still returns 200 with the invoice paid when the email send throws (best-effort Phase 2)', async () => {
    vi.mocked(sendEmail).mockRejectedValueOnce(new Error('resend down'))

    const res = await handleInvoicePaid(db, 'resend-key', makeEvent(STRIPE_DEPOSIT_ID))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
  })
})

// ---------------------------------------------------------------------------
// handleInvoicePaymentFailed
// ---------------------------------------------------------------------------

describe('handleInvoicePaymentFailed', () => {
  it('logs the failure and leaves the invoice as sent', async () => {
    const res = await handleInvoicePaymentFailed(
      db,
      makeEvent(STRIPE_DEPOSIT_ID, 'invoice.payment_failed')
    )
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('sent')
    expect(invoice?.paid_at).toBeNull()
  })

  it('acks an unknown Stripe invoice with 200', async () => {
    const res = await handleInvoicePaymentFailed(
      db,
      makeEvent('in_unknown_999', 'invoice.payment_failed')
    )
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true })
  })
})

// ---------------------------------------------------------------------------
// Route dispatch — POST /api/webhooks/stripe with a signed payload
//
// Signature verification edge cases live in tests/webhooks/stripe-verify.test.ts.
// These two tests prove the dispatch wiring: a correctly signed business
// event flows through the route into the handler and mutates env.DB.
// ---------------------------------------------------------------------------

const WEBHOOK_SECRET = 'whsec_test_dispatch_secret'

async function signedStripeRequest(event: StripeWebhookEvent): Promise<Request> {
  const body = JSON.stringify(event)
  const timestamp = Math.floor(Date.now() / 1000)
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(WEBHOOK_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, enc.encode(`${timestamp}.${body}`))
  const signature = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

  return new Request('http://test.local/api/webhooks/stripe', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'stripe-signature': `t=${timestamp},v1=${signature}`,
    },
    body,
  })
}

function buildRouteContext(request: Request) {
  return {
    request,
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof POST>[0]
}

describe('POST /api/webhooks/stripe — event dispatch', () => {
  beforeEach(() => {
    Object.assign(testEnv, { STRIPE_WEBHOOK_SECRET: WEBHOOK_SECRET, DB: db })
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
  })

  it('a signed invoice.paid event marks the invoice paid through the route', async () => {
    const request = await signedStripeRequest(makeEvent(STRIPE_DEPOSIT_ID))
    const res = await POST(buildRouteContext(request))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('paid')
    const engagement = await getEngagementRow(db)
    expect(engagement?.status).toBe('active')
  })

  it('a signed invoice.payment_failed event leaves the invoice as sent through the route', async () => {
    const request = await signedStripeRequest(
      makeEvent(STRIPE_DEPOSIT_ID, 'invoice.payment_failed')
    )
    const res = await POST(buildRouteContext(request))
    expect(res.status).toBe(200)

    const invoice = await getInvoiceRow(db, DEPOSIT_INVOICE_ID)
    expect(invoice?.status).toBe('sent')
  })
})
