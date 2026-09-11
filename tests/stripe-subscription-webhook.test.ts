/**
 * Tests for the Operator retainer webhook handling
 * (src/lib/webhooks/stripe-subscription-handler.ts, #1679).
 *
 * Covers: subscription-id extraction across both Stripe API shapes, the
 * finalized/paid cycle-invoice mirror (insert, refresh, idempotency), the
 * payment-failure posture (overdue + alert, never a Machine action), and the
 * customer.subscription.* status mirror onto the local row.
 */

import { describe, it, expect } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  handleRetainerInvoiceFinalized,
  handleRetainerInvoicePaid,
  handleRetainerInvoicePaymentFailed,
  type RetainerInvoicePayload,
} from '../src/lib/webhooks/stripe-subscription-handler'
import { handleSubscriptionLifecycle } from '../src/lib/webhooks/stripe-subscription-lifecycle'

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

interface FakeDbState {
  /** subscriptions row served by SELECT ... WHERE stripe_subscription_id = ? */
  subRow?: {
    id: string
    org_id: string
    entity_id: string
    product_slug: string
    status: string
    stripe_subscription_id: string | null
    settings_json?: string | null
  } | null
  /** subscriptions row served by SELECT ... WHERE id = ? (the ordering
   * fallback's lookup by the row id the invoice metadata names) */
  subRowById?: FakeDbState['subRow']
  /** invoices row served by SELECT ... WHERE stripe_invoice_id = ? */
  invoiceRow?: { id: string; org_id: string; status: string } | null
}

interface Recorded {
  sql: string
  args: unknown[]
}

function makeDb(state: FakeDbState): { db: D1Database; writes: Recorded[] } {
  const writes: Recorded[] = []
  const db = {
    prepare(sql: string) {
      return {
        bind(...args: unknown[]) {
          return {
            first() {
              if (sql.includes('FROM subscriptions') && sql.includes('WHERE id = ?')) {
                return Promise.resolve(state.subRowById ?? null)
              }
              if (sql.includes('FROM subscriptions')) return Promise.resolve(state.subRow ?? null)
              if (sql.includes('FROM invoices')) return Promise.resolve(state.invoiceRow ?? null)
              if (sql.includes('FROM contacts')) return Promise.resolve(null)
              if (sql.includes('FROM entities')) return Promise.resolve({ name: 'Test Firm' })
              throw new Error(`unexpected first(): ${sql}`)
            },
            run() {
              writes.push({ sql, args })
              return Promise.resolve({ meta: { changes: 1 } })
            },
          }
        },
      }
    },
  }
  return { db: db as unknown as D1Database, writes }
}

const SUB_ROW = {
  id: 'local-sub-1',
  org_id: 'org-1',
  entity_id: 'ent-1',
  product_slug: 'operator',
  status: 'active',
  stripe_subscription_id: 'sub_stripe_1',
  settings_json: null,
}

function invoicePayload(overrides?: Partial<RetainerInvoicePayload>): RetainerInvoicePayload {
  return {
    id: 'in_cycle_1',
    amount_due: 500000,
    amount_paid: 0,
    hosted_invoice_url: 'https://invoice.stripe.com/i/x',
    due_date: 1785000000,
    status_transitions: { paid_at: null },
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Cycle-invoice mirror
// ---------------------------------------------------------------------------

describe('handleRetainerInvoiceFinalized', () => {
  it('inserts a local retainer row (status sent) for a known subscription', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW, invoiceRow: null })
    const res = await handleRetainerInvoiceFinalized(db, 'sub_stripe_1', invoicePayload())
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain('INSERT INTO invoices')
    expect(writes[0].sql).toContain("'retainer'")
    expect(writes[0].sql).toContain("'sent'")
    // amount converted from cents; entity/org from the subscription row
    expect(writes[0].args).toContain(5000)
    expect(writes[0].args).toContain('ent-1')
    expect(writes[0].args).toContain('org-1')
  })

  it('refreshes an existing mirror row instead of duplicating', async () => {
    const { db, writes } = makeDb({
      subRow: SUB_ROW,
      invoiceRow: { id: 'local-inv-1', org_id: 'org-1', status: 'sent' },
    })
    await handleRetainerInvoiceFinalized(db, 'sub_stripe_1', invoicePayload())
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain('UPDATE invoices')
  })

  it('skips honestly when no local subscription matches (e.g. a smoke test)', async () => {
    const { db, writes } = makeDb({ subRow: null })
    const res = await handleRetainerInvoiceFinalized(db, 'sub_unknown', invoicePayload())
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })
})

