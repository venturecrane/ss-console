import type { APIRoute } from 'astro'
import { z } from 'zod'
import type { StripeWebhookEvent } from '../../../lib/stripe/types'
import { handleInvoicePaid, handleInvoicePaymentFailed } from '../../../lib/webhooks/stripe-handler'
import {
  handleUnrecognizedInvoiceLinkage,
  resolveStripeSubscriptionLinkage,
  handleRetainerInvoiceFinalized,
  handleRetainerInvoicePaid,
  handleRetainerInvoicePaymentFailed,
  handleSubscriptionLifecycle,
} from '../../../lib/webhooks/stripe-subscription-handler'
import { handleHostedAgentCheckoutCompleted } from '../../../lib/webhooks/hosted-agent-checkout-handler'
import {
  handleOperatorCheckoutAsyncPaymentFailed,
  handleOperatorCheckoutCompleted,
} from '../../../lib/webhooks/operator-checkout-handler'
import { OPERATOR_CHECKOUT_PRODUCT_SLUG } from '../../../lib/stripe/subscriptions'
import { env } from 'cloudflare:workers'
import { errorResponse, jsonResponse } from '../../../lib/api/helpers'
import { getAdminBaseUrl, getPortalBaseUrl } from '../../../lib/config/app-url'

/**
 * POST /api/webhooks/stripe
 *
 * Receives webhook callbacks from Stripe when invoice events occur.
 *
 * This is an unauthenticated endpoint — Stripe webhooks do not carry
 * session tokens. Security is enforced via Stripe-Signature header
 * verification using the STRIPE_WEBHOOK_SECRET.
 *
 * Dispatch (#1679): invoice events carrying a subscription linkage belong to
 * the Operator retainer flow (stripe-subscription-handler.ts — Stripe
 * originates those invoices monthly and we mirror them locally); invoice
 * events without one belong to the legacy console-originated one-time flow
 * (stripe-handler.ts). `customer.subscription.updated`/`.deleted` mirror
 * billing state onto the local subscriptions row. All other events are
 * acknowledged with 200 but not acted upon.
 */
const StripeWebhookEnvelopeSchema = z.looseObject({
  type: z.string(),
})

const InvoiceSubscriptionDetailsSchema = z
  .looseObject({ metadata: z.record(z.string(), z.string()).nullable().optional() })
  .nullable()
  .optional()

const StripeInvoiceSchema = z.looseObject({
  id: z.string().min(1),
  object: z.literal('invoice'),
  status: z.enum(['draft', 'open', 'paid', 'uncollectible', 'void']),
  amount_due: z.number(),
  amount_paid: z.number(),
  currency: z.string(),
  customer: z.string(),
  customer_email: z.string().nullable(),
  description: z.string().nullable(),
  hosted_invoice_url: z.string().nullable(),
  invoice_pdf: z.string().nullable(),
  collection_method: z.string(),
  status_transitions: z.object({
    paid_at: z.number().nullable(),
    finalized_at: z.number().nullable(),
    voided_at: z.number().nullable(),
  }),
  metadata: z.record(z.string(), z.string()),
  created: z.number(),
  due_date: z.number().nullable(),
  // The subscription-metadata snapshot Stripe stamps on subscription
  // invoices: `subscription_details.metadata` pre-basil,
  // `parent.subscription_details.metadata` from 2025-03-31.basil (the prod
  // endpoint pins 2026-03-25.dahlia). Both declared so the ordering
  // fallback in handleRetainerInvoicePaid can read whichever arrives;
  // `parent.subscription_details.subscription` stays undeclared and is
  // read by resolveStripeSubscriptionLinkage from the retained fields.
  subscription_details: InvoiceSubscriptionDetailsSchema,
  parent: z
    .looseObject({ subscription_details: InvoiceSubscriptionDetailsSchema })
    .nullable()
    .optional(),
})

const StripeInvoiceWebhookEventSchema = z.looseObject({
  id: z.string(),
  object: z.literal('event'),
  type: z.string(),
  data: z.object({
    object: StripeInvoiceSchema,
  }),
  created: z.number(),
})

