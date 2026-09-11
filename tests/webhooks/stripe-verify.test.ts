/**
 * Behavioral tests for the Stripe webhook signature verification path.
 *
 * Stripe-Signature header format: `t=<timestamp>,v1=<hex_signature>`
 * Signed payload: `<timestamp>.<rawBody>`
 * Signature: HMAC-SHA256(webhook_secret, signed_payload).hex()
 * Tolerance: 5 minutes between header timestamp and server clock.
 *
 * The verifier is file-private to src/pages/api/webhooks/stripe.ts so we
 * exercise it through the route. We use an event type the route does NOT
 * dispatch (anything that isn't `invoice.paid` / `invoice.payment_failed`)
 * so a successful verification returns 200 without touching D1 — this
 * keeps the test focused on the crypto boundary.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { installWorkerdPolyfills } from '@venturecrane/crane-test-harness'
import { env as testEnv } from 'cloudflare:workers'
import { POST } from '../../src/pages/api/webhooks/stripe'

installWorkerdPolyfills()

const SECRET = 'whsec_test_stripe_webhook_secret'
const WRONG_SECRET = 'whsec_test_totally_different'

// ---------------------------------------------------------------------------
// Stripe HMAC helper — must match the verifier:
//   signed_payload = `${timestamp}.${rawBody}`
//   v1 = HMAC_SHA256(secret, signed_payload).hex()
// ---------------------------------------------------------------------------

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

function buildStripeHeader(timestamp: number, signature: string): string {
  return `t=${timestamp},v1=${signature}`
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000)
}

// ---------------------------------------------------------------------------
// Test event — non-dispatched type so success returns 200 with ack only.
// ---------------------------------------------------------------------------

const SAMPLE_EVENT = {
  id: 'evt_test_123',
  object: 'event',
  type: 'customer.subscription.created',
  data: { object: {} },
  created: 0,
}

interface BuildOpts {
  body?: string
  signatureHeader?: string | null
}

async function parseJson<T>(res: Response): Promise<T> {
  return res.json()
}

function buildContext(opts: BuildOpts) {
  const body = opts.body ?? JSON.stringify(SAMPLE_EVENT)
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (opts.signatureHeader !== null && opts.signatureHeader !== undefined) {
    headers.set('stripe-signature', opts.signatureHeader)
  }
  const request = new Request('http://test.local/api/webhooks/stripe', {
    method: 'POST',
    headers,
    body,
  })
  return {
    request,
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof POST>[0]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('POST /api/webhooks/stripe — signature verification', () => {
  beforeEach(() => {
    Object.assign(testEnv, { STRIPE_WEBHOOK_SECRET: SECRET })
    // Suppress expected console.error noise on the negative paths.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.restoreAllMocks()
  })

  it('accepts a request with a valid signature + fresh timestamp', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, SECRET)
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(ts, sig) }))
    expect(res.status).toBe(200)
  })

  it('rejects a request whose signature has been tampered with (single bit flip)', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, SECRET)
    // Flip one hex char of the signature
    const flipped = sig.slice(0, -1) + (sig.slice(-1) === '0' ? '1' : '0')
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(ts, flipped) }))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a request signed with the wrong secret', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, WRONG_SECRET)
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(ts, sig) }))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a request with no Stripe-Signature header', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const res = await POST(buildContext({ body, signatureHeader: null }))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a request whose timestamp is more than 5 minutes old', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const staleTs = nowSeconds() - 6 * 60
    // Sign with the correct secret + stale timestamp so we know the freshness
    // check is what's rejecting (not the HMAC).
    const sig = await computeStripeV1(staleTs, body, SECRET)
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(staleTs, sig) }))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a request where the body has been mutated after signing', async () => {
    const body = JSON.stringify(SAMPLE_EVENT)
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, SECRET)
    // Mutate body to simulate an attacker swapping the payload while
    // re-using a captured signature.
    const tampered = body.replace('customer.subscription.created', 'invoice.paid')
    const res = await POST(
      buildContext({ body: tampered, signatureHeader: buildStripeHeader(ts, sig) })
    )
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('returns 500 when the webhook secret is not configured', async () => {
    delete (testEnv as unknown as Record<string, unknown>).STRIPE_WEBHOOK_SECRET
    const body = JSON.stringify(SAMPLE_EVENT)
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, SECRET)
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(ts, sig) }))
    expect(res.status).toBe(500)
  })

  it('rejects a signed invoice event with a malformed invoice object', async () => {
    const body = JSON.stringify({
      id: 'evt_bad_invoice',
      object: 'event',
      type: 'invoice.paid',
      data: { object: { object: 'invoice' } },
      created: nowSeconds(),
    })
    const ts = nowSeconds()
    const sig = await computeStripeV1(ts, body, SECRET)
    const res = await POST(buildContext({ body, signatureHeader: buildStripeHeader(ts, sig) }))

    expect(res.status).toBe(400)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('validation_failed')
  })
})