describe('handleRetainerInvoicePaid', () => {
  it('upserts a paid retainer row when no mirror exists (paid arrived first)', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW, invoiceRow: null })
    const res = await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload({ amount_paid: 500000, status_transitions: { paid_at: 1785100000 } })
    )
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain('INSERT INTO invoices')
    expect(writes[0].sql).toContain("'paid'")
  })

  it('marks an existing mirror row paid', async () => {
    const { db, writes } = makeDb({
      subRow: SUB_ROW,
      invoiceRow: { id: 'local-inv-1', org_id: 'org-1', status: 'sent' },
    })
    await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload({ amount_paid: 500000, status_transitions: { paid_at: 1785100000 } })
    )
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain("SET status = 'paid'")
  })

  it('promotes an attached operator row still in provisioning (the ACH first payment settled)', async () => {
    const { db, writes } = makeDb({
      subRow: { ...SUB_ROW, status: 'provisioning' },
      invoiceRow: null,
    })
    await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload({ amount_paid: 500000, status_transitions: { paid_at: 1785100000 } })
    )
    expect(writes).toHaveLength(2)
    expect(writes[0].sql).toContain('INSERT INTO invoices')
    expect(writes[1].sql).toContain("SET status = 'active'")
    expect(writes[1].sql).toContain("product_slug = 'operator' AND status = 'provisioning'")
  })

  it('leaves a provisioning NON-operator row alone (the Hosted Agent has its own activation)', async () => {
    const { db, writes } = makeDb({
      subRow: { ...SUB_ROW, status: 'provisioning', product_slug: 'hosted-agent' },
      invoiceRow: null,
    })
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', invoicePayload())
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain('INSERT INTO invoices')
  })

  it('is idempotent: an already-paid mirror row is not rewritten', async () => {
    const { db, writes } = makeDb({
      subRow: SUB_ROW,
      invoiceRow: { id: 'local-inv-1', org_id: 'org-1', status: 'paid' },
    })
    const res = await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', invoicePayload())
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// Event ordering: invoice.paid before checkout.session.completed (A1)
//
// Stripe does not order the first invoice.paid after the checkout event, so
// the paid invoice can arrive while no local row carries the Stripe
// subscription id. The checkout stamped `smd_subscription_id` onto the
// subscription's metadata, and Stripe snapshots that onto the invoice at
// `parent.subscription_details.metadata` (the prod endpoint pins
// 2026-03-25.dahlia, post-basil) or `subscription_details.metadata` on
// older versions.
// ---------------------------------------------------------------------------

describe('handleRetainerInvoicePaid — ordering fallback via invoice metadata', () => {
  const PROVISIONING_UNATTACHED = {
    ...SUB_ROW,
    status: 'provisioning',
    stripe_subscription_id: null,
  }
  /** The live (post-basil) shape. */
  const nestedMeta = {
    customer: 'cus_1',
    parent: { subscription_details: { metadata: { smd_subscription_id: 'local-sub-1' } } },
  }

  it('binds and promotes the unattached provisioning row the metadata names, then mirrors the invoice', async () => {
    const { db, writes } = makeDb({
      subRow: null,
      subRowById: PROVISIONING_UNATTACHED,
      invoiceRow: null,
    })
    const res = await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload({ amount_paid: 500000, ...nestedMeta })
    )
    expect(res.status).toBe(200)
    const sqls = writes.map((w) => w.sql)
    expect(sqls[0]).toContain('SET stripe_subscription_id = ?')
    expect(writes[0].args).toEqual(['sub_stripe_1', 'cus_1', 'cus_1', 'local-sub-1'])
    expect(sqls[1]).toContain("SET status = 'active'")
    expect(sqls[1]).toContain("status = 'provisioning'")
    expect(sqls[2]).toContain('INSERT INTO invoices')
    expect(sqls[2]).toContain("'paid'")
    expect(writes).toHaveLength(3)
  })

  it('reads the pre-basil position too (subscription_details.metadata)', async () => {
    const { db, writes } = makeDb({
      subRow: null,
      subRowById: PROVISIONING_UNATTACHED,
      invoiceRow: null,
    })
    await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload({
        amount_paid: 500000,
        subscription_details: { metadata: { smd_subscription_id: 'local-sub-1' } },
      })
    )
    expect(writes).toHaveLength(3)
    expect(writes[0].sql).toContain('SET stripe_subscription_id = ?')
  })

  it('keeps the honest skip when the metadata names nothing (a smoke-test subscription)', async () => {
    const { db, writes } = makeDb({ subRow: null, subRowById: PROVISIONING_UNATTACHED })
    const res = await handleRetainerInvoicePaid(db, undefined, 'sub_unknown', invoicePayload())
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })

  it('does NOT re-bind a row that already carries a different Stripe subscription', async () => {
    // The named row is attached to another subscription: a stale or
    // mis-stamped invoice must not steal it. The NULL check lives here, in
    // the caller, because attachStripeSubscription is an unguarded UPDATE.
    const { db, writes } = makeDb({
      subRow: null,
      subRowById: { ...SUB_ROW, stripe_subscription_id: 'sub_stripe_OTHER' },
    })
    const res = await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload(nestedMeta)
    )
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })

  it('does NOT bind a row that is past provisioning (a cancelled row is not resurrected)', async () => {
    const { db, writes } = makeDb({
      subRow: null,
      subRowById: { ...PROVISIONING_UNATTACHED, status: 'cancelled' },
    })
    const res = await handleRetainerInvoicePaid(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload(nestedMeta)
    )
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })

  it('does NOT bind a non-operator row', async () => {
    const { db, writes } = makeDb({
      subRow: null,
      subRowById: { ...PROVISIONING_UNATTACHED, product_slug: 'hosted-agent' },
    })
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_1', invoicePayload(nestedMeta))
    expect(writes).toHaveLength(0)
  })

  it('never binds from invoice.finalized or invoice.payment_failed', async () => {
    // finalized precedes ACH collection; payment_failed is not a go-live act.
    const state = { subRow: null, subRowById: PROVISIONING_UNATTACHED, invoiceRow: null }
    const finalized = makeDb(state)
    await handleRetainerInvoiceFinalized(finalized.db, 'sub_stripe_1', invoicePayload(nestedMeta))
    expect(finalized.writes).toHaveLength(0)
    const failed = makeDb(state)
    await handleRetainerInvoicePaymentFailed(
      failed.db,
      undefined,
      'sub_stripe_1',
      invoicePayload(nestedMeta)
    )
    expect(failed.writes).toHaveLength(0)
  })
})