// customer.subscription.* payload — only the fields the local mirror consumes
// (#1679). `pause_collection` stays unknown-typed: presence is the signal.
const StripeSubscriptionSchema = z.looseObject({
  id: z.string().min(1),
  object: z.literal('subscription'),
  status: z.string(),
  pause_collection: z.unknown().optional(),
  // Client-scheduled cancellation (Billing Portal, mode at_period_end). The
  // end date rides on `cancel_at`; `items[].current_period_end` is the
  // fallback because this API version carries no top-level period end.
  cancel_at_period_end: z.boolean().optional(),
  cancel_at: z.number().nullable().optional(),
  items: z
    .looseObject({
      data: z.array(z.looseObject({ current_period_end: z.number().nullable().optional() })),
    })
    .optional(),
})

const StripeSubscriptionWebhookEventSchema = z.looseObject({
  id: z.string(),
  object: z.literal('event'),
  type: z.string(),
  data: z.object({
    object: StripeSubscriptionSchema,
  }),
  created: z.number(),
})

// checkout.session.completed payload — the fields the Hosted Agent concierge
// pipeline consumes (ADR 0067). looseObject retains everything else.
const StripeCheckoutSessionSchema = z.looseObject({
  id: z.string().min(1),
  object: z.literal('checkout.session'),
  client_reference_id: z.string().nullable().optional().default(null),
  customer: z.string().nullable().optional().default(null),
  subscription: z.string().nullable().optional().default(null),
  amount_total: z.number().nullable().optional().default(null),
  customer_details: z
    .looseObject({
      email: z.string().nullable().optional().default(null),
      name: z.string().nullable().optional().default(null),
    })
    .nullable()
    .optional()
    .default(null),
  metadata: z.record(z.string(), z.string()).optional().default({}),
  // docs.stripe.com/api/checkout/sessions/object: "one of paid, unpaid, or
  // no_payment_required". An ACH session completes `unpaid`; the operator
  // handler gates go-live on it.
  payment_status: z.enum(['paid', 'unpaid', 'no_payment_required']),
  total_details: z
    .looseObject({ amount_discount: z.number().optional() })
    .nullable()
    .optional()
    .default(null),
})

const StripeCheckoutSessionWebhookEventSchema = z.looseObject({
  id: z.string(),
  object: z.literal('event'),
  type: z.string(),
  data: z.object({
    object: StripeCheckoutSessionSchema,
  }),
  created: z.number(),
})

/**
 * Route an invoice event. Subscription-linked payloads (every API shape
 * `resolveStripeSubscriptionLinkage` knows — looseObject retains the fields
 * even though the schema does not declare them) belong to the retainer
 * mirror; unlinked ones to the legacy one-time flow.
 *
 * A payload that signals a subscription but whose linkage cannot be read is
 * the third case, and it must not travel either route: sending it to the
 * legacy flow finds no local invoice row and acks, which is how a Stripe API
 * change turns into a silently missing retainer invoice. It fails loudly
 * instead (ss#2315).
 *
 * Returns null when the event type is not an invoice event this route handles.
 */
