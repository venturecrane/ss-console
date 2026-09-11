/**
 * Stripe invoice → subscription linkage (ss#2315, #2280 hardening item 10).
 *
 * The linkage between a cycle invoice and its subscription decides which of
 * two handlers runs, and it has moved between Stripe API versions. Reading it
 * as `string | null` overloads `null`: "this is a one-time console-originated
 * invoice" and "this is a retainer invoice whose linkage this code cannot
 * read" become the same value, and the second is answered with a 200 ack and
 * no mirror — a money-adjacent row that quietly never appears.
 *
 * These tests pin the three outcomes apart, and pin the expanded-object shape
 * (Stripe's expandable fields) as parseable rather than unknown.
 *
 * The route is driven end-to-end, through signature verification, because the
 * ack is the defect: the observable is the RESPONSE, and a handler-level test
 * would not see it.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  installWorkerdPolyfills,
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

// The operator checkout handlers are observed by their calls: routing is the
// claim under test, not the handlers' own behaviour (tests/operator-go-live-
// billing.test.ts covers that against real D1).
const handleOperatorCheckoutCompleted = vi.fn()
const handleOperatorCheckoutAsyncPaymentFailed = vi.fn()
vi.mock('../../src/lib/webhooks/operator-checkout-handler', () => ({
  handleOperatorCheckoutCompleted: (...args: unknown[]) => handleOperatorCheckoutCompleted(...args),
  handleOperatorCheckoutAsyncPaymentFailed: (...args: unknown[]) =>
    handleOperatorCheckoutAsyncPaymentFailed(...args),
}))

import { POST } from '../../src/pages/api/webhooks/stripe'
import { resolveStripeSubscriptionLinkage } from '../../src/lib/webhooks/stripe-subscription-handler'

installWorkerdPolyfills()

const SECRET = 'whsec_test_stripe_webhook_secret'

async function computeStripeV1(timestamp: number, body: string, secret: string): Promise<string> {
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, enc.encode(`${timestamp}.${body}`))
  return Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/** A schema-valid invoice event (`invoice.finalized` by default);
 * `invoiceExtra` carries the shape under test. */
function invoiceEvent(invoiceExtra: Record<string, unknown>, type = 'invoice.finalized'): string {
  return JSON.stringify({
    id: 'evt_linkage',
    object: 'event',
    type,
    created: 1785000000,
    data: {
      object: {
        id: 'in_linkage_1',
        object: 'invoice',
        status: 'open',
        amount_due: 500000,
        amount_paid: 0,
        currency: 'usd',
        customer: 'cus_1',
        customer_email: null,
        description: null,
        hosted_invoice_url: null,
        invoice_pdf: null,
        collection_method: 'charge_automatically',
        status_transitions: { paid_at: null, finalized_at: 1785000000, voided_at: null },
        metadata: {},
        created: 1785000000,
        due_date: null,
        ...invoiceExtra,
      },
    },
  })
}

/** A schema-valid checkout.session.* event of the given type; `sessionExtra`
 * overrides the session object. The operator `product_slug` is the default. */
function checkoutEvent(type: string, sessionExtra: Record<string, unknown> = {}): string {
  return JSON.stringify({
    id: 'evt_checkout',
    object: 'event',
    type,
    created: 1785000000,
    data: {
      object: {
        id: 'cs_test_route_1',
        object: 'checkout.session',
        client_reference_id: 'user-1',
        customer: 'cus_1',
        subscription: 'sub_stripe_1',
        amount_total: 500000,
        customer_details: { email: 'admin@example.com', name: 'Admin' },
        metadata: { product_slug: 'operator', smd_subscription_id: 'sub-op' },
        payment_status: 'unpaid',
        ...sessionExtra,
      },
    },
  })
}