describe('handleRetainerInvoicePaymentFailed', () => {
  it('marks the mirror row overdue and takes NO other local action', async () => {
    const { db, writes } = makeDb({
      subRow: SUB_ROW,
      invoiceRow: { id: 'local-inv-1', org_id: 'org-1', status: 'sent' },
    })
    const res = await handleRetainerInvoicePaymentFailed(
      db,
      undefined,
      'sub_stripe_1',
      invoicePayload()
    )
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain("SET status = 'overdue'")
    // Exactly one write: no subscriptions-row change, no Machine-facing action.
    const touchedSubscriptions = writes.some((w) => w.sql.includes('UPDATE subscriptions'))
    expect(touchedSubscriptions).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Subscription lifecycle mirror
// ---------------------------------------------------------------------------

describe('handleSubscriptionLifecycle', () => {
  it('deleted → cancelled with ended_at', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    await handleSubscriptionLifecycle(db, 'customer.subscription.deleted', {
      id: 'sub_stripe_1',
      status: 'canceled',
    })
    expect(writes).toHaveLength(1)
    expect(writes[0].sql).toContain("status = 'cancelled'")
    expect(writes[0].sql).toContain('ended_at')
  })

  it('updated with pause_collection present → paused', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      pause_collection: { behavior: 'void' },
    })
    expect(writes).toHaveLength(1)
    expect(writes[0].args).toContain('paused')
  })

  it('updated active without pause_collection → active; past_due keeps access', async () => {
    for (const status of ['active', 'past_due']) {
      const { db, writes } = makeDb({ subRow: SUB_ROW })
      await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
        id: 'sub_stripe_1',
        status,
      })
      expect(writes).toHaveLength(1)
      expect(writes[0].args).toContain('active')
    }
  })

  it('unknown Stripe subscription is skipped honestly', async () => {
    const { db, writes } = makeDb({ subRow: null })
    const res = await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_unknown',
      status: 'active',
    })
    expect(res.status).toBe(200)
    expect(writes).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// Client-scheduled cancellation mirror
