/**
 * Resend email client for transactional emails.
 *
 * Sends emails via the Resend API (https://resend.com/docs/api-reference).
 * In dev/test environments (no RESEND_API_KEY), logs emails to console instead.
 *
 * Sender name is sourced from BRAND_NAME (../config/brand.ts).
 */

import { BRAND_NAME } from '../config/brand'

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