async function postEvent(body: string): Promise<Response> {
  const ts = Math.floor(Date.now() / 1000)
  const sig = await computeStripeV1(ts, body, SECRET)
  const request = new Request('http://test.local/api/webhooks/stripe', {
    method: 'POST',
    headers: new Headers({
      'Content-Type': 'application/json',
      'stripe-signature': `t=${ts},v1=${sig}`,
    }),
    body,
  })
  const context = {
    request,
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof POST>[0]
  return POST(context)
}

// ---------------------------------------------------------------------------
// Tri-state resolution
// ---------------------------------------------------------------------------

describe('resolveStripeSubscriptionLinkage', () => {
  it('reads the legacy top-level string', () => {
    expect(resolveStripeSubscriptionLinkage({ subscription: 'sub_a' })).toEqual({
      kind: 'linked',
      subscriptionId: 'sub_a',
    })
  })

  it('reads the parent.subscription_details string', () => {
    expect(
      resolveStripeSubscriptionLinkage({
        parent: { type: 'subscription_details', subscription_details: { subscription: 'sub_b' } },
      })
    ).toEqual({ kind: 'linked', subscriptionId: 'sub_b' })
  })

  it('reads an EXPANDED subscription object at either position', () => {
    expect(
      resolveStripeSubscriptionLinkage({
        subscription: { id: 'sub_c', object: 'subscription' },
      })
    ).toEqual({ kind: 'linked', subscriptionId: 'sub_c' })

    expect(
      resolveStripeSubscriptionLinkage({
        parent: { subscription_details: { subscription: { id: 'sub_d', object: 'subscription' } } },
      })
    ).toEqual({ kind: 'linked', subscriptionId: 'sub_d' })
  })

  it('reports a genuine one-time invoice as unlinked, not unrecognized', () => {
    expect(resolveStripeSubscriptionLinkage({ billing_reason: 'manual' }).kind).toBe('unlinked')
    expect(resolveStripeSubscriptionLinkage({ subscription: null }).kind).toBe('unlinked')
    expect(
      resolveStripeSubscriptionLinkage({ parent: { type: null, subscription_details: null } }).kind
    ).toBe('unlinked')
    expect(resolveStripeSubscriptionLinkage({}).kind).toBe('unlinked')
    expect(resolveStripeSubscriptionLinkage(null).kind).toBe('unlinked')
  })

  it('reports a subscription-signalling invoice it cannot read as unrecognized', () => {
    // billing_reason is the version-stable signal: whatever Stripe does to the
    // linkage field next, a cycle invoice still says why it was billed.
    expect(resolveStripeSubscriptionLinkage({ billing_reason: 'subscription_cycle' }).kind).toBe(
      'unrecognized'
    )
    // A linkage field that is present but shaped in a way this code cannot read.
    expect(resolveStripeSubscriptionLinkage({ subscription: { ref: 'sub_z' } }).kind).toBe(
      'unrecognized'
    )
    expect(
      resolveStripeSubscriptionLinkage({
        parent: { type: 'subscription_details', subscription_details: { sub_id: 'sub_z' } },
      }).kind
    ).toBe('unrecognized')
  })
})

// ---------------------------------------------------------------------------
// The ack is the defect
// ---------------------------------------------------------------------------

describe('POST /api/webhooks/stripe — unreadable subscription linkage', () => {
  beforeEach(() => {
    Object.assign(testEnv, { STRIPE_WEBHOOK_SECRET: SECRET })
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'log').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.restoreAllMocks()
  })

  it('fails the delivery instead of acking when the shape is unreadable', async () => {
    const res = await postEvent(
      invoiceEvent({ billing_reason: 'subscription_cycle', parent: { type: 'something_new' } })
    )

    expect(res.status).toBe(500)
    expect(console.error).toHaveBeenCalled()
  })

  it('still acks a genuine one-time invoice without alerting', async () => {
    // The falsifier for the detector itself: if this over-fires, the legacy
    // console-originated flow starts 500ing and Stripe retry-loops it.
    const res = await postEvent(invoiceEvent({ billing_reason: 'manual' }))

    expect(res.status).toBe(200)
    expect(console.error).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Routing of the checkout.session.* family (A2)
//
// Both async events must reach the operator handlers, and the session's
// payment_status must survive the schema, because the endpoint's event list
// is a runtime setting nobody re-reads: an unrouted event is a silent 200.
// ---------------------------------------------------------------------------

describe('POST /api/webhooks/stripe — checkout.session.* routing', () => {
  const okResponse = () => new Response(JSON.stringify({ ok: true }), { status: 200 })

  beforeEach(() => {
    Object.assign(testEnv, {
      STRIPE_WEBHOOK_SECRET: SECRET,
      STRIPE_API_KEY: 'sk_test_route',
      RESEND_API_KEY: 'resend_route',
    })
    handleOperatorCheckoutCompleted.mockReset().mockResolvedValue(okResponse())
    handleOperatorCheckoutAsyncPaymentFailed.mockReset().mockResolvedValue(okResponse())
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'log').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.restoreAllMocks()
  })

  it('routes completed AND async_payment_succeeded to the completion handler, payment_status intact', async () => {
    for (const [type, status] of [
      ['checkout.session.completed', 'unpaid'],
      ['checkout.session.async_payment_succeeded', 'paid'],
    ] as const) {
      handleOperatorCheckoutCompleted.mockClear()
      const res = await postEvent(checkoutEvent(type, { payment_status: status }))
      expect(res.status).toBe(200)
      expect(handleOperatorCheckoutCompleted).toHaveBeenCalledTimes(1)
      const [, session] = handleOperatorCheckoutCompleted.mock.calls[0] as [
        unknown,
        { payment_status: string; subscription: string },
      ]
      expect(session.payment_status).toBe(status)
      expect(session.subscription).toBe('sub_stripe_1')
    }
    expect(handleOperatorCheckoutAsyncPaymentFailed).not.toHaveBeenCalled()
  })

  it('routes async_payment_failed to the failure handler with the Stripe and Resend keys', async () => {
    const res = await postEvent(checkoutEvent('checkout.session.async_payment_failed'))
    expect(res.status).toBe(200)
    expect(handleOperatorCheckoutCompleted).not.toHaveBeenCalled()
    expect(handleOperatorCheckoutAsyncPaymentFailed).toHaveBeenCalledTimes(1)
    const [, stripeKey, resendKey, session] = handleOperatorCheckoutAsyncPaymentFailed.mock
      .calls[0] as [unknown, string, string, { id: string }]
    expect(stripeKey).toBe('sk_test_route')
    expect(resendKey).toBe('resend_route')
    expect(session.id).toBe('cs_test_route_1')
  })

  it('rejects a session whose payment_status is outside the vocabulary (parse, never cast)', async () => {
    const res = await postEvent(
      checkoutEvent('checkout.session.completed', { payment_status: 'pending' })
    )
    expect(res.status).toBe(400)
    expect(handleOperatorCheckoutCompleted).not.toHaveBeenCalled()
  })

  it('acks async events for a non-operator product without dispatching (the Hosted Agent never emits them)', async () => {
    for (const type of [
      'checkout.session.async_payment_succeeded',
      'checkout.session.async_payment_failed',
    ]) {
      const res = await postEvent(
        checkoutEvent(type, { metadata: { product_slug: 'hosted-agent' } })
      )
      expect(res.status).toBe(200)
    }
    expect(handleOperatorCheckoutCompleted).not.toHaveBeenCalled()
    expect(handleOperatorCheckoutAsyncPaymentFailed).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// The invoice schema retains the subscription-metadata snapshot (A1)
//
// The handler test proves the fallback binds from metadata it is handed; this
// proves the ROUTE hands it over — a schema that dropped the nested position
// would pass every handler test and still skip the live event.
// ---------------------------------------------------------------------------

describe('POST /api/webhooks/stripe — invoice.paid before checkout, through the schema', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-a', 'Org A', 'org-a')
      .run()
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind('entity-a', 'org-a', 'Entity A', 'entity-a')
      .run()
    await db
      .prepare(
        "INSERT INTO subscriptions (id, org_id, entity_id, product_slug, instance_slug, status) VALUES ('sub-op', 'org-a', 'entity-a', 'operator', 'seat-a', 'provisioning')"
      )
      .run()
    Object.assign(testEnv, { STRIPE_WEBHOOK_SECRET: SECRET, DB: db })
    vi.spyOn(console, 'log').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.restoreAllMocks()
  })

  async function rowState() {
    return db
      .prepare('SELECT status, stripe_subscription_id FROM subscriptions WHERE id = ?')
      .bind('sub-op')
      .first<{ status: string; stripe_subscription_id: string | null }>()
  }

  it('the live (2026-03-25.dahlia) shape: parent.subscription_details.metadata binds the row', async () => {
    const res = await postEvent(
      invoiceEvent(
        {
          status: 'paid',
          amount_paid: 500000,
          billing_reason: 'subscription_create',
          status_transitions: { paid_at: 1785000100, finalized_at: 1785000000, voided_at: null },
          parent: {
            type: 'subscription_details',
            subscription_details: {
              subscription: 'sub_stripe_1',
              metadata: { product_slug: 'operator', smd_subscription_id: 'sub-op' },
            },
          },
        },
        'invoice.paid'
      )
    )
    expect(res.status).toBe(200)
    expect(await rowState()).toEqual({ status: 'active', stripe_subscription_id: 'sub_stripe_1' })
  })

  it('the pre-basil shape: top-level subscription + subscription_details.metadata binds too', async () => {
    const res = await postEvent(
      invoiceEvent(
        {
          status: 'paid',
          amount_paid: 500000,
          status_transitions: { paid_at: 1785000100, finalized_at: 1785000000, voided_at: null },
          subscription: 'sub_stripe_1',
          subscription_details: { metadata: { smd_subscription_id: 'sub-op' } },
        },
        'invoice.paid'
      )
    )
    expect(res.status).toBe(200)
    expect(await rowState()).toEqual({ status: 'active', stripe_subscription_id: 'sub_stripe_1' })
  })

  it('invoice.finalized with the same metadata does NOT bind (finalized precedes ACH collection)', async () => {
    const res = await postEvent(
      invoiceEvent({
        parent: {
          type: 'subscription_details',
          subscription_details: {
            subscription: 'sub_stripe_1',
            metadata: { smd_subscription_id: 'sub-op' },
          },
        },
      })
    )
    expect(res.status).toBe(200)
    expect(await rowState()).toEqual({ status: 'provisioning', stripe_subscription_id: null })
  })
})
