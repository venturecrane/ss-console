/**
 * Captain notification email via Resend.
 *
 * One digest email per nightly run. The body lists each new alert with
 * customer, date, driver attribution, daily total, 7-day average, and
 * ratio. Includes a deep link to the cost dashboard so Captain can
 * snooze/ack from one click.
 *
 * Failure isolation: send failures are logged but do NOT block writes
 * to cost_anomaly_alerts. The dashboard banner is the source of truth;
 * email is a courtesy nudge.
 */

import { formatCents } from '../../../src/lib/format/money'

const RESEND_API_URL = 'https://api.resend.com/emails'

export interface AlertNotificationItem {
  customer_slug: string
  entity_name: string | null
  alert_date: string
  driver: string
  daily_cents: number
  rolling_avg_cents: number
  ratio_bps: number
}

export interface NotificationConfig {
  apiKey: string
  fromEmail: string
  toEmail: string
  dashboardUrl: string
}

export interface NotificationResult {
  ok: boolean
  reason?: string
  resendId?: string
}

export async function sendAnomalyDigest(
  config: NotificationConfig,
  alerts: AlertNotificationItem[],
  runDate: string
): Promise<NotificationResult> {
  if (alerts.length === 0) {
    return { ok: true, reason: 'no alerts to notify' }
  }

  const subject = `[SMD Ops] ${alerts.length} cost anomal${alerts.length === 1 ? 'y' : 'ies'} on ${runDate}`
  const html = renderDigestHtml(alerts, config.dashboardUrl, runDate)

  let response: Response
  try {
    response = await fetch(RESEND_API_URL, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: config.fromEmail,
        to: [config.toEmail],
        subject,
        html,
      }),
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, reason: `resend fetch failed: ${msg}` }
  }
  if (!response.ok) {
    const text = await safeText(response)
    return { ok: false, reason: `resend ${response.status}: ${text.slice(0, 200)}` }
  }
  let payload: { id?: string }
  try {
    payload = await response.json()
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, reason: `resend response parse failed: ${msg}` }
  }
  return { ok: true, resendId: payload.id }
}

function renderDigestHtml(
  alerts: AlertNotificationItem[],
  dashboardUrl: string,
  runDate: string
): string {
  const rows = alerts
    .map((a) => {
      const name = escapeHtml(a.entity_name ?? a.customer_slug)
      const driverLabel = a.driver === '' ? 'all drivers (aggregate)' : escapeHtml(a.driver)
      const daily = formatCents(a.daily_cents)
      const avg = formatCents(a.rolling_avg_cents)
      const ratio = `${(a.ratio_bps / 100).toFixed(0)}%`
      return `<tr>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">${name}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">${escapeHtml(a.alert_date)}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">${driverLabel}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:right;">${daily}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:right;">${avg}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:600;">${ratio}</td>
      </tr>`
    })
    .join('')
  const safeUrl = escapeHtml(dashboardUrl)
  return `<!doctype html>
<html><body style="font-family:-apple-system,system-ui,sans-serif;color:#111;max-width:720px;margin:0 auto;padding:24px;">
  <h2 style="margin:0 0 8px;">Operator — cost anomaly digest</h2>
  <p style="margin:0 0 16px;color:#4b5563;">${alerts.length} alert${alerts.length === 1 ? '' : 's'} detected on ${escapeHtml(runDate)}. Each row shows the customer's daily cost against the 7-day rolling average and the driver that contributed the largest delta.</p>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <thead>
      <tr style="background:#f9fafb;text-align:left;">
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;">Customer</th>
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;">Date</th>
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;">Top driver</th>
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;text-align:right;">Daily</th>
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;text-align:right;">7-day avg</th>
        <th style="padding:8px 12px;border-bottom:1px solid #d1d5db;text-align:right;">Ratio</th>
      </tr>
    </thead>
    <tbody>${rows}</tbody>
  </table>
  <p style="margin:20px 0 0;"><a href="${safeUrl}" style="color:#2563eb;">Open cost dashboard</a> to snooze or acknowledge.</p>
</body></html>`
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text()
  } catch {
    return ''
  }
}
