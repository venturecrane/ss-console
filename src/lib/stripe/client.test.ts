/**
 * Behavioral tests for the fetch-based Stripe client.
 *
 * Replaces the source-mirror describe block 'invoices: stripe client' that
 * previously lived in tests/invoices.test.ts. The client talks to
 * https://api.stripe.com/v1 with form-encoded bodies; here fetch is mocked
 * and the tests assert the request shapes (endpoint URLs, form encoding,
 * auth header) and response parsing — never live calls.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createStripeInvoice,
  finalizeStripeInvoice,
  getStripeInvoice,
  sendStripeInvoice,
  voidStripeInvoice,
} from './client'
import type { StripeCreateInvoiceParams } from './types'

const API = 'https://api.stripe.com/v1'
const KEY = 'sk_test_key_123'

type FetchCall = { url: string; init: RequestInit | undefined }

let calls: FetchCall[]
let responses: Response[]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function queue(...res: Response[]): void {
  responses.push(...res)
}

function bodyParams(call: FetchCall): URLSearchParams {
  return new URLSearchParams(String(call.init?.body ?? ''))
}

function headersOf(call: FetchCall): Record<string, string> {
  return (call.init?.headers ?? {}) as Record<string, string>
}

beforeEach(() => {
  calls = []
  responses = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      const next = responses.shift()
      if (!next) throw new Error(`Unexpected fetch call: ${String(url)}`)
      return next
    })
  )
  vi.spyOn(console, 'log').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function baseParams(overrides?: Partial<StripeCreateInvoiceParams>): StripeCreateInvoiceParams {
  return {
    customer_email: 'owner@example.com',
    line_items: [{ amount: 350000, currency: 'usd', description: 'Deposit', quantity: 1 }],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// createStripeInvoice
// ---------------------------------------------------------------------------

describe('createStripeInvoice', () => {
  it('reuses an existing customer found via /customers/search', async () => {
    queue(
      json({ data: [{ id: 'cus_existing' }] }),
      json({ id: 'in_1', hosted_invoice_url: 'https://pay.stripe.com/in_1', status: 'draft' }),
      json({ id: 'ii_1' })
    )

    const result = await createStripeInvoice(KEY, baseParams())

    expect(calls).toHaveLength(3)
    expect(calls[0].url).toBe(
      `${API}/customers/search?query=email:'${encodeURIComponent('owner@example.com')}'`
    )
    expect(calls[0].init?.method).toBe('GET')
    expect(headersOf(calls[0]).Authorization).toBe(`Bearer ${KEY}`)
    // No POST /customers — the search hit short-circuits creation.
    expect(calls.some((c) => c.url === `${API}/customers`)).toBe(false)

    const invoiceBody = bodyParams(calls[1])
    expect(calls[1].url).toBe(`${API}/invoices`)
    expect(invoiceBody.get('customer')).toBe('cus_existing')

    expect(result).toEqual({
      id: 'in_1',
      hosted_invoice_url: 'https://pay.stripe.com/in_1',
      status: 'draft',
    })
  })

  it('creates a customer when the search returns no match', async () => {
    queue(
      json({ data: [] }),
      json({ id: 'cus_new' }),
      json({ id: 'in_2', hosted_invoice_url: 'https://pay.stripe.com/in_2', status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(KEY, baseParams())

    expect(calls[1].url).toBe(`${API}/customers`)
    expect(calls[1].init?.method).toBe('POST')
    expect(bodyParams(calls[1]).get('email')).toBe('owner@example.com')
    expect(bodyParams(calls[2]).get('customer')).toBe('cus_new')
  })

  it('falls back to customer creation when the search request itself fails', async () => {
    queue(
      json({ error: 'rate limited' }, 429),
      json({ id: 'cus_fallback' }),
      json({ id: 'in_3', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(KEY, baseParams())

    expect(calls[1].url).toBe(`${API}/customers`)
    expect(bodyParams(calls[2]).get('customer')).toBe('cus_fallback')
  })

  it('form-encodes the invoice with defaults: send_invoice collection and 15 days until due', async () => {
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_4', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(KEY, baseParams())

    const invoiceCall = calls[1]
    expect(headersOf(invoiceCall)['Content-Type']).toBe('application/x-www-form-urlencoded')
    expect(headersOf(invoiceCall).Authorization).toBe(`Bearer ${KEY}`)
    const body = bodyParams(invoiceCall)
    expect(body.get('collection_method')).toBe('send_invoice')
    expect(body.get('days_until_due')).toBe('15')
    expect(body.get('description')).toBeNull()
  })

  it('encodes description, metadata, payment_method_types, and explicit due days', async () => {
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_5', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(
      KEY,
      baseParams({
        description: 'Deposit - engagement',
        days_until_due: 3,
        collection_method: 'send_invoice',
        metadata: { invoice_id: 'inv-1', engagement_id: 'eng-1' },
        payment_settings: { payment_method_types: ['ach_debit', 'card'] },
      })
    )

    const body = bodyParams(calls[1])
    expect(body.get('description')).toBe('Deposit - engagement')
    expect(body.get('days_until_due')).toBe('3')
    expect(body.get('metadata[invoice_id]')).toBe('inv-1')
    expect(body.get('metadata[engagement_id]')).toBe('eng-1')
    expect(body.getAll('payment_settings[payment_method_types][]')).toEqual(['ach_debit', 'card'])
  })

  it('sends an exact due_date instead of days_until_due when one is given', async () => {
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_7', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(KEY, baseParams({ due_date: 1789023599, days_until_due: 30 }))

    const body = bodyParams(calls[1])
    expect(body.get('due_date')).toBe('1789023599')
    expect(body.get('days_until_due')).toBeNull()
  })

  it('posts one /invoiceitems request per line item with amount, currency, and description', async () => {
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_6', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' }),
      json({ id: 'ii_2' })
    )

    await createStripeInvoice(
      KEY,
      baseParams({
        line_items: [
          { amount: 100000, currency: 'usd', description: 'Phase one', quantity: 1 },
          { amount: 50000, currency: 'usd', description: 'Phase two', quantity: 1 },
        ],
      })
    )

    const itemCalls = calls.filter((c) => c.url === `${API}/invoiceitems`)
    expect(itemCalls).toHaveLength(2)

    const first = bodyParams(itemCalls[0])
    expect(first.get('customer')).toBe('cus_1')
    expect(first.get('invoice')).toBe('in_6')
    expect(first.get('amount')).toBe('100000')
    expect(first.get('currency')).toBe('usd')
    expect(first.get('description')).toBe('Phase one')

    const second = bodyParams(itemCalls[1])
    expect(second.get('amount')).toBe('50000')
    expect(second.get('description')).toBe('Phase two')
  })

  it('transmits the line TOTAL (amount × quantity) on each invoiceitem', async () => {
    // Fixes the latent underbilling bug (#1354): StripeInvoiceLineItem.amount
    // is per-unit, but Stripe's invoiceitem `amount` is the line total
    // (amount = unit_amount × quantity). The encoding multiplies; it does NOT
    // send `quantity`/`unit_amount`, because unit_amount was removed from
    // invoiceitems create in API version 2025-03-31.basil and this client
    // pins no Stripe-Version header.
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_7', hosted_invoice_url: null, status: 'draft' }),
      json({ id: 'ii_1' })
    )

    await createStripeInvoice(
      KEY,
      baseParams({
        line_items: [{ amount: 100000, currency: 'usd', description: 'Two units', quantity: 2 }],
      })
    )

    const itemBody = bodyParams(calls[2])
    expect(itemBody.get('amount')).toBe('200000')
    expect(itemBody.get('quantity')).toBeNull()
  })

  it('dev mode (no API key): returns a stub without any network call', async () => {
    const result = await createStripeInvoice(undefined, baseParams())
    expect(calls).toHaveLength(0)
    expect(result.id).toMatch(/^dev_inv_/)
    expect(result.hosted_invoice_url).toBe('#dev-mode')
    expect(result.status).toBe('draft')
  })

  it('throws with the status code when invoice creation fails', async () => {
    queue(json({ data: [{ id: 'cus_1' }] }), json({ error: 'card declined' }, 402))
    await expect(createStripeInvoice(KEY, baseParams())).rejects.toThrow(
      /Stripe invoice creation failed 402/
    )
  })

  it('throws when a line item creation fails', async () => {
    queue(
      json({ data: [{ id: 'cus_1' }] }),
      json({ id: 'in_8', hosted_invoice_url: null, status: 'draft' }),
      json({ error: 'bad item' }, 400)
    )
    await expect(createStripeInvoice(KEY, baseParams())).rejects.toThrow(
      /Stripe invoice item creation failed 400/
    )
  })
})

// ---------------------------------------------------------------------------
// sendStripeInvoice
// ---------------------------------------------------------------------------

describe('finalizeStripeInvoice (present without email)', () => {
  it('finalizes with auto_advance=false and never calls /send', async () => {
    queue(json({ id: 'in_7', hosted_invoice_url: 'https://pay.stripe.com/final', status: 'open' }))

    const result = await finalizeStripeInvoice(KEY, 'in_7')

    expect(calls).toHaveLength(1)
    expect(calls[0].url).toBe(`${API}/invoices/in_7/finalize`)
    expect(calls[0].init?.method).toBe('POST')
    expect(headersOf(calls[0]).Authorization).toBe(`Bearer ${KEY}`)
    // auto_advance=false is the whole point: Stripe emails a finalized
    // send_invoice invoice automatically UNLESS automatic collection is off.
    expect(bodyParams(calls[0]).get('auto_advance')).toBe('false')
    expect(calls.some((c) => c.url.endsWith('/send'))).toBe(false)

    expect(result).toEqual({
      id: 'in_7',
      hosted_invoice_url: 'https://pay.stripe.com/final',
      status: 'open',
    })
  })

  it('throws on finalize failure', async () => {
    queue(json({ error: { message: 'nope' } }, 400))
    await expect(finalizeStripeInvoice(KEY, 'in_8')).rejects.toThrow(/finalize failed 400/)
  })

  it('dev mode (no API key): returns an open stub without any network call', async () => {
    const result = await finalizeStripeInvoice(undefined, 'in_9')
    expect(calls).toHaveLength(0)
    expect(result.status).toBe('open')
  })
})

describe('sendStripeInvoice', () => {
  it('finalizes then sends, preferring the send response fields', async () => {
    queue(
      json({ id: 'in_1', hosted_invoice_url: 'https://pay.stripe.com/final', status: 'open' }),
      json({ id: 'in_1', hosted_invoice_url: 'https://pay.stripe.com/sent', status: 'open' })
    )

    const result = await sendStripeInvoice(KEY, 'in_1')

    expect(calls).toHaveLength(2)
    expect(calls[0].url).toBe(`${API}/invoices/in_1/finalize`)
    expect(calls[0].init?.method).toBe('POST')
    expect(headersOf(calls[0]).Authorization).toBe(`Bearer ${KEY}`)
    expect(calls[1].url).toBe(`${API}/invoices/in_1/send`)
    expect(calls[1].init?.method).toBe('POST')

    expect(result).toEqual({
      id: 'in_1',
      hosted_invoice_url: 'https://pay.stripe.com/sent',
      status: 'open',
    })
  })

  it('falls back to the finalize response when the send response omits fields', async () => {
    queue(
      json({ id: 'in_2', hosted_invoice_url: 'https://pay.stripe.com/final', status: 'open' }),
      json({})
    )

    const result = await sendStripeInvoice(KEY, 'in_2')
    expect(result).toEqual({
      id: 'in_2',
      hosted_invoice_url: 'https://pay.stripe.com/final',
      status: 'open',
    })
  })

  it('throws on finalize failure without attempting the send', async () => {
    queue(json({ error: 'nope' }, 400))
    await expect(sendStripeInvoice(KEY, 'in_3')).rejects.toThrow(/Stripe finalize failed 400/)
    expect(calls).toHaveLength(1)
  })

  it('throws on send failure', async () => {
    queue(json({ id: 'in_4', hosted_invoice_url: null, status: 'open' }), json({ error: 'x' }, 500))
    await expect(sendStripeInvoice(KEY, 'in_4')).rejects.toThrow(/Stripe send failed 500/)
  })

  it('dev mode (no API key): returns an open stub without any network call', async () => {
    const result = await sendStripeInvoice(undefined, 'in_dev')
    expect(calls).toHaveLength(0)
    expect(result).toEqual({ id: 'in_dev', hosted_invoice_url: '#dev-mode', status: 'open' })
  })
})

// ---------------------------------------------------------------------------
// voidStripeInvoice / getStripeInvoice
// ---------------------------------------------------------------------------

describe('voidStripeInvoice', () => {
  it('posts to /invoices/:id/void with auth', async () => {
    queue(json({ id: 'in_1', status: 'void' }))
    await voidStripeInvoice(KEY, 'in_1')
    expect(calls[0].url).toBe(`${API}/invoices/in_1/void`)
    expect(calls[0].init?.method).toBe('POST')
    expect(headersOf(calls[0]).Authorization).toBe(`Bearer ${KEY}`)
  })

  it('throws on failure', async () => {
    queue(json({ error: 'no' }, 400))
    await expect(voidStripeInvoice(KEY, 'in_1')).rejects.toThrow(/Stripe void failed 400/)
  })

  it('dev mode: no network call', async () => {
    await voidStripeInvoice(undefined, 'in_1')
    expect(calls).toHaveLength(0)
  })
})

describe('getStripeInvoice', () => {
  it('GETs the invoice and maps id, hosted url, and status', async () => {
    queue(
      json({
        id: 'in_9',
        object: 'invoice',
        status: 'open',
        hosted_invoice_url: 'https://pay.stripe.com/in_9',
      })
    )
    const result = await getStripeInvoice(KEY, 'in_9')
    expect(calls[0].url).toBe(`${API}/invoices/in_9`)
    expect(calls[0].init?.method).toBe('GET')
    expect(headersOf(calls[0]).Authorization).toBe(`Bearer ${KEY}`)
    expect(result).toEqual({
      id: 'in_9',
      hosted_invoice_url: 'https://pay.stripe.com/in_9',
      status: 'open',
    })
  })

  it('throws on failure with the status code', async () => {
    queue(json({ error: 'missing' }, 404))
    await expect(getStripeInvoice(KEY, 'in_9')).rejects.toThrow(/Stripe invoice get failed 404/)
  })

  it('dev mode: returns a draft stub without any network call', async () => {
    const result = await getStripeInvoice(undefined, 'in_dev')
    expect(calls).toHaveLength(0)
    expect(result).toEqual({ id: 'in_dev', hosted_invoice_url: '#dev-mode', status: 'draft' })
  })
})
