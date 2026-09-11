/**
 * Behavioural tests for the Hosted Agent Stripe Checkout client
 * (src/lib/stripe/checkout.ts, ADR 0067).
 *
 * Three production routes import this module and every test that reached it
 * mocked it away (2026-09-10 code review, Testing 4). These exercise the real
 * module through a stubbed global fetch and assert the exact Stripe wire
 * shapes: the product resolved (or created) by its metadata marker and
 * self-healed onto the SaaS tax code, the founding coupon resolved by its
 * FIXED id (Stripe's idempotent identity for coupons: the module sends no
 * Idempotency-Key header and relies on that id, which is asserted here so a
 * change to that design goes red), the subscription-mode session with
 * exclusive tax, and the full-price retry when the coupon is rejected.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  createBillingPortalSession,
  createHostedAgentCheckoutSession,
  getHostedAgentCheckoutSession,
} from '../src/lib/stripe/checkout'

const KEY = 'sk_test_fake'

interface RecordedCall {
  url: string
  method: string
  body: string
  headers: Record<string, string>
}

type Responder = {
  match: string
  method?: string
  /** A function may answer differently per call (e.g. fail once, then succeed). */
  reply: object | ((call: RecordedCall, nth: number) => { json: unknown; status?: number })
  status?: number
}

