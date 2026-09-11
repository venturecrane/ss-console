/**
 * The Billing surface's checkout return (claims review 2026-09-04, A5).
 *
 * `?start=done` used to render "Your subscription is active" from the query
 * string alone. The sentence now comes from two facts: the Checkout Session
 * Stripe substituted into the success URL (`getOperatorCheckoutSession`,
 * parsed, never cast) and the client's own subscription row
 * (`resolveStartDoneMessage`). Every combination the two facts do not
 * settle renders nothing.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { getOperatorCheckoutSession } from '../src/lib/stripe/subscriptions'
import { resolveStartDoneMessage } from '../src/lib/portal/billing'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubSession(json: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(json), { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('getOperatorCheckoutSession (the success URL names the session, Stripe names the row)', () => {
  it('reads payment_status and the stamped console row id', async () => {
    const fetchMock = stubSession({
      id: 'cs_test_1',
      payment_status: 'paid',
      metadata: { product_slug: 'operator', smd_subscription_id: 'sub-row-1' },
    })
    const view = await getOperatorCheckoutSession('sk_test_fake', 'cs_test_1')
    expect(view).toEqual({
      id: 'cs_test_1',
      payment_status: 'paid',
      smd_subscription_id: 'sub-row-1',
    })
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      'https://api.stripe.com/v1/checkout/sessions/cs_test_1'
    )
  })

  it('a session with no stamped row id resolves to null, never to a guess', async () => {
    stubSession({ id: 'cs_test_2', payment_status: 'unpaid', metadata: {} })
    const view = await getOperatorCheckoutSession('sk_test_fake', 'cs_test_2')
    expect(view.smd_subscription_id).toBeNull()
    expect(view.payment_status).toBe('unpaid')
  })

  it('an unknown payment_status is an error, not a display state', async () => {
    stubSession({ id: 'cs_test_3', payment_status: 'settled', metadata: {} })
    await expect(getOperatorCheckoutSession('sk_test_fake', 'cs_test_3')).rejects.toThrow(
      /unexpected shape/
    )
  })

  it('a Stripe error surfaces as a throw (the page then renders nothing)', async () => {
    stubSession({ error: { message: 'No such checkout.session' } }, 404)
    await expect(getOperatorCheckoutSession('sk_test_fake', 'cs_missing')).rejects.toThrow(/404/)
  })

  it('dev mode never fetches and carries no row id', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const view = await getOperatorCheckoutSession(undefined, 'dev_cs_1')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(view.smd_subscription_id).toBeNull()
  })
})

describe('resolveStartDoneMessage (row + session, never the query string)', () => {
  it('paid and active: the row date, nothing else', () => {
    expect(resolveStartDoneMessage('paid', { status: 'active' }, 'Sep 4, 2026')).toBe(
      'Your subscription started on Sep 4, 2026.'
    )
  })

  it('paid and active with no formattable date renders nothing', () => {
    expect(resolveStartDoneMessage('paid', { status: 'active' }, '')).toBeNull()
  })

  it('unpaid (ACH still settling) says the page catches up, whatever the row says', () => {
    const pending =
      'Checkout is complete. This page shows your subscription as active once the payment settles.'
    expect(resolveStartDoneMessage('unpaid', { status: 'provisioning' }, '')).toBe(pending)
    expect(resolveStartDoneMessage('unpaid', { status: 'active' }, 'Sep 4, 2026')).toBe(pending)
  })

  it('paid but the webhook has not landed (row still provisioning) is the same pending sentence', () => {
    expect(resolveStartDoneMessage('paid', { status: 'provisioning' }, '')).toMatch(
      /^Checkout is complete\./
    )
  })

  it('every other combination renders nothing', () => {
    expect(resolveStartDoneMessage('no_payment_required', { status: 'active' }, 'x')).toBeNull()
    expect(resolveStartDoneMessage('paid', { status: 'paused' }, 'x')).toBeNull()
    expect(resolveStartDoneMessage('paid', { status: 'cancelled' }, 'x')).toBeNull()
  })

  it('never states the subscription is active', () => {
    for (const ps of ['paid', 'unpaid', 'no_payment_required'] as const) {
      for (const status of ['provisioning', 'active', 'paused']) {
        const msg = resolveStartDoneMessage(ps, { status }, 'Sep 4, 2026') ?? ''
        expect(msg).not.toMatch(/subscription is active/i)
      }
    }
  })
})
