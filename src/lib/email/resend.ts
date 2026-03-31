/**
 * Resend email client for transactional emails.
 *
 * Sends emails via the Resend API (https://resend.com/docs/api-reference).
 * In dev/test environments (no RESEND_API_KEY), logs emails to console instead.
 *
 * Sender: SMD Services <team@smd.services>
 */

const RESEND_API_URL = 'https://api.resend.com/emails'
const SENDER = 'SMD Services <team@smd.services>'

export interface EmailPayload {
  to: string
  subject: string
  html: string
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
    return { success: true, id: 'dev-mode' }
  }

  const response = await fetch(RESEND_API_URL, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: SENDER,
      to: [payload.to],
      subject: payload.subject,
      html: payload.html,
    }),
  })

  if (!response.ok) {
    const body = await response.text()
    return { success: false, error: `Resend API error ${response.status}: ${body}` }
  }

  const data = (await response.json()) as { id: string }
  return { success: true, id: data.id }
}
