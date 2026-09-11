/**
 * Stripe API client for invoice operations.
 *
 * Uses raw fetch against https://api.stripe.com/v1/ (no SDK).
 * Stripe API uses form-encoded bodies for most endpoints.
 *
 * DEV-MODE PATTERN: When apiKey is undefined, logs the request and
 * returns a mock response. Follows the same pattern as
 * src/lib/email/resend.ts handles missing RESEND_API_KEY.
 */

import type { StripeCreateInvoiceParams, StripeInvoiceResult } from './types'

const STRIPE_API_BASE = 'https://api.stripe.com/v1'

function stripeHeaders(apiKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${apiKey}`,
    'Content-Type': 'application/x-www-form-urlencoded',
  }
}

async function createStripeCustomer(apiKey: string, email: string): Promise<string> {
  const body = new URLSearchParams()
  body.append('email', email)
  const res = await fetch(`${STRIPE_API_BASE}/customers`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe customer creation failed ${res.status}: ${await res.text()}`)
  }
  const data: { id: string } = await res.json()
  return data.id
}

async function resolveStripeCustomerId(apiKey: string, email: string): Promise<string> {
  const searchRes = await fetch(
    `${STRIPE_API_BASE}/customers/search?query=email:'${encodeURIComponent(email)}'`,
    { method: 'GET', headers: { Authorization: `Bearer ${apiKey}` } }
  )
  if (searchRes.ok) {
    const data: { data: Array<{ id: string }> } = await searchRes.json()
    if (data.data.length > 0) return data.data[0].id
  }
  return createStripeCustomer(apiKey, email)
}

async function createStripeInvoiceRecord(
  apiKey: string,
  customerId: string,
  params: StripeCreateInvoiceParams
): Promise<{ id: string; hosted_invoice_url: string; status: string }> {
  const body = new URLSearchParams()
  body.append('customer', customerId)
  body.append('collection_method', params.collection_method ?? 'send_invoice')
  // Stripe accepts exactly one of due_date / days_until_due on a send_invoice
  // invoice; an exact instant wins when the caller has one.
  if (params.due_date !== undefined) body.append('due_date', String(params.due_date))
  else body.append('days_until_due', String(params.days_until_due ?? 15))
  if (params.description) body.append('description', params.description)
  if (params.metadata) {
    for (const [key, value] of Object.entries(params.metadata)) {
      body.append(`metadata[${key}]`, value)
    }
  }
  if (params.payment_settings?.payment_method_types) {
    for (const methodType of params.payment_settings.payment_method_types) {
      body.append('payment_settings[payment_method_types][]', methodType)
    }
  }
  const res = await fetch(`${STRIPE_API_BASE}/invoices`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!res.ok) {
    throw new Error(`Stripe invoice creation failed ${res.status}: ${await res.text()}`)
  }
  return await res.json()
}

async function addStripeLineItems(
  apiKey: string,
  customerId: string,
  invoiceId: string,
  lineItems: StripeCreateInvoiceParams['line_items']
): Promise<void> {
  for (const item of lineItems) {
    const body = new URLSearchParams()
    body.append('customer', customerId)
    body.append('invoice', invoiceId)
    // `amount` is the LINE TOTAL in Stripe's model (amount = unit_amount ×
    // quantity). Our StripeInvoiceLineItem.amount is per-unit, so multiply
    // here. Don't send `quantity`/`unit_amount` instead: unit_amount was
    // removed from invoiceitems create in API version 2025-03-31.basil and
    // this client pins no Stripe-Version header.
    body.append('amount', String(item.amount * item.quantity))
    body.append('currency', item.currency)
    body.append('description', item.description)
    const res = await fetch(`${STRIPE_API_BASE}/invoiceitems`, {
      method: 'POST',
      headers: stripeHeaders(apiKey),
      body: body.toString(),
    })
    if (!res.ok) {
      throw new Error(`Stripe invoice item creation failed ${res.status}: ${await res.text()}`)
    }
  }
}

/**
 * Create a Stripe invoice with line items, then return the result.
 *
 * Stripe invoice creation is a multi-step process:
 * 1. Create or find a customer by email
 * 2. Create a draft invoice for that customer
 * 3. Add line items (invoice items) to the invoice
 *
 * If apiKey is undefined: dev-mode stub.
 */
