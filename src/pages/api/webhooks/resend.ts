import type { APIContext, APIRoute } from 'astro'
import { z } from 'zod'
import { env } from 'cloudflare:workers'
import { handleResendEvent, type ResendWebhookPayload } from '../../../lib/webhooks/resend-handler'
import { handleBookingEmailDeliveryFailure } from '../../../lib/webhooks/booking-email-failure'
import { errorResponse, jsonResponse } from '../../../lib/api/helpers'
import { captureError } from '../../../lib/observability/sentry'
import { constantTimeEqual } from '../../../lib/auth/constant-time'

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

const ResendWebhookPayloadSchema = z
  .object({
    type: z.string().min(1),
    data: z
      .object({
        email_id: z.string().optional(),
        to: z.array(z.string()).optional(),
        from: z.string().optional(),
        subject: z.string().optional(),
        tags: z.record(z.string(), z.string()).optional(),
        bounce: z
          .object({
            message: z.string().optional(),
            type: z.string().optional(),
            subType: z.string().optional(),
          })
          .optional(),
        suppressed: z
          .object({ message: z.string().optional(), type: z.string().optional() })
          .optional(),
        failed: z.object({ reason: z.string().optional() }).optional(),
      })
      .catchall(z.unknown())
      .optional(),
  })
  .catchall(z.unknown())

function jsonErr(status: number, message: string): Response {
  return errorResponse(status, message)
}

async function verifySvixHeaders(
  request: Request,
  rawBody: string,
  webhookSecret: string
): Promise<Response | { svixId: string }> {
  const svixId = request.headers.get('svix-id')
  const svixTimestamp = request.headers.get('svix-timestamp')
  const svixSignature = request.headers.get('svix-signature')

  if (!svixId || !svixTimestamp || !svixSignature) {
    return jsonErr(400, 'Missing svix headers')
  }

  const tsSeconds = parseInt(svixTimestamp, 10)
  if (!Number.isFinite(tsSeconds)) return jsonErr(400, 'Invalid timestamp')

  const nowSeconds = Math.floor(Date.now() / 1000)
  if (Math.abs(nowSeconds - tsSeconds) > MAX_WEBHOOK_AGE_SECONDS) {
    console.error(`[webhook/resend] Stale webhook: ts=${tsSeconds}, now=${nowSeconds}`)
    return jsonErr(401, 'Stale webhook')
  }

  const isValid = await verifySvixSignature(
    svixId,
    svixTimestamp,
    rawBody,
    svixSignature,
    webhookSecret
  )
  if (!isValid) {
    console.error('[webhook/resend] Invalid signature')
    return jsonErr(401, 'Invalid signature')
  }

  return { svixId }
}

async function handlePost({ request }: APIContext): Promise<Response> {
  const webhookSecret = env.RESEND_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/resend] RESEND_WEBHOOK_SECRET not configured')
    return jsonErr(500, 'Server misconfigured')
  }

  // Read the raw body BEFORE parsing — Svix signs the exact bytes.
  const rawBody = await request.text()

  const headerResult = await verifySvixHeaders(request, rawBody, webhookSecret)
  if (headerResult instanceof Response) return headerResult

  let rawPayload: unknown
  try {
    rawPayload = JSON.parse(rawBody) as unknown
  } catch {
    return jsonErr(400, 'Invalid JSON')
  }

  const payloadResult = ResendWebhookPayloadSchema.safeParse(rawPayload)
  if (!payloadResult.success) return jsonErr(400, 'Malformed event payload')
  const payload: ResendWebhookPayload = payloadResult.data

  try {
    const result = await handleResendEvent(env.DB, {
      providerEventId: headerResult.svixId,
      payload,
      fallbackOrgId: DEFAULT_ORG_ID,
    })
    // Independently, alert the team if this event is a delivery failure for a
    // transactional booking email (suppressed/bounced/failed). Best-effort.
    const bookingFailure = await handleBookingEmailDeliveryFailure(
      env.DB,
      env.RESEND_API_KEY,
      payload
    )
    return jsonResponse(200, {
      ok: true,
      recorded: result.recorded,
      ...(result.reason ? { reason: result.reason } : {}),
      ...(result.eventType ? { event_type: result.eventType } : {}),
      ...(bookingFailure.handled ? { booking_alert: true } : {}),
    })
  } catch (err) {
    console.error('[webhook/resend] handler failed:', err)
    captureError(err, 'webhook.resend')
    // 500 → Svix retries with backoff.
    return jsonErr(500, 'Internal error')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)

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
    captureError(err, 'webhook.resend.import-key')
    return false
  }

  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(signedContent))
  const expectedSignature = bytesToBase64(new Uint8Array(mac))

  // svix-signature: `v1,sig1 v1,sig2 v2,sig3` — accept ANY v1 match.
  const candidates = signatureHeader.split(' ')
  for (const candidate of candidates) {
    const [version, sig] = candidate.split(',', 2)
    if (version !== 'v1' || !sig) continue
    if (constantTimeEqual(expectedSignature, sig)) {
      return true
    }
  }

  return false
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
