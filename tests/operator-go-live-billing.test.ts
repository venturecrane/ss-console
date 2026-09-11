/**
 * The Operator's commercial go-live, end to end at the data layer
 * (A&P production-client readiness, 2026-08-29).
 *
 * Three facts the portal's visibility rules depend on (offerings.ts):
 *   1. A `provisioning` operator row is promoted to `active` by the client's
 *      own checkout (operator-checkout-handler) and by nothing else (the
 *      generic status mirror keeps its provisioning guard). That flip is
 *      what reveals Home + Billing.
 *   2. The stand-up fee is an `implementation` invoice (migration 0110) that
 *      is born with its authored line item, so it can be presented at once.
 *   3. The checkout-completed handler binds, promotes, and is idempotent.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

// The failed-first-payment path cancels the subscription at Stripe and
// alerts team@. Both leave the process; both are observed here as calls.
const cancelOperatorSubscription = vi.fn()
const sendEmail = vi.fn()
vi.mock('../src/lib/stripe/subscriptions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/lib/stripe/subscriptions')>()),
  cancelOperatorSubscription: (...args: unknown[]) => cancelOperatorSubscription(...args),
}))
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: (...args: unknown[]) => sendEmail(...args),
}))

import {
  activateOperatorSubscriptionForBilling,
  getSubscriptionForBilling,
  setSubscriptionBillingStatus,
} from '../src/lib/db/subscriptions'
import { handleRetainerInvoicePaid } from '../src/lib/webhooks/stripe-subscription-handler'
import {
  CARD_FEE_LINE_DESCRIPTION,
  cardProcessingFeeCents,
  createInvoice,
  invoiceIsCardPayable,
  invoiceTypeLabel,
  isInvoiceType,
  listLineItemsForInvoice,
  updateInvoiceStatus,
} from '../src/lib/db/invoices'
import {
  handleOperatorCheckoutAsyncPaymentFailed,
  handleOperatorCheckoutCompleted,
  type OperatorCheckoutSessionPayload,
} from '../src/lib/webhooks/operator-checkout-handler'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG = 'org-a'
const ENTITY = 'entity-a'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(ORG, 'Org A', 'org-a')
    .run()
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(ENTITY, ORG, 'Entity A', 'entity-a')
    .run()
}

async function insertSubscription(
  db: D1Database,
  id: string,
  productSlug: string,
  status: string,
  instanceSlug = 'seat-a'
): Promise<void> {
  await db
    .prepare(
      'INSERT INTO subscriptions (id, org_id, entity_id, product_slug, instance_slug, status) VALUES (?, ?, ?, ?, ?, ?)'
    )
    .bind(id, ORG, ENTITY, productSlug, productSlug === 'operator' ? instanceSlug : null, status)
    .run()
}

describe('go-live: the promotion primitive (offerings.ts hasBillingRelationship)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('promotes a provisioning operator row to active', async () => {
    await insertSubscription(db, 'sub-op', 'operator', 'provisioning')
    expect(await activateOperatorSubscriptionForBilling(db, 'sub-op')).toBe(true)
    const row = await getSubscriptionForBilling(db, ENTITY, 'operator')
    expect(row?.status).toBe('active')
  })

  it('is a no-op on rows that are not provisioning (never resurrects cancelled, never re-flips)', async () => {
    await insertSubscription(db, 'sub-cancel', 'operator', 'cancelled')
    expect(await activateOperatorSubscriptionForBilling(db, 'sub-cancel')).toBe(false)
    expect((await getSubscriptionForBilling(db, ENTITY, 'operator'))?.status).toBe('cancelled')
  })

  it('never touches a non-operator product (the Hosted Agent has its own activation flow)', async () => {
    await insertSubscription(db, 'sub-ha', 'hosted-agent', 'provisioning')
    expect(await activateOperatorSubscriptionForBilling(db, 'sub-ha')).toBe(false)
    expect((await getSubscriptionForBilling(db, ENTITY, 'hosted-agent'))?.status).toBe(
      'provisioning'
    )
  })

  it('the webhook mirror still cannot promote a provisioning row (guard intact)', async () => {
    await insertSubscription(db, 'sub-op', 'operator', 'provisioning')
    await setSubscriptionBillingStatus(db, 'sub-op', 'active')
    expect((await getSubscriptionForBilling(db, ENTITY, 'operator'))?.status).toBe('provisioning')
  })
})

describe('the stand-up fee is an implementation invoice, born presentable (migration 0110)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('implementation is in the vocabulary with a client-readable label', () => {
    expect(isInvoiceType('implementation')).toBe(true)
    expect(invoiceTypeLabel('implementation')).toBe('Implementation')
    expect(isInvoiceType('surcharge')).toBe(false)
    expect(invoiceTypeLabel('surcharge')).toBeNull()
  })

  it('creates the invoice with its authored line and passes the send-gate', async () => {
    const invoice = await createInvoice(db, ORG, {
      entity_id: ENTITY,
      type: 'implementation',
      amount: 4000,
      description: 'Operator implementation',
      line_items: [{ description: 'Operator implementation and stand-up', amount_cents: 400000 }],
    })
    expect(invoice.type).toBe('implementation')
    const lines = await listLineItemsForInvoice(db, invoice.id)
    expect(lines).toHaveLength(1)
    expect(lines[0].amount_cents).toBe(400000)

    const sent = await updateInvoiceStatus(db, ORG, invoice.id, 'sent')
    expect(sent?.status).toBe('sent')
  })

  it('a line-less invoice is still unsendable (gate unchanged)', async () => {
    const invoice = await createInvoice(db, ORG, {
      entity_id: ENTITY,
      type: 'implementation',
      amount: 4000,
    })
    await expect(updateInvoiceStatus(db, ORG, invoice.id, 'sent')).rejects.toThrow(
      /missing authored line items/
    )
  })

  it('line items still cascade with their invoice after the table rebuild', async () => {
    const invoice = await createInvoice(db, ORG, {
      entity_id: ENTITY,
      type: 'deposit',
      amount: 10,
      line_items: [{ description: 'x', amount_cents: 1000 }],
    })
    await db.prepare('DELETE FROM invoices WHERE id = ?').bind(invoice.id).run()
    expect(await listLineItemsForInvoice(db, invoice.id)).toHaveLength(0)
  })
})

describe('card processing fee (agreement §3.8)', () => {
  it('is 3% of the amount paid by card, rounded to the cent', () => {
    expect(cardProcessingFeeCents(400000)).toBe(12000)
    expect(cardProcessingFeeCents(500000)).toBe(15000)
    expect(cardProcessingFeeCents(1)).toBe(0)
  })

  it('an invoice is card-payable only when it carries the fee line', () => {
    expect(invoiceIsCardPayable([{ description: 'Operator implementation' }])).toBe(false)
    expect(
      invoiceIsCardPayable([
        { description: 'Operator implementation' },
        { description: CARD_FEE_LINE_DESCRIPTION },
      ])
    ).toBe(true)
  })
})

interface SubState {
  status: string
  stripe_subscription_id: string | null
  settings_json: string | null
  started_at: string | null
}

async function subState(db: D1Database, id = 'sub-op'): Promise<SubState> {
  const row = await db
    .prepare(
      'SELECT status, stripe_subscription_id, settings_json, started_at FROM subscriptions WHERE id = ?'
    )
    .bind(id)
    .first<SubState>()
  if (!row) throw new Error(`no subscription row ${id}`)
  return row
}

async function orderStatus(db: D1Database, sessionId = 'cs_test_1'): Promise<string | null> {
  const row = await db
    .prepare('SELECT status FROM stripe_checkout_orders WHERE session_id = ?')
    .bind(sessionId)
    .first<{ status: string }>()
  return row?.status ?? null
}

/** The start gates (portal `canStart`, server start-subscription route) both
 * require exactly this: provisioning and no Stripe subscription attached. */