export async function createStripeInvoice(
  apiKey: string | undefined,
  params: StripeCreateInvoiceParams
): Promise<StripeInvoiceResult> {
  const totalCents = params.line_items.reduce((sum, item) => sum + item.amount * item.quantity, 0)
  const totalDollars = (totalCents / 100).toFixed(2)

  if (!apiKey) {
    const devId = 'dev_inv_' + crypto.randomUUID()
    console.log(`[DEV] Stripe: would create invoice for $${totalDollars}`)
    console.log(`[DEV] Stripe: customer_email=${params.customer_email}`)
    console.log(`[DEV] Stripe: line_items=${params.line_items.length}`)
    return { id: devId, hosted_invoice_url: '#dev-mode', status: 'draft' }
  }

  const customerId = await resolveStripeCustomerId(apiKey, params.customer_email)
  const invoice = await createStripeInvoiceRecord(apiKey, customerId, params)
  await addStripeLineItems(apiKey, customerId, invoice.id, params.line_items)

  return { id: invoice.id, hosted_invoice_url: invoice.hosted_invoice_url, status: invoice.status }
}

/**
 * Finalize a Stripe invoice WITHOUT emailing it: POST /invoices/:id/finalize
 * only. The invoice becomes payable at its hosted URL (which the portal
 * renders as the Pay button) while Stripe sends nothing to the customer.
 *
 * This is the "present" path: the client reads and pays the invoice in the
 * portal when they choose to, with no email announcing it. Stripe's own
 * reminder/dunning emails are governed by the dashboard's invoice settings,
 * not by this call.
 *
 * `auto_advance=false` is set so Stripe's invoice automation never emails
 * or attempts collection on this invoice on its own (send_invoice invoices
 * default to auto_advance=true at finalization).
 *
 * If apiKey is undefined: dev-mode stub.
 */
export async function finalizeStripeInvoice(
  apiKey: string | undefined,
  invoiceId: string
): Promise<StripeInvoiceResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would finalize (not send) invoice ${invoiceId}`)
    return { id: invoiceId, hosted_invoice_url: '#dev-mode', status: 'open' }
  }
  const body = new URLSearchParams()
  body.append('auto_advance', 'false')
  const finalizeRes = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/finalize`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
    body: body.toString(),
  })
  if (!finalizeRes.ok) {
    throw new Error(`Stripe finalize failed ${finalizeRes.status}: ${await finalizeRes.text()}`)
  }
  const finalized: { id?: string; hosted_invoice_url?: string; status?: string } =
    await finalizeRes.json()
  return {
    id: finalized.id ?? invoiceId,
    hosted_invoice_url: finalized.hosted_invoice_url ?? null,
    status: finalized.status ?? 'open',
  }
}

/**
 * Finalize and send a Stripe invoice.
 *
 * Two-step process:
 * 1. POST /invoices/:id/finalize — locks the invoice
 * 2. POST /invoices/:id/send — sends the hosted invoice email
 *
 * If apiKey is undefined: dev-mode stub.
 */
export async function sendStripeInvoice(
  apiKey: string | undefined,
  invoiceId: string
): Promise<StripeInvoiceResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would send invoice ${invoiceId}`)
    return { id: invoiceId, hosted_invoice_url: '#dev-mode', status: 'open' }
  }

  const finalizeRes = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/finalize`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
  })
  if (!finalizeRes.ok) {
    throw new Error(`Stripe finalize failed ${finalizeRes.status}: ${await finalizeRes.text()}`)
  }
  const finalized: { id?: string; hosted_invoice_url?: string; status?: string } =
    await finalizeRes.json()

  const sendRes = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/send`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
  })
  if (!sendRes.ok) {
    throw new Error(`Stripe send failed ${sendRes.status}: ${await sendRes.text()}`)
  }
  const sent: { id?: string; hosted_invoice_url?: string; status?: string } = await sendRes.json()

  return {
    id: sent.id ?? finalized.id ?? invoiceId,
    hosted_invoice_url: sent.hosted_invoice_url ?? finalized.hosted_invoice_url ?? null,
    status: sent.status ?? finalized.status ?? 'open',
  }
}

export async function voidStripeInvoice(
  apiKey: string | undefined,
  invoiceId: string
): Promise<void> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would void invoice ${invoiceId}`)
    return
  }
  const res = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/void`, {
    method: 'POST',
    headers: stripeHeaders(apiKey),
  })
  if (!res.ok) {
    throw new Error(`Stripe void failed ${res.status}: ${await res.text()}`)
  }
}
