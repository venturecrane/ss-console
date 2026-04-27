import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { handleResendEvent, type ResendWebhookPayload } from '../../../lib/webhooks/resend-handler'

/**
 * POST /api/webhooks/resend
 *
 * Receives webhook callbacks from Resend (https://resend.com/docs/dashboard/webhooks)
 * for outreach lifecycle events: email.sent, email.delivered, email.opened,
 * email.clicked, email.bounced, email.complained.
 *
 * This is an unauthenticated endpoint — Resend webhooks do not carry
 * session tokens. Security is enforced via Svix signature verification
 * using the RESEND_WEBHOOK_SECRET (the `whsec_…` value from the Resend
 * dashboard webhook detail page).
 *
 * Resend uses Svix for delivery, which means three headers are sent on
 * every request:
 *   svix-id        — message id (also used as our idempotency key)
 *   svix-timestamp — unix seconds, used for staleness check
 *   svix-signature — space-delimited list of `vN,base64sig` pairs
 *
 * The signed content is `${svix-id}.${svix-timestamp}.${rawBody}`. The
 * secret is base64-decoded after stripping the `whsec_` prefix; HMAC-SHA256
 * yields the expected signature in base64. We accept the request when ANY
 * `v1` signature in the header matches.
 *
 * Refs:
 *   https://resend.com/docs/dashboard/webhooks/verify-webhooks-requests
 *   https://docs.svix.com/receiving/verifying-payloads/how-manual
 *
 * Idempotency: handleResendEvent dedupes on svix-id via the unique partial
 * index on outreach_events.provider_event_id. A retry of the same event
 * collapses to a single row.
 */

/** Reject webhooks older than this many seconds. Matches Stripe/SignWell. */
const MAX_WEBHOOK_AGE_SECONDS = 300

/** Default org id for events that can't be re-attributed via the sent row. */
const DEFAULT_ORG_ID = '01JQFK0000SMDSERVICES000'

