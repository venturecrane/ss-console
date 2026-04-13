import type { APIRoute } from 'astro'
import type { SignWellWebhookPayload } from '../../../lib/signwell/types'
import { handleDocumentCompleted } from '../../../lib/webhooks/signwell-handler'

/**
 * POST /api/webhooks/signwell
 *
 * Receives webhook callbacks from SignWell when document events occur.
 *
 * This is an unauthenticated endpoint — SignWell webhooks do not carry
 * session tokens. Security is enforced via HMAC-SHA256 hash verification
 * using the webhook ID (stored as SIGNWELL_WEBHOOK_SECRET).
 *
 * Unlike Stripe (which puts the signature in an HTTP header), SignWell
 * includes the hash inside the JSON body at `event.hash`. This means we
 * must parse the body before we can verify it. To maintain defense in
 * depth, we extract ONLY the three verification fields (type, time, hash)
 * before verification and do not log, dispatch, or act on any payload
 * data until the hash check passes.
 *
 * Ref: https://developers.signwell.com/reference/event-hash-verification
 *
 * Only processes `document_completed` events. All other events are
 * acknowledged with 200 but not acted upon.
 */

/** Maximum age (in seconds) for a webhook timestamp to be considered fresh. */
const MAX_WEBHOOK_AGE_SECONDS = 300

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

  // --- Parse body (required — SignWell puts the hash inside the JSON) ---
  let payload: SignWellWebhookPayload
  try {
    const rawBody = await request.text()
    payload = JSON.parse(rawBody) as SignWellWebhookPayload
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Extract verification fields only (no logging/dispatch yet) ---
  const eventType = payload.event?.type
  const eventTime = payload.event?.time
  const eventHash = payload.event?.hash

  if (!eventType || eventTime == null || !eventHash) {
    return new Response(JSON.stringify({ error: 'Missing event fields' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- HMAC-SHA256 verification ---
  const isValid = await verifyEventHash(eventType, eventTime, eventHash, webhookSecret)
  if (!isValid) {
    console.error('[webhook/signwell] Invalid event hash')
    return new Response(JSON.stringify({ error: 'Invalid signature' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Timestamp freshness check (replay protection) ---
  const nowSeconds = Math.floor(Date.now() / 1000)
  if (nowSeconds - eventTime > MAX_WEBHOOK_AGE_SECONDS) {
    console.error(`[webhook/signwell] Stale webhook: event.time ${eventTime}, now ${nowSeconds}`)
    return new Response(JSON.stringify({ error: 'Stale webhook' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Dispatch by event type ---
  if (payload.event.type === 'document_completed') {
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
      env.APP_BASE_URL,
      payload
    )
  }

  // Acknowledge all other events without processing
  return new Response(JSON.stringify({ ok: true, event: payload.event.type }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Verify the HMAC-SHA256 event hash from a SignWell webhook.
 *
 * SignWell signs the string "{event_type}@{event_time}" using the
 * webhook ID as the HMAC key and includes the hex digest in the
 * payload at `event.hash`.
 *
 * Ref: https://developers.signwell.com/reference/event-hash-verification
 *
 * Uses the Web Crypto API (available in Cloudflare Workers).
 */
async function verifyEventHash(
  type: string,
  time: number,
  hash: string,
  secret: string
): Promise<boolean> {
  if (!hash) {
    return false
  }

  const data = `${type}@${time}`
  const encoder = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )

  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(data))
  const digest = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

  // Constant-time comparison to prevent timing attacks
  if (digest.length !== hash.length) {
    return false
  }

  let mismatch = 0
  for (let i = 0; i < digest.length; i++) {
    mismatch |= digest.charCodeAt(i) ^ hash.charCodeAt(i)
  }

  return mismatch === 0
}