async function dispatchInvoiceEvent(eventType: string, parsed: unknown): Promise<Response | null> {
  if (
    eventType !== 'invoice.paid' &&
    eventType !== 'invoice.payment_failed' &&
    eventType !== 'invoice.finalized'
  ) {
    return null
  }
  const eventResult = StripeInvoiceWebhookEventSchema.safeParse(parsed)
  if (!eventResult.success) {
    return errorResponse(400, 'Malformed event payload')
  }
  const invoice = eventResult.data.data.object
  const linkage = resolveStripeSubscriptionLinkage(invoice)
  if (linkage.kind === 'unrecognized') {
    return handleUnrecognizedInvoiceLinkage(
      env.RESEND_API_KEY,
      eventType,
      invoice.id,
      linkage.reason
    )
  }
  const subId = linkage.kind === 'linked' ? linkage.subscriptionId : null

  if (eventType === 'invoice.paid') {
    if (subId !== null) return handleRetainerInvoicePaid(env.DB, env.RESEND_API_KEY, subId, invoice)
    const event: StripeWebhookEvent = eventResult.data
    return handleInvoicePaid(env.DB, env.RESEND_API_KEY, event)
  }
  if (eventType === 'invoice.payment_failed') {
    if (subId !== null) {
      return handleRetainerInvoicePaymentFailed(env.DB, env.RESEND_API_KEY, subId, invoice)
    }
    const event: StripeWebhookEvent = eventResult.data
    return handleInvoicePaymentFailed(env.DB, event)
  }
  // invoice.finalized: mirror the open cycle invoice so the portal shows what
  // Stripe emailed. One-time invoices are console-originated; no mirror needed.
  if (subId !== null) return handleRetainerInvoiceFinalized(env.DB, subId, invoice)
  return jsonResponse(200, { ok: true, event: eventType })
}

/** Route checkout.session.* by the session's `product_slug` metadata: the
 * Operator retainer start (operator-checkout-handler.ts) or the Hosted Agent
 * concierge pipeline (ADR 0067). Each handler acks sessions that are not
 * its own.
 *
 * The two `async_payment_*` events exist for delayed payment methods (ACH
 * on the operator checkout): `completed` fires while the funds are still
 * pending and the async event says whether they landed. Only the operator
 * flow offers ACH, so those two route to it alone; a Hosted Agent session
 * never produces them and is acked. Returns null for other event types. */
async function dispatchCheckoutEvent(eventType: string, parsed: unknown): Promise<Response | null> {
  if (
    eventType !== 'checkout.session.completed' &&
    eventType !== 'checkout.session.async_payment_succeeded' &&
    eventType !== 'checkout.session.async_payment_failed'
  ) {
    return null
  }
  const eventResult = StripeCheckoutSessionWebhookEventSchema.safeParse(parsed)
  if (!eventResult.success) {
    return errorResponse(400, 'Malformed event payload')
  }
  const session = eventResult.data.data.object
  const isOperator = session.metadata['product_slug'] === OPERATOR_CHECKOUT_PRODUCT_SLUG
  if (eventType === 'checkout.session.async_payment_failed') {
    if (!isOperator) return jsonResponse(200, { ok: true, event: eventType })
    return handleOperatorCheckoutAsyncPaymentFailed(
      env.DB,
      env.STRIPE_API_KEY,
      env.RESEND_API_KEY,
      session
    )
  }
  if (isOperator) {
    // completed and async_payment_succeeded share the handler; the
    // session's payment_status decides whether the row goes live.
    return handleOperatorCheckoutCompleted(env.DB, session)
  }
  if (eventType !== 'checkout.session.completed') {
    return jsonResponse(200, { ok: true, event: eventType })
  }
  // Non-throwing base-URL reads: a missing env var must degrade the email
  // links, never 500 the webhook (Stripe would retry-loop a config gap).
  const portalBase = getPortalBaseUrl(env) ?? 'https://portal.smd.services'
  const adminBase = getAdminBaseUrl(env) ?? 'https://admin.smd.services'
  return handleHostedAgentCheckoutCompleted(
    env.DB,
    env.RESEND_API_KEY,
    `${portalBase}/portal/products/hosted-agent`,
    `${adminBase}/admin/hosted-agent`,
    eventResult.data.data.object
  )
}

/** Route customer.subscription.updated/.deleted to the local status mirror.
 * Returns null for other event types. */
async function dispatchSubscriptionEvent(
  eventType: string,
  parsed: unknown
): Promise<Response | null> {
  if (
    eventType !== 'customer.subscription.updated' &&
    eventType !== 'customer.subscription.deleted'
  ) {
    return null
  }
  const eventResult = StripeSubscriptionWebhookEventSchema.safeParse(parsed)
  if (!eventResult.success) {
    return errorResponse(400, 'Malformed event payload')
  }
  return handleSubscriptionLifecycle(
    env.DB,
    eventType,
    eventResult.data.data.object,
    env.RESEND_API_KEY
  )
}