function startable(row: SubState): boolean {
  return row.status === 'provisioning' && row.stripe_subscription_id === null
}

const payload = (
  over: Partial<OperatorCheckoutSessionPayload> = {}
): OperatorCheckoutSessionPayload => ({
  id: 'cs_test_1',
  client_reference_id: 'user-1',
  customer: 'cus_1',
  subscription: 'sub_stripe_1',
  amount_total: 500000,
  customer_details: { email: 'admin@firm.com', name: 'Admin' },
  metadata: { product_slug: 'operator', smd_entity_id: ENTITY, smd_subscription_id: 'sub-op' },
  payment_status: 'paid',
  ...over,
})

/** A first cycle invoice in the live (post-basil) shape: the subscription
 * metadata the checkout stamped rides at parent.subscription_details. */
function firstInvoice(stripeSubscriptionId = 'sub_stripe_1', rowId = 'sub-op') {
  return {
    id: 'in_first_1',
    amount_due: 500000,
    amount_paid: 500000,
    hosted_invoice_url: 'https://invoice.stripe.com/i/first',
    due_date: null,
    status_transitions: { paid_at: 1785100000 },
    customer: 'cus_1',
    parent: {
      type: 'subscription_details',
      subscription_details: {
        subscription: stripeSubscriptionId,
        metadata: {
          product_slug: 'operator',
          smd_entity_id: ENTITY,
          smd_subscription_id: rowId,
        },
      },
    },
  }
}

