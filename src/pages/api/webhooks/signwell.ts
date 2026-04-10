import type { APIRoute } from 'astro'
import type { SignWellWebhookPayload } from '../../../lib/signwell/types'
import { handleDocumentCompleted } from '../../../lib/webhooks/signwell-handler'

/**
 * POST /api/webhooks/signwell
 *
 * Receives webhook callbacks from SignWell when document events occur.
 *
 * This is an unauthenticated endpoint — SignWell webhooks do not carry
 * session tokens. Security is enforced via HMAC-SHA256 signature verification
 * using the SIGNWELL_WEBHOOK_SECRET.
 *
 * Only processes `document_completed` events. All other events are
 * acknowledged with 200 but not acted upon.
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const env = locals.runtime.env

  const webhookSecret = env.SIGNWELL_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/signwell] SIGNWELL_WEBHOOK_SECRET not configured')
    return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Signature verification ---
  const rawBody = await request.text()
  const signature = request.headers.get('x-signwell-signature') ?? ''

  const isValid = await verifySignature(rawBody, signature, webhookSecret)
  if (!isValid) {
    console.error('[webhook/signwell] Invalid webhook signature')
    return new Response(JSON.stringify({ error: 'Invalid signature' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Parse payload ---
  let payload: SignWellWebhookPayload
  try {
    payload = JSON.parse(rawBody) as SignWellWebhookPayload
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Dispatch by event type ---
  if (payload.event === 'document_completed') {
    const apiKey = env.SIGNWELL_API_KEY
    if (!apiKey) {
      console.error('[webhook/signwell] SIGNWELL_API_KEY not configured')
      return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return handleDocumentCompleted(
      env.DB,
      env.STORAGE,
      apiKey,
      env.RESEND_API_KEY,
      env.STRIPE_API_KEY,
      payload
    )
  }

  // Acknowledge all other events without processing
  return new Response(JSON.stringify({ ok: true, event: payload.event }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Verify the HMAC-SHA256 signature of the webhook payload.
 *
 * SignWell signs the raw request body with the webhook secret using
 * HMAC-SHA256 and sends the hex-encoded digest in the
 * x-signwell-signature header.
 *
 * Uses the Web Crypto API (available in Cloudflare Workers).
 */
async function verifySignature(body: string, signature: string, secret: string): Promise<boolean> {
  if (!signature) {
    return false
  }

  const encoder = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )

  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(body))
  const digest = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

  // Constant-time comparison to prevent timing attacks
  if (digest.length !== signature.length) {
    return false
  }

  let mismatch = 0
  for (let i = 0; i < digest.length; i++) {
    mismatch |= digest.charCodeAt(i) ^ signature.charCodeAt(i)
  }

  return mismatch === 0
}
