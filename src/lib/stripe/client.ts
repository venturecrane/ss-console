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

import type { StripeCreateInvoiceParams, StripeInvoice, StripeInvoiceResult } from './types'

const STRIPE_API_BASE = 'https://api.stripe.com/v1'

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

  // Step 1: Create or find customer by email
  const customerSearchBody = new URLSearchParams()
  customerSearchBody.append('email', params.customer_email)
  customerSearchBody.append('limit', '1')

  const customerSearchRes = await fetch(
    `${STRIPE_API_BASE}/customers/search?query=email:'${encodeURIComponent(params.customer_email)}'`,
    {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${apiKey}`,
      },
    }
  )

  let customerId: string

  if (customerSearchRes.ok) {
    const searchData = (await customerSearchRes.json()) as { data: { id: string }[] }
    if (searchData.data.length > 0) {
      customerId = searchData.data[0].id
    } else {
      // Create customer
      const createCustomerBody = new URLSearchParams()
      createCustomerBody.append('email', params.customer_email)
      const createRes = await fetch(`${STRIPE_API_BASE}/customers`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: createCustomerBody.toString(),
      })
      if (!createRes.ok) {
        const errBody = await createRes.text()
        throw new Error(`Stripe customer creation failed ${createRes.status}: ${errBody}`)
      }
      const customerData = (await createRes.json()) as { id: string }
      customerId = customerData.id
    }
  } else {
    // Fallback: just create a new customer
    const createCustomerBody = new URLSearchParams()
    createCustomerBody.append('email', params.customer_email)
    const createRes = await fetch(`${STRIPE_API_BASE}/customers`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: createCustomerBody.toString(),
    })
    if (!createRes.ok) {
      const errBody = await createRes.text()
      throw new Error(`Stripe customer creation failed ${createRes.status}: ${errBody}`)
    }
    const customerData = (await createRes.json()) as { id: string }
    customerId = customerData.id
  }

  // Step 2: Create draft invoice
  const invoiceBody = new URLSearchParams()
  invoiceBody.append('customer', customerId)
  invoiceBody.append('collection_method', params.collection_method ?? 'send_invoice')
  invoiceBody.append('days_until_due', String(params.days_until_due ?? 15))

  if (params.description) {
    invoiceBody.append('description', params.description)
  }

  if (params.metadata) {
    for (const [key, value] of Object.entries(params.metadata)) {
      invoiceBody.append(`metadata[${key}]`, value)
    }
  }

  if (params.payment_settings?.payment_method_types) {
    for (const methodType of params.payment_settings.payment_method_types) {
      invoiceBody.append('payment_settings[payment_method_types][]', methodType)
    }
  }

  const invoiceRes = await fetch(`${STRIPE_API_BASE}/invoices`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: invoiceBody.toString(),
  })

  if (!invoiceRes.ok) {
    const errBody = await invoiceRes.text()
    throw new Error(`Stripe invoice creation failed ${invoiceRes.status}: ${errBody}`)
  }

  const invoice = (await invoiceRes.json()) as StripeInvoice

  // Step 3: Add line items (invoice items)
  for (const item of params.line_items) {
    const itemBody = new URLSearchParams()
    itemBody.append('customer', customerId)
    itemBody.append('invoice', invoice.id)
    itemBody.append('unit_amount', String(Math.round(item.amount / item.quantity)))
    itemBody.append('currency', item.currency)
    itemBody.append('description', item.description)
    itemBody.append('quantity', String(item.quantity))

    const itemRes = await fetch(`${STRIPE_API_BASE}/invoiceitems`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: itemBody.toString(),
    })

    if (!itemRes.ok) {
      const errBody = await itemRes.text()
      throw new Error(`Stripe invoice item creation failed ${itemRes.status}: ${errBody}`)
    }
  }

  return {
    id: invoice.id,
    hosted_invoice_url: invoice.hosted_invoice_url,
    status: invoice.status,
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
    return {
      id: invoiceId,
      hosted_invoice_url: '#dev-mode',
      status: 'open',
    }
  }

  // Finalize
  const finalizeRes = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/finalize`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  })

  if (!finalizeRes.ok) {
    const errBody = await finalizeRes.text()
    throw new Error(`Stripe invoice finalize failed ${finalizeRes.status}: ${errBody}`)
  }

  // Send
  const sendRes = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/send`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  })

  if (!sendRes.ok) {
    const errBody = await sendRes.text()
    throw new Error(`Stripe invoice send failed ${sendRes.status}: ${errBody}`)
  }

  const invoice = (await sendRes.json()) as StripeInvoice

  return {
    id: invoice.id,
    hosted_invoice_url: invoice.hosted_invoice_url,
    status: invoice.status,
  }
}

/**
 * Void a Stripe invoice.
 *
 * If apiKey is undefined: dev-mode stub.
 */
export async function voidStripeInvoice(
  apiKey: string | undefined,
  invoiceId: string
): Promise<StripeInvoiceResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would void invoice ${invoiceId}`)
    return { id: invoiceId, hosted_invoice_url: null, status: 'void' }
  }

  const res = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}/void`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  })

  if (!res.ok) {
    const errBody = await res.text()
    throw new Error(`Stripe invoice void failed ${res.status}: ${errBody}`)
  }

  const invoice = (await res.json()) as StripeInvoice

  return {
    id: invoice.id,
    hosted_invoice_url: invoice.hosted_invoice_url,
    status: invoice.status,
  }
}

/**
 * Get a Stripe invoice by ID.
 *
 * If apiKey is undefined: dev-mode stub.
 */
export async function getStripeInvoice(
  apiKey: string | undefined,
  invoiceId: string
): Promise<StripeInvoiceResult> {
  if (!apiKey) {
    console.log(`[DEV] Stripe: would get invoice ${invoiceId}`)
    return { id: invoiceId, hosted_invoice_url: '#dev-mode', status: 'draft' }
  }

  const res = await fetch(`${STRIPE_API_BASE}/invoices/${invoiceId}`, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${apiKey}`,
    },
  })

  if (!res.ok) {
    const errBody = await res.text()
    throw new Error(`Stripe invoice get failed ${res.status}: ${errBody}`)
  }

  const invoice = (await res.json()) as StripeInvoice

  return {
    id: invoice.id,
    hosted_invoice_url: invoice.hosted_invoice_url,
    status: invoice.status,
  }
}