/** Stub fetch with per-URL-substring responders; records every call. */
function stubStripe(responders: Responder[]): { calls: RecordedCall[] } {
  const calls: RecordedCall[] = []
  const seen = new Map<Responder, number>()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? init.body : ''
      const headers = Object.fromEntries(
        Object.entries((init?.headers as Record<string, string> | undefined) ?? {})
      )
      const call = { url, method, body, headers }
      calls.push(call)
      for (const r of responders) {
        if (url.includes(r.match) && (!r.method || r.method === method)) {
          const nth = (seen.get(r) ?? 0) + 1
          seen.set(r, nth)
          if (typeof r.reply === 'function') {
            const out = (
              r.reply as (c: RecordedCall, n: number) => { json: unknown; status?: number }
            )(call, nth)
            return new Response(JSON.stringify(out.json), { status: out.status ?? 200 })
          }
          return new Response(JSON.stringify(r.reply), { status: r.status ?? 200 })
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

const PARAMS = {
  customer_email: 'buyer@example.com',
  clerk_user_id: 'user_123',
  success_url: 'https://smd.services/hosted-agent/thanks?session_id={CHECKOUT_SESSION_ID}',
  cancel_url: 'https://smd.services/hosted-agent',
}

const VALID_COUPON = { valid: true, times_redeemed: 3, max_redemptions: 25 }
const TAXED_PRODUCT = { id: 'prod_ha', tax_code: 'txcd_10103000' }

function session(id: string) {
  return { id, url: `https://checkout.stripe.com/c/${id}` }
}

function postBodies(calls: RecordedCall[], suffix: string): URLSearchParams[] {
  return calls
    .filter((c) => c.method === 'POST' && c.url.endsWith(suffix))
    .map((c) => new URLSearchParams(c.body))
}

describe('createHostedAgentCheckoutSession', () => {
  it('dev mode: no key, no fetch, a dev session id', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const result = await createHostedAgentCheckoutSession(undefined, PARAMS)
    expect(result.id).toMatch(/^dev_cs_/)
    expect(result.url).toBe('#dev-mode')
    expect(result.founding).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('founding seat: subscription mode, exclusive tax, the fixed coupon, and no idempotency header', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [TAXED_PRODUCT] } },
      { match: '/coupons/hosted-agent-founding', method: 'GET', reply: VALID_COUPON },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_founding') },
    ])
    const result = await createHostedAgentCheckoutSession(KEY, PARAMS)
    expect(result).toEqual({ ...session('cs_founding'), founding: true })

    const [body] = postBodies(calls, '/checkout/sessions')
    expect(body.get('mode')).toBe('subscription')
    expect(body.get('line_items[0][quantity]')).toBe('1')
    expect(body.get('line_items[0][price_data][unit_amount]')).toBe('7900')
    expect(body.get('line_items[0][price_data][currency]')).toBe('usd')
    expect(body.get('line_items[0][price_data][product]')).toBe('prod_ha')
    expect(body.get('line_items[0][price_data][recurring][interval]')).toBe('month')
    expect(body.get('line_items[0][price_data][tax_behavior]')).toBe('exclusive')
    expect(body.get('automatic_tax[enabled]')).toBe('true')
    expect(body.get('success_url')).toBe(PARAMS.success_url)
    expect(body.get('cancel_url')).toBe(PARAMS.cancel_url)
    expect(body.get('client_reference_id')).toBe('user_123')
    expect(body.get('customer_email')).toBe('buyer@example.com')
    expect(body.get('metadata[product_slug]')).toBe('hosted-agent')
    expect(body.get('subscription_data[metadata][product_slug]')).toBe('hosted-agent')
    expect(body.get('subscription_data[metadata][smd_clerk_user_id]')).toBe('user_123')
    expect(body.get('discounts[0][coupon]')).toBe('hosted-agent-founding')
    // Never the retainer's invoiced shape.
    expect(body.has('collection_method')).toBe(false)
    expect(body.has('payment_method_types[]')).toBe(false)

    // Idempotency rests on the coupon's fixed id, not on a request header.
    const create = calls.find((c) => c.method === 'POST' && c.url.endsWith('/checkout/sessions'))
    expect(Object.keys(create!.headers).map((h) => h.toLowerCase())).not.toContain(
      'idempotency-key'
    )
    expect(create!.headers.Authorization).toBe(`Bearer ${KEY}`)
    expect(create!.headers['Content-Type']).toBe('application/x-www-form-urlencoded')
    // The product is found by its metadata marker, never by name.
    const search = calls.find((c) => c.url.includes('/products/search'))
    expect(decodeURIComponent(search!.url)).toContain("metadata['smd_product']:'hosted-agent'")
  })

  it('full price when the coupon has hit its cap: no discount, founding false', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [TAXED_PRODUCT] } },
      {
        match: '/coupons/hosted-agent-founding',
        method: 'GET',
        reply: { valid: true, times_redeemed: 25, max_redemptions: 25 },
      },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_full') },
    ])
    const result = await createHostedAgentCheckoutSession(KEY, PARAMS)
    expect(result.founding).toBe(false)
    const [body] = postBodies(calls, '/checkout/sessions')
    expect(body.has('discounts[0][coupon]')).toBe(false)
    expect(body.get('line_items[0][price_data][unit_amount]')).toBe('7900')
  })

  it('retries at full price when Stripe rejects the founding session', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [TAXED_PRODUCT] } },
      { match: '/coupons/hosted-agent-founding', method: 'GET', reply: VALID_COUPON },
      {
        match: '/checkout/sessions',
        method: 'POST',
        reply: (_call, nth) =>
          nth === 1
            ? { json: { error: { message: 'coupon exhausted' } }, status: 400 }
            : { json: session('cs_retry') },
      },
    ])
    const result = await createHostedAgentCheckoutSession(KEY, PARAMS)
    expect(result).toEqual({ ...session('cs_retry'), founding: false })
    const bodies = postBodies(calls, '/checkout/sessions')
    expect(bodies).toHaveLength(2)
    expect(bodies[0].get('discounts[0][coupon]')).toBe('hosted-agent-founding')
    expect(bodies[1].has('discounts[0][coupon]')).toBe(false)
  })

  it('creates the coupon by its fixed id when it does not exist yet', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [TAXED_PRODUCT] } },
      {
        match: '/coupons/hosted-agent-founding',
        method: 'GET',
        reply: { error: 'no' },
        status: 404,
      },
      { match: '/coupons', method: 'POST', reply: { id: 'hosted-agent-founding' } },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_new') },
    ])
    const result = await createHostedAgentCheckoutSession(KEY, PARAMS)
    expect(result.founding).toBe(true)
    const [coupon] = postBodies(calls, '/coupons')
    expect(coupon.get('id')).toBe('hosted-agent-founding')
    expect(coupon.get('amount_off')).toBe('3000')
    expect(coupon.get('currency')).toBe('usd')
    expect(coupon.get('duration')).toBe('forever')
    expect(coupon.get('max_redemptions')).toBe('25')
  })

  it('a lost coupon-create race re-reads once rather than losing the sale', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [TAXED_PRODUCT] } },
      {
        match: '/coupons/hosted-agent-founding',
        method: 'GET',
        reply: (_call, nth) =>
          nth === 1 ? { json: { error: 'no' }, status: 404 } : { json: VALID_COUPON },
      },
      { match: '/coupons', method: 'POST', reply: { error: 'already exists' }, status: 400 },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_raced') },
    ])
    const result = await createHostedAgentCheckoutSession(KEY, PARAMS)
    expect(result.founding).toBe(true)
    expect(calls.filter((c) => c.url.endsWith('/coupons/hosted-agent-founding'))).toHaveLength(2)
  })

  it('creates the shared product by marker with the SaaS tax code when the search is empty', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [] } },
      { match: '/products', method: 'POST', reply: { id: 'prod_created' } },
      { match: '/coupons/hosted-agent-founding', method: 'GET', reply: VALID_COUPON },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_x') },
    ])
    await createHostedAgentCheckoutSession(KEY, PARAMS)
    const [product] = postBodies(calls, '/products')
    expect(product.get('name')).toBe('SMD Hosted Agent')
    expect(product.get('metadata[smd_product]')).toBe('hosted-agent')
    expect(product.get('tax_code')).toBe('txcd_10103000')
    const [body] = postBodies(calls, '/checkout/sessions')
    expect(body.get('line_items[0][price_data][product]')).toBe('prod_created')
  })

  it('self-heals a product that predates tax wiring onto the SaaS tax code', async () => {
    const { calls } = stubStripe([
      { match: '/products/search', reply: { data: [{ id: 'prod_old', tax_code: null }] } },
      { match: '/products/prod_old', method: 'POST', reply: { id: 'prod_old' } },
      { match: '/coupons/hosted-agent-founding', method: 'GET', reply: VALID_COUPON },
      { match: '/checkout/sessions', method: 'POST', reply: session('cs_y') },
    ])
    await createHostedAgentCheckoutSession(KEY, PARAMS)
    const [patch] = postBodies(calls, '/products/prod_old')
    expect(patch.get('tax_code')).toBe('txcd_10103000')
  })

  it("surfaces a product-creation failure with Stripe's status", async () => {
    stubStripe([
      { match: '/products/search', reply: { data: [] } },
      { match: '/products', method: 'POST', reply: { error: 'nope' }, status: 402 },
    ])
    await expect(createHostedAgentCheckoutSession(KEY, PARAMS)).rejects.toThrow(
      /Stripe product creation failed 402/
    )
  })
})

