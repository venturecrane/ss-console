/**
 * Tests for the Operator retainer Stripe subscription client
 * (src/lib/stripe/subscriptions.ts, #1679).
 *
 * The configured path is exercised through the real fetch layer with a
 * stubbed global fetch, asserting the exact Stripe API shapes: the
 * client-started Checkout Session (subscription mode, on the authored rail:
 * ACH, or card with the recurring 3% fee line; inline monthly price_data
 * against the shared retainer product), pause via
 * pause_collection[behavior]=void, resume via clearing pause_collection,
 * cancel via DELETE.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  cancelOperatorSubscription,
  createOperatorCheckoutSession,
  pauseOperatorSubscription,
  resumeOperatorSubscription,
} from '../src/lib/stripe/subscriptions'

const KEY = 'sk_test_fake'

interface RecordedCall {
  url: string
  method: string
  body: string
}

/** Stub fetch with per-URL-substring responders; records every call. */
function stubStripe(
  responders: Array<{ match: string; method?: string; json: unknown; status?: number }>
): { calls: RecordedCall[] } {
  const calls: RecordedCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? init.body : ''
      calls.push({ url, method, body })
      for (const r of responders) {
        if (url.includes(r.match) && (!r.method || r.method === method)) {
          return new Response(JSON.stringify(r.json), { status: r.status ?? 200 })
        }
      }
      return new Response(JSON.stringify({ error: `unmatched ${method} ${url}` }), { status: 500 })
    })
  )
  return { calls }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createOperatorCheckoutSession (the client starts the retainer)', () => {
  const PARAMS = {
    customer_email: 'admin@firm.com',
    monthly_amount_cents: 500000,
    payment_method: 'ach' as const,
    entity_id: 'ent-1',
    subscription_row_id: 'sub-row-1',
    user_id: 'user-1',
    success_url:
      'https://portal.example/portal/billing?start=done&session_id={CHECKOUT_SESSION_ID}',
    cancel_url: 'https://portal.example/portal/billing?start=cancelled',
  }

  it('dev-mode stub when apiKey is undefined (no fetch)', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const result = await createOperatorCheckoutSession(undefined, PARAMS)
    expect(result.id).toMatch(/^dev_cs_/)
    expect(result.url).toBe('#dev-mode')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('creates a subscription-mode session: ACH + card, monthly inline price, routed by metadata', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', json: { data: [{ id: 'prod_retainer' }] } },
      {
        match: '/checkout/sessions',
        method: 'POST',
        json: { id: 'cs_1', url: 'https://checkout.stripe.com/c/cs_1' },
      },
    ])
    const result = await createOperatorCheckoutSession(KEY, PARAMS)
    expect(result).toEqual({ id: 'cs_1', url: 'https://checkout.stripe.com/c/cs_1' })

    const create = calls.find((c) => c.url.endsWith('/checkout/sessions') && c.method === 'POST')
    const body = new URLSearchParams(create!.body)
    expect(body.get('mode')).toBe('subscription')
    expect(body.getAll('payment_method_types[]')).toEqual(['us_bank_account'])
    expect(body.get('payment_method_options[us_bank_account][verification_method]')).toBe(
      'automatic'
    )
    expect(body.get('line_items[0][price_data][unit_amount]')).toBe('500000')
    expect(body.get('line_items[0][price_data][product]')).toBe('prod_retainer')
    expect(body.get('line_items[0][price_data][recurring][interval]')).toBe('month')
    expect(body.get('customer_email')).toBe('admin@firm.com')
    expect(body.get('client_reference_id')).toBe('user-1')
    expect(body.get('metadata[product_slug]')).toBe('operator')
    expect(body.get('metadata[smd_subscription_id]')).toBe('sub-row-1')
    expect(body.get('subscription_data[metadata][smd_subscription_id]')).toBe('sub-row-1')
    expect(body.get('success_url')).toContain('{CHECKOUT_SESSION_ID}')
    // Never the admin-started shape: no collection_method, no anchor, no tax.
    expect(body.has('collection_method')).toBe(false)
    expect(body.has('billing_cycle_anchor')).toBe(false)
    expect(body.has('automatic_tax[enabled]')).toBe(false)
  })

  it('card rail: card only, plus the recurring 3% fee line on its own shared product (agreement §3.8)', async () => {
    // 2026-09-10: the first Operator client cannot pay from the firm's bank
    // account. The fee is a second recurring line, not a raised price, so
    // Checkout and every monthly invoice read "Card processing fee (3%)"
    // as its own figure, the same phrase the one-time invoices carry.
    const { calls } = stubStripe([
      {
        match: "metadata%5B'smd_product'%5D%3A'operator-retainer'",
        json: { data: [{ id: 'prod_retainer' }] },
      },
      {
        match: "metadata%5B'smd_product'%5D%3A'operator-card-fee'",
        json: { data: [{ id: 'prod_fee' }] },
      },
      {
        match: '/checkout/sessions',
        method: 'POST',
        json: { id: 'cs_3', url: 'https://checkout.stripe.com/c/cs_3' },
      },
    ])
    await createOperatorCheckoutSession(KEY, { ...PARAMS, payment_method: 'card' })
    const create = calls.find((c) => c.url.endsWith('/checkout/sessions') && c.method === 'POST')
    const body = new URLSearchParams(create!.body)
    expect(body.getAll('payment_method_types[]')).toEqual(['card'])
    expect(body.has('payment_method_options[us_bank_account][verification_method]')).toBe(false)
    expect(body.get('line_items[0][price_data][unit_amount]')).toBe('500000')
    expect(body.get('line_items[0][price_data][product]')).toBe('prod_retainer')
    expect(body.get('line_items[1][price_data][unit_amount]')).toBe('15000')
    expect(body.get('line_items[1][price_data][product]')).toBe('prod_fee')
    expect(body.get('line_items[1][price_data][recurring][interval]')).toBe('month')
    expect(body.get('line_items[1][quantity]')).toBe('1')
  })

  it('card rail: creates the fee product named by the invoice fee line on first use', async () => {
    const { calls } = stubStripe([
      {
        match: "metadata%5B'smd_product'%5D%3A'operator-retainer'",
        json: { data: [{ id: 'prod_retainer' }] },
      },
      { match: "metadata%5B'smd_product'%5D%3A'operator-card-fee'", json: { data: [] } },
      { match: '/products', method: 'POST', json: { id: 'prod_fee_new' } },
      {
        match: '/checkout/sessions',
        method: 'POST',
        json: { id: 'cs_4', url: 'https://checkout.stripe.com/c/cs_4' },
      },
    ])
    await createOperatorCheckoutSession(KEY, { ...PARAMS, payment_method: 'card' })
    const productCreate = calls.find((c) => c.url.endsWith('/products') && c.method === 'POST')
    const productBody = new URLSearchParams(productCreate!.body)
    expect(productBody.get('name')).toBe('Card processing fee (3%)')
    expect(productBody.get('metadata[smd_product]')).toBe('operator-card-fee')
    const create = calls.find((c) => c.url.endsWith('/checkout/sessions'))
    expect(new URLSearchParams(create!.body).get('line_items[1][price_data][product]')).toBe(
      'prod_fee_new'
    )
  })

  it('ACH rail: no fee line, no card', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', json: { data: [{ id: 'prod_retainer' }] } },
      {
        match: '/checkout/sessions',
        method: 'POST',
        json: { id: 'cs_5', url: 'https://checkout.stripe.com/c/cs_5' },
      },
    ])
    await createOperatorCheckoutSession(KEY, PARAMS)
    const create = calls.find((c) => c.url.endsWith('/checkout/sessions') && c.method === 'POST')
    const body = new URLSearchParams(create!.body)
    expect(body.getAll('payment_method_types[]')).toEqual(['us_bank_account'])
    expect(body.has('line_items[1][price_data][unit_amount]')).toBe(false)
  })

  it('creates the shared retainer product on first use', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', json: { data: [] } },
      { match: '/products', method: 'POST', json: { id: 'prod_new' } },
      {
        match: '/checkout/sessions',
        method: 'POST',
        json: { id: 'cs_2', url: 'https://checkout.stripe.com/c/cs_2' },
      },
    ])
    await createOperatorCheckoutSession(KEY, PARAMS)
    const productCreate = calls.find((c) => c.url.endsWith('/products') && c.method === 'POST')
    expect(productCreate).toBeDefined()
    const create = calls.find((c) => c.url.endsWith('/checkout/sessions'))
    expect(new URLSearchParams(create!.body).get('line_items[0][price_data][product]')).toBe(
      'prod_new'
    )
  })

  it('throws on a Stripe error (caller decides the redirect)', async () => {
    stubStripe([
      { match: '/products/search', json: { data: [{ id: 'prod_retainer' }] } },
      { match: '/checkout/sessions', method: 'POST', json: { error: 'nope' }, status: 400 },
    ])
    await expect(createOperatorCheckoutSession(KEY, PARAMS)).rejects.toThrow(
      /checkout session creation failed 400/
    )
  })
})