describe("operator checkout.session.completed: the client's payment is the go-live act", () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    await insertSubscription(db, 'sub-op', 'operator', 'provisioning')
  })

  it('binds the Stripe subscription + customer and promotes the row to active', async () => {
    const res = await handleOperatorCheckoutCompleted(db, payload())
    expect(res.status).toBe(200)
    const row = await db
      .prepare(
        'SELECT status, stripe_subscription_id, settings_json FROM subscriptions WHERE id = ?'
      )
      .bind('sub-op')
      .first<{ status: string; stripe_subscription_id: string; settings_json: string }>()
    expect(row?.status).toBe('active')
    expect(row?.stripe_subscription_id).toBe('sub_stripe_1')
    expect(JSON.parse(row!.settings_json)).toEqual({ stripe_customer_id: 'cus_1' })
    const order = await db
      .prepare('SELECT status, product_slug FROM stripe_checkout_orders WHERE session_id = ?')
      .bind('cs_test_1')
      .first<{ status: string; product_slug: string }>()
    expect(order).toEqual({ status: 'processed', product_slug: 'operator' })
  })

  it('is idempotent on replay', async () => {
    await handleOperatorCheckoutCompleted(db, payload())
    const res = await handleOperatorCheckoutCompleted(db, payload({ subscription: 'sub_other' }))
    expect(res.status).toBe(200)
    const row = await db
      .prepare('SELECT stripe_subscription_id FROM subscriptions WHERE id = ?')
      .bind('sub-op')
      .first<{ stripe_subscription_id: string }>()
    expect(row?.stripe_subscription_id).toBe('sub_stripe_1')
  })

  it('acks a session for another product without touching anything', async () => {
    const res = await handleOperatorCheckoutCompleted(
      db,
      payload({ metadata: { product_slug: 'hosted-agent' } })
    )
    expect(res.status).toBe(200)
    const row = await db
      .prepare('SELECT status FROM subscriptions WHERE id = ?')
      .bind('sub-op')
      .first<{ status: string }>()
    expect(row?.status).toBe('provisioning')
  })

  it('records a session naming an unknown row as failed (200, no invention)', async () => {
    const res = await handleOperatorCheckoutCompleted(
      db,
      payload({ metadata: { product_slug: 'operator', smd_subscription_id: 'sub-nope' } })
    )
    expect(res.status).toBe(200)
    const order = await db
      .prepare('SELECT status FROM stripe_checkout_orders WHERE session_id = ?')
      .bind('cs_test_1')
      .first<{ status: string }>()
    expect(order?.status).toBe('failed')
    const row = await db
      .prepare('SELECT status FROM subscriptions WHERE id = ?')
      .bind('sub-op')
      .first<{ status: string }>()
    expect(row?.status).toBe('provisioning')
  })

  it('an unbound failure is NOT terminal: after a human fixes the binding, a replay re-runs the pipeline (0087)', async () => {
    // The session names a row that does not exist yet.
    const unbound = payload({
      metadata: { product_slug: 'operator', smd_subscription_id: 'sub-late' },
    })
    await handleOperatorCheckoutCompleted(db, unbound)
    expect(await orderStatus(db)).toBe('failed')
    // The human reconciles: the row the session named now exists.
    await insertSubscription(db, 'sub-late', 'operator', 'provisioning', 'seat-b')
    // Stripe re-delivers (or the human resends from the dashboard).
    const res = await handleOperatorCheckoutCompleted(db, unbound)
    expect(res.status).toBe(200)
    const row = await subState(db, 'sub-late')
    expect(row.status).toBe('active')
    expect(row.stripe_subscription_id).toBe('sub_stripe_1')
    expect(await orderStatus(db)).toBe('processed')
  })
})

// ---------------------------------------------------------------------------
// A1: invoice.paid can land BEFORE checkout.session.completed
//
// Stripe does not order the two. Before the fallback, the first paid invoice
// found no row carrying the Stripe subscription id, logged "no local
// subscription", and returned 200: money in, portal still provisioning,
// nothing anywhere saying so.
// ---------------------------------------------------------------------------

