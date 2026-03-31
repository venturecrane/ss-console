/**
 * TypeScript types for the Stripe Invoice API.
 *
 * Based on the Stripe API v1 documentation.
 * These types cover the subset of the API used by the SMD Services portal:
 * invoice creation, finalization, sending, voiding, and webhook events.
 *
 * Stripe uses snake_case for all API fields.
 */

/**
 * Line item for a Stripe invoice.
 */
export interface StripeInvoiceLineItem {
  /** Price amount in cents */
  amount: number
  /** Currency code (e.g. 'usd') */
  currency: string
  /** Description shown on the invoice */
  description: string
  /** Quantity (default 1) */
  quantity: number
}

/**
 * Parameters for creating a Stripe invoice.
 */
export interface StripeCreateInvoiceParams {
  /** Customer email — Stripe creates/reuses a customer record */
  customer_email: string
  /** Description shown on the invoice */
  description?: string
  /** Line items to include */
  line_items: StripeInvoiceLineItem[]
  /** Days until due (default 15) */
  days_until_due?: number
  /** Collection method: 'send_invoice' sends email, 'charge_automatically' charges on file */
  collection_method?: 'send_invoice' | 'charge_automatically'
  /** Metadata key-value pairs */
  metadata?: Record<string, string>
  /** Payment settings */
  payment_settings?: {
    /** Default payment method types to enable */
    payment_method_types?: string[]
  }
}

/**
 * Stripe invoice object returned by the API (relevant fields).
 */
export interface StripeInvoice {
  id: string
  object: 'invoice'
  status: 'draft' | 'open' | 'paid' | 'uncollectible' | 'void'
  /** Amount due in cents */
  amount_due: number
  /** Amount paid in cents */
  amount_paid: number
  /** Currency code */
  currency: string
  /** Customer ID */
  customer: string
  /** Customer email */
  customer_email: string | null
  /** Description */
  description: string | null
  /** URL for hosted invoice payment page */
  hosted_invoice_url: string | null
  /** PDF URL for the invoice */
  invoice_pdf: string | null
  /** Collection method */
  collection_method: string
  /** Unix timestamp when paid */
  status_transitions: {
    paid_at: number | null
    finalized_at: number | null
    voided_at: number | null
  }
  /** Metadata */
  metadata: Record<string, string>
  /** Unix timestamp */
  created: number
  /** Due date as Unix timestamp */
  due_date: number | null
}

/**
 * Stripe webhook event envelope.
 */
export interface StripeWebhookEvent {
  id: string
  object: 'event'
  type: string
  data: {
    object: StripeInvoice
  }
  created: number
}

/**
 * Simplified result returned by the Stripe client (both real and dev-mode).
 */
export interface StripeInvoiceResult {
  id: string
  hosted_invoice_url: string | null
  status: string
}