export const POST: APIRoute = async ({ request }) => {
  const webhookSecret = env.RESEND_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/resend] RESEND_WEBHOOK_SECRET not configured')
    return new Response(JSON.stringify({ error: 'Server misconfigured' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const svixId = request.headers.get('svix-id')
  const svixTimestamp = request.headers.get('svix-timestamp')
  const svixSignature = request.headers.get('svix-signature')

  if (!svixId || !svixTimestamp || !svixSignature) {
    return new Response(JSON.stringify({ error: 'Missing svix headers' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Read the raw body BEFORE parsing — Svix signs the exact bytes, and any
  // round-trip through JSON.parse + JSON.stringify will mutate whitespace
  // and break verification.
  const rawBody = await request.text()

  // --- Timestamp freshness (replay protection) ---
  const tsSeconds = parseInt(svixTimestamp, 10)
  if (!Number.isFinite(tsSeconds)) {
    return new Response(JSON.stringify({ error: 'Invalid timestamp' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const nowSeconds = Math.floor(Date.now() / 1000)
  if (Math.abs(nowSeconds - tsSeconds) > MAX_WEBHOOK_AGE_SECONDS) {
    console.error(`[webhook/resend] Stale webhook: ts=${tsSeconds}, now=${nowSeconds}`)
    return new Response(JSON.stringify({ error: 'Stale webhook' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Svix signature verification ---
  const isValid = await verifySvixSignature(
    svixId,
    svixTimestamp,
    rawBody,
    svixSignature,
    webhookSecret
  )
  if (!isValid) {
    console.error('[webhook/resend] Invalid signature')
    return new Response(JSON.stringify({ error: 'Invalid signature' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Parse the now-verified body ---
  let payload: ResendWebhookPayload
  try {
    payload = JSON.parse(rawBody) as ResendWebhookPayload
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  if (!payload.type) {
    return new Response(JSON.stringify({ error: 'Missing event type' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // --- Dispatch ---
  try {
    const result = await handleResendEvent(env.DB, {
      providerEventId: svixId,
      payload,
      fallbackOrgId: DEFAULT_ORG_ID,
    })
    return new Response(
      JSON.stringify({
        ok: true,
        recorded: result.recorded,
        ...(result.reason ? { reason: result.reason } : {}),
        ...(result.eventType ? { event_type: result.eventType } : {}),
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )
  } catch (err) {
    console.error('[webhook/resend] handler failed:', err)
    // 500 → Svix retries with backoff. Better than silently 200ing on a
    // transient D1 error and losing the event.
    return new Response(JSON.stringify({ error: 'Internal error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}

/**
 * Verify a Svix-signed webhook payload.
 *
 * Algorithm (Svix manual verification):
 *   1. Strip the `whsec_` prefix from the secret and base64-decode the rest.
 *   2. Build signed content as `${svix-id}.${svix-timestamp}.${rawBody}`.
 *   3. HMAC-SHA256 with the decoded key bytes; output base64.
 *   4. Compare against any of the `v1,…` segments in the svix-signature
 *      header (which may carry multiple signatures during key rotation).
 *
 * Constant-time comparison mitigates timing attacks.
 *
 * Ref: https://docs.svix.com/receiving/verifying-payloads/how-manual
 */
async function verifySvixSignature(
  svixId: string,
  svixTimestamp: string,
  body: string,
  signatureHeader: string,
  secret: string
): Promise<boolean> {
  // Resend dashboard secrets carry a `whsec_` prefix. Tolerate either form
  // so a misconfigured secret (paste of just the base64 portion) still works.
  const secretBase64 = secret.startsWith('whsec_') ? secret.slice('whsec_'.length) : secret
  const keyBuffer = base64ToArrayBuffer(secretBase64)
  if (!keyBuffer) {
    console.error('[webhook/resend] Webhook secret is not valid base64')
    return false
  }

  const signedContent = `${svixId}.${svixTimestamp}.${body}`
  const encoder = new TextEncoder()

  let key: CryptoKey
  try {
    key = await crypto.subtle.importKey(
      'raw',
      keyBuffer,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    )
  } catch (err) {
    console.error('[webhook/resend] importKey failed:', err)
    return false
  }

  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(signedContent))
  const expectedSignature = bytesToBase64(new Uint8Array(mac))

  // svix-signature: `v1,sig1 v1,sig2 v2,sig3` — accept ANY v1 match.
  const candidates = signatureHeader.split(' ')
  for (const candidate of candidates) {
    const [version, sig] = candidate.split(',', 2)
    if (version !== 'v1' || !sig) continue
    if (constantTimeEquals(expectedSignature, sig)) {
      return true
    }
  }

  return false
}

/**
 * Constant-time string comparison. Returns false immediately on length
 * mismatch — that is acceptable here because the expected signature is
 * a fixed 44-character base64 of a 32-byte HMAC-SHA256 digest.
 */
function constantTimeEquals(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let mismatch = 0
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i)
  }
  return mismatch === 0
}

/**
 * Decode a standard base64 string to a fresh ArrayBuffer. Returns null when
 * the input is malformed (atob throws on illegal characters).
 *
 * We return an ArrayBuffer (not a Uint8Array view) because crypto.subtle
 * APIs are typed against `BufferSource` with the strict `ArrayBuffer`
 * variant under @cloudflare/workers-types — passing a typed-array view
 * runs afoul of `ArrayBufferLike` vs `ArrayBuffer` strictness.
 */
function base64ToArrayBuffer(input: string): ArrayBuffer | null {
  try {
    const binary = atob(input)
    const buffer = new ArrayBuffer(binary.length)
    const view = new Uint8Array(buffer)
    for (let i = 0; i < binary.length; i++) {
      view[i] = binary.charCodeAt(i)
    }
    return buffer
  } catch {
    return null
  }
}

/**
 * Encode a Uint8Array to standard base64.
 */
function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}
