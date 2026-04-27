/**
 * Resend email client for transactional and outreach emails.
 *
 * Sends emails via the Resend API (https://resend.com/docs/api-reference).
 * In dev/test environments (no RESEND_API_KEY), logs emails to console instead.
 *
 * Sender name is sourced from BRAND_NAME (../config/brand.ts).
 *
 * ## Outreach attribution (issue #587)
 *
 * `sendOutreachEmail` is a thin wrapper around the same fetch path that:
 *   1. Threads `entity_id` from the caller through to a 'sent' row in
 *      `outreach_events`, so every downstream event (open/click/bounce/
 *      reply) can be re-attributed to the originating signal by the
 *      webhook handler. See src/pages/api/webhooks/resend.ts.
 *   2. Records the synthetic 'sent' row immediately after Resend returns
 *      success — we do not wait for the `email.sent` webhook because the
 *      send-time attribution data (entity_id, org_id) is only known here.
 *   3. Relies on Resend's native open/click tracking — Resend rewrites
 *      links and injects a pixel server-side when tracking is enabled on
 *      the project. We do NOT inject our own pixel; double tracking would
 *      double-count opens. See https://resend.com/docs/dashboard/emails/tracking.
 *
 * The base `sendEmail` API is unchanged so existing callers (transactional
 * emails — booking, magic link, invoices, payment confirmations) keep
 * working without entity attribution.
 */

import { BRAND_NAME } from '../config/brand'
import { recordEvent } from '../db/outreach-events'

const RESEND_API_URL = 'https://api.resend.com/emails'
const SENDER = `${BRAND_NAME} <team@smd.services>`

export interface EmailAttachment {
  filename: string
  content: string // base64-encoded
  content_type?: string
}

export interface EmailPayload {
  to: string
  subject: string
  html: string
  reply_to?: string
  attachments?: EmailAttachment[]
}

export interface SendResult {
  success: boolean
  id?: string
  error?: string
}

/**
 * Send an email via Resend API.
 *
 * If RESEND_API_KEY is not set, logs the email to console (dev/test mode).
 */
export async function sendEmail(
  apiKey: string | undefined,
  payload: EmailPayload
): Promise<SendResult> {
  if (!apiKey) {
    // Dev/test mode — log instead of sending
    console.log('[email:dev] Would send email:')
    console.log(`  To: ${payload.to}`)
    console.log(`  Subject: ${payload.subject}`)
    console.log(`  Body length: ${payload.html.length} chars`)
    if (payload.attachments?.length) {
      console.log(`  Attachments: ${payload.attachments.map((a) => a.filename).join(', ')}`)
    }
    return { success: true, id: 'dev-mode' }
  }

  const apiPayload: Record<string, unknown> = {
    from: SENDER,
    to: [payload.to],
    subject: payload.subject,
    html: payload.html,
    ...(payload.reply_to ? { reply_to: payload.reply_to } : {}),
  }

  if (payload.attachments?.length) {
    apiPayload.attachments = payload.attachments.map((a) => ({
      filename: a.filename,
      content: a.content,
      ...(a.content_type ? { content_type: a.content_type } : {}),
    }))
  }

  const response = await fetch(RESEND_API_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(apiPayload),
  })

  if (!response.ok) {
    const body = await response.text()
    return { success: false, error: `Resend API error ${response.status}: ${body}` }
  }

  const data = (await response.json()) as { id: string }
  return { success: true, id: data.id }
}

// ===========================================================================
// Outreach send path (issue #587)
// ===========================================================================

export interface OutreachSendOptions {
  /** D1 binding used to record the synthetic 'sent' row. */
  db: D1Database
  /** Org id the send is attributed to. Required for tenant scoping. */
  orgId: string
  /**
   * Originating entity. Threaded through so the webhook handler can
   * re-attribute downstream events. May be null only for org-internal
   * outreach without a specific prospect target — never null for the
   * lead-gen send queue (#588).
   */
  entityId?: string | null
}

export interface OutreachSendResult extends SendResult {
  /**
   * Resolves to the row id in `outreach_events` for the synthetic 'sent'
   * event when the send succeeded. Undefined when send failed or when
   * the DB write itself failed (we still return SendResult.success=true
   * because the email did go out — observability is best-effort).
   */
  outreach_event_id?: string
}

/**
 * Send an outreach email and record a 'sent' row in `outreach_events`.
 *
 * The DB write is best-effort — a failure to record the event does NOT
 * fail the send (the email already went out, and rolling that back is
 * impossible). Failures are logged for monitoring; the missing row
 * surfaces as a gap in funnel attribution rather than a lost email.
 *
 * If RESEND_API_KEY is unset, the send is logged (dev mode) and a
 * 'sent' row is still recorded with a synthetic message_id so dev/test
 * flows exercise the full event path.
 */
export async function sendOutreachEmail(
  apiKey: string | undefined,
  payload: EmailPayload,
  options: OutreachSendOptions
): Promise<OutreachSendResult> {
  const sendResult = await sendEmail(apiKey, payload)
  if (!sendResult.success || !sendResult.id) {
    return sendResult
  }

  try {
    const eventResult = await recordEvent(options.db, {
      org_id: options.orgId,
      entity_id: options.entityId ?? null,
      event_type: 'sent',
      channel: 'email',
      message_id: sendResult.id,
      // No provider_event_id — this is a synthetic row recorded by the
      // send wrapper, not an inbound webhook. The eventual `email.sent`
      // webhook from Resend will dedupe against this row by message_id
      // semantics in the webhook handler.
      provider_event_id: null,
      payload: {
        to: payload.to,
        subject: payload.subject,
        recorded_by: 'send-wrapper',
      },
    })
    return { ...sendResult, outreach_event_id: eventResult.id }
  } catch (err) {
    // Best-effort: do not fail the send because the telemetry row failed.
    console.error('[email/outreach] failed to record sent event:', err)
    return sendResult
  }
}
