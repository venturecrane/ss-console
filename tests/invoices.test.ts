/**
 * Behavioural tests for invoicing: the two admin routes, the two client
 * emails, and the portal's billing card.
 *
 * The data layer, the Stripe client, and the Stripe and SignWell webhooks
 * already had behavioural suites (src/lib/db/invoices.test.ts,
 * src/lib/stripe/client.test.ts, src/lib/webhooks/stripe-handler.test.ts,
 * tests/webhooks/stripe-verify.test.ts, src/lib/webhooks/signwell-handler.test.ts).
 * Until 2026-09-11 the rest of this file still matched source text (review
 * 2026-09-10, Testing 3). The routes now run against a migrated D1 with the
 * Stripe client and the email transport as the only fakes. The policy guards
 * over the Astro billing pages are kept at the end, each with its reason.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createInvoice,
  getInvoice,
  listInvoices,
  listLineItemsForInvoice,
} from '../src/lib/db/invoices'
import { createContact } from '../src/lib/db/contacts'
import { CARD_FEE_LINE_DESCRIPTION } from '../src/lib/pricing/card-fee'
import { invoiceSentEmailHtml, paymentConfirmationEmailHtml } from '../src/lib/email/templates'
import { BRAND_NAME } from '../src/lib/config/brand'
import { loadHomeCards } from '../src/lib/portal/home-cards'
import { formatShortDate } from '../src/lib/portal/formatters'
import { resolvePortalOfferings } from '../src/lib/portal/offerings'
import {
  adminSession,
  bindEnv,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedEngagement,
  seedEntity,
  seedOrg,
} from './_stubs/behavioural'

const stripe = {
  createStripeInvoice: vi.fn(),
  finalizeStripeInvoice: vi.fn(),
  sendStripeInvoice: vi.fn(),
  voidStripeInvoice: vi.fn(),
}
vi.mock('../src/lib/stripe/client', () => ({
  createStripeInvoice: (...args: unknown[]) => stripe.createStripeInvoice(...args),
  finalizeStripeInvoice: (...args: unknown[]) => stripe.finalizeStripeInvoice(...args),
  sendStripeInvoice: (...args: unknown[]) => stripe.sendStripeInvoice(...args),
  voidStripeInvoice: (...args: unknown[]) => stripe.voidStripeInvoice(...args),
}))
const sendEmail = vi.fn()
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: (...args: unknown[]) => sendEmail(...args),
}))

// Import AFTER the mocks so the routes bind the mocked modules.
import { POST as createRoute } from '../src/pages/api/admin/invoices/index'
import { POST as actionRoute } from '../src/pages/api/admin/invoices/[id]'

const ORG = 'org-a'
const ENT = 'ent-a'
const ENG = 'eng-a'

async function seedAll(db: D1Database) {
  await seedOrg(db, ORG)
  await seedEntity(db, { id: ENT, orgId: ORG, stage: 'engaged', name: 'Alpha Plumbing' })
  await seedEngagement(db, { id: ENG, orgId: ORG, entityId: ENT, status: 'scheduled' })
}

describe('POST /api/admin/invoices', () => {
  let db: D1Database

  const call = (
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG)
  ) =>
    createRoute(
      routeContext({
        request: formRequest('http://test.local/api/admin/invoices', fields),
        session,
      }) as unknown as Parameters<typeof createRoute>[0]
    )

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
    bindEnv({ DB: db })
  })

  it('answers 401 with no session; names a missing field, an unknown type, and a non-positive amount', async () => {
    expect((await call({ client_id: ENT, type: 'deposit', amount: '100' }, null)).status).toBe(401)
    expect(locationOf(await call({ client_id: ENT, type: 'deposit' }))).toBe(
      '/admin/entities?error=missing'
    )
    expect(
      locationQuery(await call({ client_id: ENT, type: 'tip', amount: '100' })).get('error')
    ).toBe('invalid_type')
    expect(
      locationQuery(await call({ client_id: ENT, type: 'deposit', amount: '0' })).get('error')
    ).toBe('invalid_amount')
    expect(await listInvoices(db, ORG, {})).toEqual([])
  })

  it('creates a bare draft when no line is authored, and returns to the caller page', async () => {
    const res = await call({
      client_id: ENT,
      type: 'deposit',
      amount: '100',
      engagement_id: ENG,
      due_date: '2026-10-01',
      redirect_url: `/admin/entities/${ENT}`,
    })
    expect(locationOf(res)).toBe(`/admin/entities/${ENT}?created=1`)
    const [invoice] = await listInvoices(db, ORG, {})
    expect(invoice).toMatchObject({
      entity_id: ENT,
      engagement_id: ENG,
      type: 'deposit',
      amount: 100,
      status: 'draft',
      due_date: '2026-10-01',
    })
    expect(await listLineItemsForInvoice(db, invoice.id)).toEqual([])
  })

  it('an authored line becomes one line item for the full amount; card payment adds the 3% fee line and grows the total', async () => {
    await call({ client_id: ENT, type: 'milestone', amount: '100', line_item: 'Intake redesign' })
    const [ach] = await listInvoices(db, ORG, {})
    expect(ach.amount).toBe(100)
    expect(
      (await listLineItemsForInvoice(db, ach.id)).map((l) => [l.description, l.amount_cents])
    ).toEqual([['Intake redesign', 10000]])

    await call({
      client_id: ENT,
      type: 'milestone',
      amount: '100',
      line_item: 'Intake redesign',
      card_payment: 'on',
    })
    const card = (await listInvoices(db, ORG, {})).find((i) => i.id !== ach.id)!
    expect(card.amount).toBe(103)
    expect(
      (await listLineItemsForInvoice(db, card.id)).map((l) => [l.description, l.amount_cents])
    ).toEqual([
      ['Intake redesign', 10000],
      [CARD_FEE_LINE_DESCRIPTION, 300],
    ])
  })
})

describe('POST /api/admin/invoices/[id]', () => {
  let db: D1Database

  const call = (
    id: string,
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG)
  ) =>
    actionRoute(
      routeContext({
        request: formRequest(`http://test.local/api/admin/invoices/${id}`, fields),
        params: { id },
        session,
      }) as unknown as Parameters<typeof actionRoute>[0]
    )

  const draftWithLine = (over: Partial<Parameters<typeof createInvoice>[2]> = {}) =>
    createInvoice(db, ORG, {
      entity_id: ENT,
      engagement_id: ENG,
      type: 'deposit',
      amount: 100,
      line_items: [{ description: 'Deposit', amount_cents: 10000 }],
      ...over,
    })

  beforeEach(async () => {
    for (const fn of Object.values(stripe)) fn.mockReset()
    sendEmail.mockReset()
    sendEmail.mockResolvedValue({ success: true, id: 'email-1' })
    stripe.createStripeInvoice.mockResolvedValue({ id: 'in_new', hosted_invoice_url: null })
    stripe.finalizeStripeInvoice.mockResolvedValue({
      id: 'in_new',
      hosted_invoice_url: 'https://pay.stripe/new',
    })
    stripe.sendStripeInvoice.mockResolvedValue({
      id: 'in_new',
      hosted_invoice_url: 'https://pay.stripe/new',
    })
    stripe.voidStripeInvoice.mockResolvedValue(undefined)

    db = await migratedDb()
    await seedAll(db)
    await createContact(db, ORG, ENT, { name: 'Dana', email: 'dana@example.com' })
    bindEnv({
      DB: db,
      STRIPE_API_KEY: 'sk_test',
      RESEND_API_KEY: 're_test',
      APP_BASE_URL: 'https://smd.services',
      PORTAL_BASE_URL: 'https://portal.smd.services',
    })
  })

  it('answers 401 with no session, not_found for an unknown invoice, and error=missing for no action', async () => {
    const invoice = await draftWithLine()
    expect((await call(invoice.id, { action: 'send' }, null)).status).toBe(401)
    expect(locationOf(await call('nope', { action: 'send' }))).toBe(
      '/admin/entities?error=not_found'
    )
    expect(locationQuery(await call(invoice.id, {})).get('error')).toBe('missing')
    expect(stripe.createStripeInvoice).not.toHaveBeenCalled()
  })

  it('send: creates the Stripe invoice from the authored lines as ACH, sends it, records the ids, and emails the client', async () => {
    const invoice = await draftWithLine()
    const res = await call(invoice.id, { action: 'send', redirect_url: '/admin/billing' })
    expect(locationOf(res)).toBe('/admin/billing?saved=1')

    expect(stripe.createStripeInvoice).toHaveBeenCalledTimes(1)
    const [apiKey, params] = stripe.createStripeInvoice.mock.calls[0] as [
      string,
      Record<string, unknown>,
    ]
    expect(apiKey).toBe('sk_test')
    expect(params).toMatchObject({
      customer_email: 'dana@example.com',
      line_items: [{ amount: 10000, currency: 'usd', description: 'Deposit', quantity: 1 }],
      days_until_due: 30,
      collection_method: 'send_invoice',
      metadata: { invoice_id: invoice.id, org_id: ORG, type: 'deposit' },
      payment_settings: { payment_method_types: ['us_bank_account'] },
    })
    expect(stripe.sendStripeInvoice).toHaveBeenCalledWith('sk_test', 'in_new')
    expect(stripe.finalizeStripeInvoice).not.toHaveBeenCalled()

    expect(await getInvoice(db, ORG, invoice.id)).toMatchObject({
      status: 'sent',
      stripe_invoice_id: 'in_new',
      stripe_hosted_url: 'https://pay.stripe/new',
    })
    expect(sendEmail).toHaveBeenCalledTimes(1)
    const [, email] = sendEmail.mock.calls[0] as [string, { to: string; html: string }]
    expect(email.to).toBe('dana@example.com')
    expect(email.html).toContain('$100.00')
    expect(email.html).toContain('https://portal.smd.services/portal/billing')
  })

  it('present: finalizes without sending and emails no one; a card invoice offers card only', async () => {
    const invoice = await draftWithLine({
      amount: 103,
      line_items: [
        { description: 'Deposit', amount_cents: 10000 },
        { description: CARD_FEE_LINE_DESCRIPTION, amount_cents: 300 },
      ],
    })
    const res = await call(invoice.id, { action: 'present' })
    expect(locationQuery(res).get('saved')).toBe('1')
    expect(stripe.finalizeStripeInvoice).toHaveBeenCalledWith('sk_test', 'in_new')
    expect(stripe.sendStripeInvoice).not.toHaveBeenCalled()
    expect(sendEmail).not.toHaveBeenCalled()
    const [, params] = stripe.createStripeInvoice.mock.calls[0] as [string, Record<string, unknown>]
    expect(params.payment_settings).toEqual({ payment_method_types: ['card'] })
    expect((await getInvoice(db, ORG, invoice.id))?.status).toBe('sent')
  })

  it('issue refuses a non-draft, an entity with no billing contact, and an invoice with no authored line; Stripe never sees those', async () => {
    const sent = await draftWithLine()
    await db.prepare("UPDATE invoices SET status = 'sent' WHERE id = ?").bind(sent.id).run()
    expect(locationQuery(await call(sent.id, { action: 'send' })).get('error')).toBe(
      'invalid_transition'
    )

    const bare = await draftWithLine({ line_items: [] })
    expect(locationQuery(await call(bare.id, { action: 'send' })).get('error')).toBe(
      'missing_line_items'
    )

    await db.prepare('DELETE FROM contacts').run()
    const draft = await draftWithLine()
    expect(locationQuery(await call(draft.id, { action: 'present' })).get('error')).toBe(
      'no_billing_contact'
    )
    expect(stripe.createStripeInvoice).not.toHaveBeenCalled()
  })

  it('a Stripe failure on issue is reported by message and the invoice stays a draft', async () => {
    stripe.createStripeInvoice.mockRejectedValue(new Error('Stripe create failed (402)'))
    const invoice = await draftWithLine()
    const res = await call(invoice.id, { action: 'send' })
    expect(locationQuery(res).get('error')).toBe('Stripe create failed (402)')
    expect((await getInvoice(db, ORG, invoice.id))?.status).toBe('draft')
    expect(sendEmail).not.toHaveBeenCalled()
  })

  it('reschedule: replacement first, row repointed second, original voided last; a failed void is named', async () => {
    const invoice = await draftWithLine()
    await db
      .prepare("UPDATE invoices SET status = 'sent', stripe_invoice_id = 'in_old' WHERE id = ?")
      .bind(invoice.id)
      .run()
    const order: string[] = []
    stripe.createStripeInvoice.mockImplementation(async () => {
      order.push('create')
      return { id: 'in_new', hosted_invoice_url: null }
    })
    stripe.finalizeStripeInvoice.mockImplementation(async () => {
      order.push('finalize')
      return { id: 'in_new', hosted_invoice_url: 'https://pay.stripe/new' }
    })
    stripe.voidStripeInvoice.mockImplementation(async () => {
      order.push('void')
    })

    const res = await call(invoice.id, { action: 'reschedule', due_date: '2026-12-01' })
    expect(locationQuery(res).get('rescheduled')).toBe('1')
    expect(order).toEqual(['create', 'finalize', 'void'])
    const [, params] = stripe.createStripeInvoice.mock.calls[0] as [string, Record<string, unknown>]
    expect(params.metadata).toMatchObject({ replaces: 'in_old' })
    expect(typeof params.due_date).toBe('number')
    expect(stripe.voidStripeInvoice).toHaveBeenCalledWith('sk_test', 'in_old')
    expect(await getInvoice(db, ORG, invoice.id)).toMatchObject({
      status: 'sent',
      due_date: '2026-12-01',
      stripe_invoice_id: 'in_new',
      stripe_hosted_url: 'https://pay.stripe/new',
    })
    expect(sendEmail).not.toHaveBeenCalled()

    stripe.voidStripeInvoice.mockRejectedValue(new Error('void failed'))
    stripe.createStripeInvoice.mockResolvedValue({ id: 'in_newer', hosted_invoice_url: null })
    const stale = await call(invoice.id, { action: 'reschedule', due_date: '2026-12-15' })
    expect(locationQuery(stale).get('error')).toBe('stale_stripe_invoice')
    expect((await getInvoice(db, ORG, invoice.id))?.stripe_invoice_id).toBe('in_newer')
  })

  it('reschedule: refuses a draft and a malformed date; with no Stripe invoice it only moves the date', async () => {
    const draft = await draftWithLine()
    expect(
      locationQuery(await call(draft.id, { action: 'reschedule', due_date: '2026-12-01' })).get(
        'error'
      )
    ).toBe('invalid_transition')
    await db.prepare("UPDATE invoices SET status = 'sent' WHERE id = ?").bind(draft.id).run()
    expect(
      locationQuery(await call(draft.id, { action: 'reschedule', due_date: 'next week' })).get(
        'error'
      )
    ).toBe('invalid_due_date')
    const res = await call(draft.id, { action: 'reschedule', due_date: '2026-12-01' })
    expect(locationQuery(res).get('rescheduled')).toBe('1')
    expect((await getInvoice(db, ORG, draft.id))?.due_date).toBe('2026-12-01')
    expect(stripe.createStripeInvoice).not.toHaveBeenCalled()
  })

  it('void: voids in Stripe when it can and locally regardless; refuses once paid', async () => {
    const sent = await draftWithLine()
    await db
      .prepare("UPDATE invoices SET status = 'sent', stripe_invoice_id = 'in_old' WHERE id = ?")
      .bind(sent.id)
      .run()
    stripe.voidStripeInvoice.mockRejectedValue(new Error('already void'))
    expect(locationQuery(await call(sent.id, { action: 'void' })).get('saved')).toBe('1')
    expect(stripe.voidStripeInvoice).toHaveBeenCalledWith('sk_test', 'in_old')
    expect((await getInvoice(db, ORG, sent.id))?.status).toBe('void')

    const paid = await draftWithLine()
    await db.prepare("UPDATE invoices SET status = 'paid' WHERE id = ?").bind(paid.id).run()
    expect(locationQuery(await call(paid.id, { action: 'void' })).get('error')).toBe(
      'invalid_transition'
    )
    expect((await getInvoice(db, ORG, paid.id))?.status).toBe('paid')
  })

  it('mark_paid: records a manual payment, and a paid deposit activates its scheduled engagement', async () => {
    const deposit = await draftWithLine()
    await db.prepare("UPDATE invoices SET status = 'sent' WHERE id = ?").bind(deposit.id).run()
    const before = Date.now()
    expect(locationQuery(await call(deposit.id, { action: 'mark_paid' })).get('saved')).toBe('1')
    const paid = await getInvoice(db, ORG, deposit.id)
    expect(paid).toMatchObject({ status: 'paid', payment_method: 'manual' })
    expect(Date.parse(paid!.paid_at!)).toBeGreaterThanOrEqual(before - 1000)
    const engagement = await db
      .prepare('SELECT status, start_date FROM engagements WHERE id = ?')
      .bind(ENG)
      .first<{ status: string; start_date: string | null }>()
    expect(engagement?.status).toBe('active')
    expect(engagement?.start_date).not.toBeNull()

    const draft = await draftWithLine()
    expect(locationQuery(await call(draft.id, { action: 'mark_paid' })).get('error')).toBe(
      'invalid_transition'
    )
  })
})

describe('invoice emails', () => {
  it('the sent notice names the amount and links the portal; markup in the amount is escaped', () => {
    const html = invoiceSentEmailHtml(
      'Dana',
      '$1,000.00',
      'https://portal.smd.services/portal/billing'
    )
    expect(html).toContain('Dana')
    expect(html).toContain(`Your invoice from ${BRAND_NAME} for $1,000.00 is ready`)
    expect(html).toContain('href="https://portal.smd.services/portal/billing"')
    expect(invoiceSentEmailHtml('Dana', '<b>$1</b>', 'https://x')).not.toContain('<b>$1</b>')
  })

  it('the payment confirmation acknowledges the amount received', () => {
    const html = paymentConfirmationEmailHtml('Dana', '$500.00')
    expect(html).toContain('Dana')
    expect(html).toContain("We've received your payment of $500.00")
  })
})

describe('portal home: the billing card keeps the client one tap from a pending invoice', () => {
  let db: D1Database

  const billingCard = async () => {
    const offerings = await resolvePortalOfferings(db, ORG, ENT)
    const cards = await loadHomeCards(db, { orgId: ORG, entityId: ENT, userId: 'u-1', offerings })
    return cards.find((c) => c.key === 'billing') ?? null
  }

  const invoiceIn = async (status: string, dueDate: string | null = null) => {
    const invoice = await createInvoice(db, ORG, {
      entity_id: ENT,
      type: 'milestone',
      amount: 250,
      due_date: dueDate,
    })
    await db.prepare('UPDATE invoices SET status = ? WHERE id = ?').bind(status, invoice.id).run()
    return invoice.id
  }

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  it('no billing relationship, no card; paid history reads "Up to date" with nothing to do', async () => {
    expect(await billingCard()).toBeNull()
    await invoiceIn('paid')
    expect(await billingCard()).toMatchObject({ statusLabel: 'Up to date', needsYou: null })
  })

  it('a sent or overdue invoice becomes the "Pay invoice" action, deep-linked, with its due date', async () => {
    const sent = await invoiceIn('sent', '2026-10-15')
    expect(await billingCard()).toMatchObject({
      statusLabel: 'Invoice due',
      meta: [`Due ${formatShortDate('2026-10-15')}`],
      needsYou: { label: 'Pay invoice', href: `/portal/billing/invoices/${sent}` },
    })
    await db.prepare("UPDATE invoices SET status = 'overdue' WHERE id = ?").bind(sent).run()
    expect((await billingCard())?.statusLabel).toBe('Invoice overdue')
  })
})

// The billing pages are Astro; nothing here can invoke them. What is kept is
// policy, each guard naming the rule and the incident behind it.
describe('billing pages: policy guards', () => {
  const list = readFileSync(resolve('src/pages/portal/billing/index.astro'), 'utf-8')
  const detail =
    readFileSync(resolve('src/pages/portal/billing/invoices/[id].astro'), 'utf-8') +
    '\n' +
    readFileSync(resolve('src/lib/portal/invoice-detail.ts'), 'utf-8')

  it('ACH is us_bank_account, never the legacy ach_debit type (A&P implementation invoice, 2026-09-09)', () => {
    for (const file of [
      'src/pages/api/admin/invoices/[id].ts',
      'src/lib/db/milestones.ts',
      'src/lib/stripe/client.ts',
      'src/lib/stripe/subscriptions.ts',
    ]) {
      expect(readFileSync(resolve(file), 'utf-8'), file).not.toContain("'ach_debit'")
    }
  })

  it('the detail page pays through the Stripe hosted URL only, never a route that does not exist (#419)', () => {
    expect(detail).toContain('isPayable')
    expect(detail).toContain('Payment link pending')
    expect(detail).not.toMatch(/\/api\/invoices\/\$\{[^}]*\}\/pay/)
    expect(detail).not.toContain('payHref')
  })

  it('the detail page renders authored line items only, never a fabricated fallback row (#398)', () => {
    expect(detail).not.toContain('Engagement work')
    expect(detail).not.toContain('displayLineItems')
    expect(detail).not.toMatch(/scope_summary\s*\?\?/)
  })

  it('the "Payment details" section stays gone (Captain, 2026-09-09: the page is the lines, the total, and the Pay card)', () => {
    expect(detail).not.toContain('Payment details')
    expect(detail).not.toContain('InvoicePaymentDetails')
    expect(existsSync(resolve('src/components/portal/InvoicePaymentDetails.astro'))).toBe(false)
  })

  it('the list signals action through tone, not a second Pay control beside the card link (UI-PATTERNS R2)', () => {
    expect(list).toContain('resolveInvoiceTone')
    expect(list).not.toMatch(/\bPay\b/)
    expect(list).not.toContain('Payment link pending')
  })
})