describe('pause / resume / cancel', () => {
  it('pauses with pause_collection[behavior]=void', async () => {
    const { calls } = stubStripe([
      { match: '/subscriptions/sub_9', method: 'POST', json: { id: 'sub_9', status: 'active' } },
    ])
    await pauseOperatorSubscription(KEY, 'sub_9')
    expect(new URLSearchParams(calls[0].body).get('pause_collection[behavior]')).toBe('void')
  })

  it('resumes by clearing pause_collection', async () => {
    const { calls } = stubStripe([
      { match: '/subscriptions/sub_9', method: 'POST', json: { id: 'sub_9', status: 'active' } },
    ])
    await resumeOperatorSubscription(KEY, 'sub_9')
    expect(calls[0].url.endsWith('/subscriptions/sub_9')).toBe(true)
    expect(new URLSearchParams(calls[0].body).get('pause_collection')).toBe('')
  })

  it('cancels via DELETE', async () => {
    const { calls } = stubStripe([
      {
        match: '/subscriptions/sub_9',
        method: 'DELETE',
        json: { id: 'sub_9', status: 'canceled' },
      },
    ])
    const result = await cancelOperatorSubscription(KEY, 'sub_9')
    expect(result.status).toBe('canceled')
    expect(calls[0].method).toBe('DELETE')
  })
})