//
// The client cancels in the Stripe Billing Portal (mode: at_period_end).
// Stripe keeps the subscription `active` and only flips cancel_at_period_end,
// so without this mirror the cancellation is invisible on both surfaces until
// the delete event lands weeks later.
// ---------------------------------------------------------------------------

/** Writes that touch the scheduled-cancellation key, in order. */
function cancelWrites(writes: { sql: string; args: unknown[] }[]) {
  return writes.filter((w) => w.sql.includes('cancel_at'))
}

describe('scheduled cancellation mirror', () => {
  const PERIOD_END = 1788883200 // 2026-09-08T00:00:00Z

  it('records the end date from cancel_at', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      cancel_at_period_end: true,
      cancel_at: PERIOD_END,
    })
    const c = cancelWrites(writes)
    expect(c).toHaveLength(1)
    expect(c[0].sql).toContain('json_set')
    expect(c[0].args[0]).toBe(new Date(PERIOD_END * 1000).toISOString())
  })

  it('falls back to the ITEM period end — this API version has no top-level one', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      cancel_at_period_end: true,
      cancel_at: null,
      items: { data: [{ current_period_end: PERIOD_END }] },
    })
    expect(cancelWrites(writes)[0].args[0]).toBe(new Date(PERIOD_END * 1000).toISOString())
  })

  it('never invents a date when Stripe names none', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    const res = await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      cancel_at_period_end: true,
      cancel_at: null,
    })
    expect(res.status).toBe(200)
    expect(cancelWrites(writes)).toHaveLength(0)
  })

  it('does not re-write an already-recorded schedule (Stripe resends the whole object)', async () => {
    const iso = new Date(PERIOD_END * 1000).toISOString()
    const { db, writes } = makeDb({
      subRow: { ...SUB_ROW, settings_json: JSON.stringify({ cancel_at: iso }) },
    })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      cancel_at_period_end: true,
      cancel_at: PERIOD_END,
    })
    expect(cancelWrites(writes)).toHaveLength(0)
  })

  it('clears the schedule when the client reverses the cancellation', async () => {
    const { db, writes } = makeDb({
      subRow: {
        ...SUB_ROW,
        settings_json: JSON.stringify({ cancel_at: new Date(PERIOD_END * 1000).toISOString() }),
      },
    })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
      cancel_at_period_end: false,
    })
    const c = cancelWrites(writes)
    expect(c).toHaveLength(1)
    expect(c[0].sql).toContain('json_remove')
  })

  it('an ordinary update on a healthy subscription writes no cancellation state', async () => {
    const { db, writes } = makeDb({ subRow: SUB_ROW })
    await handleSubscriptionLifecycle(db, 'customer.subscription.updated', {
      id: 'sub_stripe_1',
      status: 'active',
    })
    expect(cancelWrites(writes)).toHaveLength(0)
  })
})
