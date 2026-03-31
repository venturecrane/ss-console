import type { APIRoute } from 'astro'
import type { StripeWebhookEvent } from '../../../lib/stripe/types'
import { handleInvoicePaid, handleInvoicePaymentFailed } from '../../../lib/webhooks/stripe-handler'

/**
 * POST /api/webhooks/stripe
 *
 * Receives webhook callbacks from Stripe when invoice events occur.
 *
 * This is an unauthenticated endpoint — Stripe webhooks do not carry
 * session tokens. Security is enforced via Stripe-Signature header
 * verification using the STRIPE_WEBHOOK_SECRET.
 *
 * Only processes `invoice.paid` and `invoice.payment_failed` events.
 * All other events are acknowledged with 200 but not acted upon.
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const env = locals.runtime.env

  const webhookSecret = env.STRIPE_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/stripe] STRIPE_WEBHOOK_SECRET not configured')
    return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Signature verification ---
  const rawBody = await request.text()
  const signatureHeader = request.headers.get('stripe-signature') ?? ''

  const isValid = await verifyStripeSignature(rawBody, signatureHeader, webhookSecret)
  if (!isValid) {
    console.error('[webhook/stripe] Invalid webhook signature')
    return new Response(JSON.stringify({ error: 'Invalid signature' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Parse payload ---
  let event: StripeWebhookEvent
  try {
    event = JSON.parse(rawBody) as StripeWebhookEvent
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Dispatch by event type ---
  if (event.type === 'invoice.paid') {
    return handleInvoicePaid(env.DB, env.RESEND_API_KEY, event)
  }

  if (event.type === 'invoice.payment_failed') {
    return handleInvoicePaymentFailed(env.DB, event)
  }

  // Acknowledge all other events without processing
  return new Response(JSON.stringify({ ok: true, event: event.type }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
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
