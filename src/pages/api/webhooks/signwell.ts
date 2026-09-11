import type { APIRoute } from 'astro'
import { z } from 'zod'
import type { SignWellWebhookPayload } from '../../../lib/signwell/types'
import { handleDocumentCompleted } from '../../../lib/webhooks/signwell-handler'
import { env } from 'cloudflare:workers'
import { errorResponse, jsonResponse } from '../../../lib/api/helpers'

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

const SignWellEventTypeSchema = z.enum([
  'document_completed',
  'document_expired',
  'document_cancelled',
  'document_created',
  'document_sent',
  'document_viewed',
  'document_signed',
  'document_declined',
  'document_bounced',
  'document_error',
  'document_in_progress',
  'document_recipients_updated',
])

const SignWellWebhookPayloadSchema = z.object({
  event: z.object({
    type: SignWellEventTypeSchema,
    time: z.number(),
    hash: z.string().min(1),
    related_signer: z.object({ email: z.string(), name: z.string() }).optional(),
  }),
  data: z.object({
    object: z.object({
      id: z.string().min(1),
      name: z.string(),
      status: z.string(),
      signers: z
        .array(
          z.object({
            id: z.string(),
            name: z.string(),
            email: z.string(),
            signed_at: z.string().nullable(),
          })
        )
        .optional(),
      completed_at: z.string().nullable(),
    }),
    account_id: z.string(),
  }),
})

const SignWellVerificationFieldsSchema = z.object({
  event: z.object({
    type: SignWellEventTypeSchema,
    time: z.number(),
    hash: z.string().min(1),
  }),
})

export const POST: APIRoute = async ({ request }) => {
  const webhookSecret = env.SIGNWELL_WEBHOOK_SECRET
  if (!webhookSecret) {
    console.error('[webhook/signwell] SIGNWELL_WEBHOOK_SECRET not configured')
    return errorResponse(500, 'server_misconfigured')
  }

  // --- Parse body (required — SignWell puts the hash inside the JSON) ---
  let rawPayload: unknown
  try {
    const rawBody = await request.text()
    rawPayload = JSON.parse(rawBody) as unknown
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  // --- Extract verification fields only (no logging/dispatch yet) ---
  const verificationFields = SignWellVerificationFieldsSchema.safeParse(rawPayload)
  if (!verificationFields.success) {
    return errorResponse(400, 'validation_failed', 'Missing event fields.')
  }
  const eventType = verificationFields.data.event.type
  const eventTime = verificationFields.data.event.time
  const eventHash = verificationFields.data.event.hash

  // --- HMAC-SHA256 verification ---
  const isValid = await verifyEventHash(eventType, eventTime, eventHash, webhookSecret)
  if (!isValid) {
    console.error('[webhook/signwell] Invalid event hash')
    return errorResponse(401, 'invalid_signature')
  }

  // --- Timestamp freshness check (replay protection) ---
  const nowSeconds = Math.floor(Date.now() / 1000)
  if (nowSeconds - eventTime > MAX_WEBHOOK_AGE_SECONDS) {
    console.error(`[webhook/signwell] Stale webhook: event.time ${eventTime}, now ${nowSeconds}`)
    return errorResponse(401, 'stale', 'Stale webhook.')
  }

  const payloadResult = SignWellWebhookPayloadSchema.safeParse(rawPayload)
  if (!payloadResult.success) {
    return errorResponse(400, 'validation_failed', 'Malformed event payload.')
  }
  const payload: SignWellWebhookPayload = payloadResult.data

  // --- Dispatch by event type ---
  if (payload.event.type === 'document_completed') {
    const apiKey = env.SIGNWELL_API_KEY
    if (!apiKey) {
      console.error('[webhook/signwell] SIGNWELL_API_KEY not configured')
      return errorResponse(500, 'server_misconfigured')
    }

    return handleDocumentCompleted(
      {
        db: env.DB,
        storage: env.STORAGE,
        apiKey,
        resendApiKey: env.RESEND_API_KEY,
        stripeApiKey: env.STRIPE_API_KEY,
        appBaseUrl: env.APP_BASE_URL,
      },
      payload
    )
  }

  // Acknowledge all other events without processing
  return jsonResponse(200, { ok: true, event: payload.event.type })
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