describe('createBillingPortalSession', () => {
  it('dev mode returns the return URL without a fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    expect(await createBillingPortalSession(undefined, 'cus_1', 'https://portal/return')).toBe(
      'https://portal/return'
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('posts the customer and return_url and returns the portal URL', async () => {
    const { calls } = stubStripe([
      {
        match: '/billing_portal/sessions',
        method: 'POST',
        reply: { url: 'https://billing.stripe.com/p/session/x' },
      },
    ])
    const url = await createBillingPortalSession(KEY, 'cus_1', 'https://portal/return')
    expect(url).toBe('https://billing.stripe.com/p/session/x')
    const [body] = postBodies(calls, '/billing_portal/sessions')
    expect(body.get('customer')).toBe('cus_1')
    expect(body.get('return_url')).toBe('https://portal/return')
  })

  it('throws with the status when Stripe refuses', async () => {
    stubStripe([{ match: '/billing_portal/sessions', method: 'POST', reply: {}, status: 400 }])
    await expect(createBillingPortalSession(KEY, 'cus_1', 'https://portal/return')).rejects.toThrow(
      /billing portal session failed 400/
    )
  })
})

describe('getHostedAgentCheckoutSession', () => {
  it('dev mode returns a paid, complete view', async () => {
    vi.stubGlobal('fetch', vi.fn())
    expect(await getHostedAgentCheckoutSession(undefined, 'cs_dev')).toEqual({
      id: 'cs_dev',
      status: 'complete',
      payment_status: 'paid',
      customer_email: null,
    })
  })

  it('returns only the thanks-page view, never payment details', async () => {
    const { calls } = stubStripe([
      {
        match: '/checkout/sessions/cs_1',
        method: 'GET',
        reply: {
          id: 'cs_1',
          status: 'complete',
          payment_status: 'paid',
          customer_details: { email: 'buyer@example.com', address: { line1: 'never surfaced' } },
          payment_intent: 'pi_secret',
        },
      },
    ])
    const view = await getHostedAgentCheckoutSession(KEY, 'cs_1')
    expect(view).toEqual({
      id: 'cs_1',
      status: 'complete',
      payment_status: 'paid',
      customer_email: 'buyer@example.com',
    })
    expect(calls[0].headers.Authorization).toBe(`Bearer ${KEY}`)
  })

  it('a session with no customer details reads as email null', async () => {
    stubStripe([
      {
        match: '/checkout/sessions/cs_2',
        method: 'GET',
        reply: { id: 'cs_2', status: 'open', payment_status: 'unpaid', customer_details: null },
      },
    ])
    expect((await getHostedAgentCheckoutSession(KEY, 'cs_2')).customer_email).toBeNull()
  })

  it('throws with the status when the session cannot be read', async () => {
    stubStripe([{ match: '/checkout/sessions/cs_3', method: 'GET', reply: {}, status: 404 }])
    await expect(getHostedAgentCheckoutSession(KEY, 'cs_3')).rejects.toThrow(
      /checkout session get failed 404/
    )
  })
})