describe('go-live ordering: invoice.paid before checkout.session.completed (A1)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    await insertSubscription(db, 'sub-op', 'operator', 'provisioning')
    sendEmail.mockReset()
    cancelOperatorSubscription.mockReset()
  })

  it('binds + promotes from the invoice metadata, mirrors the invoice, and the later checkout is a no-op', async () => {
    const res = await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', firstInvoice())
    expect(res.status).toBe(200)

    const afterInvoice = await subState(db)
    expect(afterInvoice.status).toBe('active')
    expect(afterInvoice.stripe_subscription_id).toBe('sub_stripe_1')
    expect(JSON.parse(afterInvoice.settings_json!)).toEqual({ stripe_customer_id: 'cus_1' })
    expect(afterInvoice.started_at).not.toBeNull()

    const invoice = await db
      .prepare('SELECT type, status, amount, entity_id FROM invoices WHERE stripe_invoice_id = ?')
      .bind('in_first_1')
      .first<{ type: string; status: string; amount: number; entity_id: string }>()
    expect(invoice).toEqual({ type: 'retainer', status: 'paid', amount: 5000, entity_id: ENTITY })

    // The checkout event arrives late: same binding, nothing changes.
    const later = await handleOperatorCheckoutCompleted(db, payload())
    expect(later.status).toBe(200)
    const afterCheckout = await subState(db)
    expect(afterCheckout).toEqual(afterInvoice)
    expect(await orderStatus(db)).toBe('processed')
  })

  it('an unattached row is bound from a late-arriving paid invoice exactly once (replay is idempotent)', async () => {
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', firstInvoice())
    const first = await subState(db)
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', firstInvoice())
    expect(await subState(db)).toEqual(first)
    const count = await db
      .prepare('SELECT COUNT(*) AS n FROM invoices WHERE stripe_invoice_id = ?')
      .bind('in_first_1')
      .first<{ n: number }>()
    expect(count?.n).toBe(1)
  })

  it('an invoice whose metadata names nothing this console holds is still an honest skip', async () => {
    const stray = firstInvoice('sub_stripe_smoke', 'sub-nope')
    const res = await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_smoke', stray)
    expect(res.status).toBe(200)
    expect(startable(await subState(db))).toBe(true)
    const count = await db.prepare('SELECT COUNT(*) AS n FROM invoices').first<{ n: number }>()
    expect(count?.n).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// A2: payment_status gates go-live; the async events finish or unwind it
//
// The checkout offers ACH. An ACH session completes with payment_status
// `unpaid` and settles (or bounces) days later. Promoting on `completed`
// alone would reveal the full portal to a client whose first payment then
// bounced, with a live Stripe subscription the console had forgotten.
// ---------------------------------------------------------------------------

describe('A2: delayed first payment (ACH) — bind on completed, go live on settlement', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    await insertSubscription(db, 'sub-op', 'operator', 'provisioning')
    sendEmail.mockReset()
    cancelOperatorSubscription.mockReset()
    cancelOperatorSubscription.mockResolvedValue({ id: 'sub_stripe_1', status: 'canceled' })
  })

  it('completed with payment_status=unpaid attaches but does NOT promote; the order stays received', async () => {
    const res = await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    expect(res.status).toBe(200)
    const row = await subState(db)
    expect(row.status).toBe('provisioning')
    expect(row.stripe_subscription_id).toBe('sub_stripe_1')
    expect(JSON.parse(row.settings_json!)).toEqual({ stripe_customer_id: 'cus_1' })
    expect(await orderStatus(db)).toBe('received')
  })

  it('no_payment_required promotes (Stripe: nothing is owed at this time)', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'no_payment_required' }))
    expect((await subState(db)).status).toBe('active')
    expect(await orderStatus(db)).toBe('processed')
  })

  it('async_payment_succeeded after an unpaid completion promotes the row and closes the order', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    // Stripe re-sends the session object, now paid.
    const res = await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'paid' }))
    expect(res.status).toBe(200)
    const row = await subState(db)
    expect(row.status).toBe('active')
    expect(row.stripe_subscription_id).toBe('sub_stripe_1')
    expect(await orderStatus(db)).toBe('processed')
  })

  it('invoice.paid on the attached-but-provisioning row goes live too; the later async_payment_succeeded is a no-op', async () => {
    // The two settlement signals converge: whichever Stripe delivers first
    // promotes, the other finds nothing left to do.
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', firstInvoice())
    const afterInvoice = await subState(db)
    expect(afterInvoice.status).toBe('active')
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'paid' }))
    expect(await subState(db)).toEqual(afterInvoice)
    expect(await orderStatus(db)).toBe('processed')
  })

  it('async_payment_failed cancels at Stripe, detaches, fails the order, alerts team@ — and the row is startable again', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    expect(startable(await subState(db))).toBe(false)

    const res = await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk_test_x',
      'resend_x',
      payload({ payment_status: 'unpaid' })
    )
    expect(res.status).toBe(200)

    const row = await subState(db)
    expect(row.status).toBe('provisioning')
    expect(row.stripe_subscription_id).toBeNull()
    // The Stripe customer is real and the retry reuses it.
    expect(JSON.parse(row.settings_json!)).toEqual({ stripe_customer_id: 'cus_1' })
    expect(startable(row)).toBe(true)
    expect(await orderStatus(db)).toBe('failed')

    expect(cancelOperatorSubscription).toHaveBeenCalledTimes(1)
    expect(cancelOperatorSubscription).toHaveBeenCalledWith('sk_test_x', 'sub_stripe_1')
    expect(sendEmail).toHaveBeenCalledTimes(1)
    const [, mail] = sendEmail.mock.calls[0] as [
      unknown,
      { to: string; subject: string; html: string },
    ]
    expect(mail.to).toBe('team@smd.services')
    expect(mail.subject).toContain('FAILED')
    expect(mail.html).toContain('sub_stripe_1')
    expect(mail.html).toContain('detached')
  })

  it('after a failed payment, a re-delivered completed event does not re-attach the dead subscription', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      undefined,
      payload({ payment_status: 'unpaid' })
    )
    const res = await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    expect(res.status).toBe(200)
    expect(startable(await subState(db))).toBe(true)
  })

  it('is edge-triggered: a redelivery of async_payment_failed sends no second email and no second cancel', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(sendEmail).toHaveBeenCalledTimes(1)
    expect(cancelOperatorSubscription).toHaveBeenCalledTimes(1)

    const again = await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(again.status).toBe(200)
    expect(sendEmail).toHaveBeenCalledTimes(1)
    expect(cancelOperatorSubscription).toHaveBeenCalledTimes(1)
    expect(await orderStatus(db)).toBe('failed')
    expect(startable(await subState(db))).toBe(true)
  })

  it('a failed event that arrives with no prior completed still records, cancels and alerts once', async () => {
    // Stripe delivered async_payment_failed but the completed never landed:
    // nothing to detach, but the ledger transition is the edge.
    const res = await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(res.status).toBe(200)
    expect(await orderStatus(db)).toBe('failed')
    expect(cancelOperatorSubscription).toHaveBeenCalledTimes(1)
    expect(sendEmail).toHaveBeenCalledTimes(1)
    const [, mail] = sendEmail.mock.calls[0] as [unknown, { html: string }]
    expect(mail.html).toContain('nothing to detach')
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(sendEmail).toHaveBeenCalledTimes(1)
  })

  it('a Stripe cancel failure is alerted ONCE, naming the error and the dashboard action, and acked; the detach still holds', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    cancelOperatorSubscription.mockRejectedValueOnce(
      new Error('Stripe cancel failed 502: upstream')
    )
    const res = await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(res.status).toBe(200)
    expect(startable(await subState(db))).toBe(true)
    const [, mail] = sendEmail.mock.calls[0] as [unknown, { html: string }]
    expect(mail.html).toContain('Stripe cancel failed 502')
    expect(mail.html).toContain('Stripe dashboard')
    // A redelivery is silent: no retry of the cancel, no second email.
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      'resend',
      payload({ payment_status: 'unpaid' })
    )
    expect(cancelOperatorSubscription).toHaveBeenCalledTimes(1)
    expect(sendEmail).toHaveBeenCalledTimes(1)
  })

  it('the detach is guarded: a late failed event for an OLD subscription leaves a NEW attachment alone', async () => {
    await handleOperatorCheckoutCompleted(db, payload({ payment_status: 'unpaid' }))
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      undefined,
      payload({ payment_status: 'unpaid' })
    )
    // The client started again and paid by card.
    await handleOperatorCheckoutCompleted(
      db,
      payload({ id: 'cs_test_2', subscription: 'sub_stripe_2' })
    )
    expect((await subState(db)).stripe_subscription_id).toBe('sub_stripe_2')
    // Stripe re-delivers the first session's failure.
    await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      undefined,
      payload({ payment_status: 'unpaid' })
    )
    const row = await subState(db)
    expect(row.stripe_subscription_id).toBe('sub_stripe_2')
    expect(row.status).toBe('active')
  })

  it('acks a failed event for another product without touching anything', async () => {
    const res = await handleOperatorCheckoutAsyncPaymentFailed(
      db,
      'sk',
      undefined,
      payload({ metadata: { product_slug: 'hosted-agent' } })
    )
    expect(res.status).toBe(200)
    expect(cancelOperatorSubscription).not.toHaveBeenCalled()
    expect(await orderStatus(db)).toBeNull()
  })
})