type ParseStripeWebhookEventResult = { parsed: unknown } | { response: Response }

function parseStripeWebhookEvent(rawBody: string): ParseStripeWebhookEventResult {
  try {
    return { parsed: JSON.parse(rawBody) as unknown }
  } catch {
    return {
      response: errorResponse(400, 'Invalid JSON'),
    }
  }
}

export const POST: APIRoute = async ({ request }) => {
  const webhookSecret = env.STRIPE_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/stripe] STRIPE_WEBHOOK_SECRET not configured')
    return errorResponse(500, 'Server misconfigured')
  }

  // --- Signature verification ---
  const rawBody = await request.text()
  const signatureHeader = request.headers.get('stripe-signature') ?? ''

  const isValid = await verifyStripeSignature(rawBody, signatureHeader, webhookSecret)
  if (!isValid) {
    console.error('[webhook/stripe] Invalid webhook signature')
    return errorResponse(401, 'Invalid signature')
  }

  // --- Parse payload ---
  const parsedResult = parseStripeWebhookEvent(rawBody)
  if ('response' in parsedResult) return parsedResult.response
  const parsed = parsedResult.parsed

  const envelopeResult = StripeWebhookEnvelopeSchema.safeParse(parsed)
  if (!envelopeResult.success) {
    return errorResponse(400, 'Invalid JSON')
  }
  const eventType = envelopeResult.data.type

  // --- Dispatch by event type ---
  const invoiceEventResponse = await dispatchInvoiceEvent(eventType, parsed)
  if (invoiceEventResponse) return invoiceEventResponse

  const checkoutEventResponse = await dispatchCheckoutEvent(eventType, parsed)
  if (checkoutEventResponse) return checkoutEventResponse

  const subscriptionEventResponse = await dispatchSubscriptionEvent(eventType, parsed)
  if (subscriptionEventResponse) return subscriptionEventResponse

  // Acknowledge all other events without processing
  return jsonResponse(200, { ok: true, event: eventType })
}

/**
 * Verify the Stripe webhook signature.
 *
 * Stripe uses a timestamp + HMAC-SHA256 signature scheme:
 * - Header format: `t=<timestamp>,v1=<signature>`
 * - Signed payload: `<timestamp>.<rawBody>`
 * - Signature: HMAC-SHA256(webhook_secret, signed_payload)
 *
 * Also validates that the timestamp is not too old (5 minute tolerance)
 * to prevent replay attacks.
 *
 * Uses the Web Crypto API (available in Cloudflare Workers).
 */
async function verifyStripeSignature(
  body: string,
  signatureHeader: string,
  secret: string
): Promise<boolean> {
  if (!signatureHeader) {
    return false
  }

  // Parse the header into components
  const elements: Record<string, string> = {}
  for (const part of signatureHeader.split(',')) {
    const [key, value] = part.split('=', 2)
    if (key && value) {
      elements[key.trim()] = value.trim()
    }
  }

  const timestamp = elements['t']
  const signature = elements['v1']

  if (!timestamp || !signature) {
    return false
  }

  // Check timestamp tolerance (5 minutes)
  const timestampSeconds = parseInt(timestamp, 10)
  if (isNaN(timestampSeconds)) {
    return false
  }

  const nowSeconds = Math.floor(Date.now() / 1000)
  if (Math.abs(nowSeconds - timestampSeconds) > 300) {
    return false
  }

  // Compute expected signature
  const signedPayload = `${timestamp}.${body}`
  const encoder = new TextEncoder()

  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )

  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(signedPayload))
  const expectedSignature = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

  // Constant-time comparison to prevent timing attacks
  if (expectedSignature.length !== signature.length) {
    return false
  }

  let mismatch = 0
  for (let i = 0; i < expectedSignature.length; i++) {
    mismatch |= expectedSignature.charCodeAt(i) ^ signature.charCodeAt(i)
  }

  return mismatch === 0
}
