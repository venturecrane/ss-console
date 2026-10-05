/**
 * The ALERT / RECOVERED email for a level condition. Moved out of index.ts
 * (2026-10-05) when threading the recovery under its alert pushed that file
 * past the 500-line ceiling; index.ts decides WHEN to send, this file owns
 * what the message says and how it threads (./alert-thread).
 */

import { threadHeaders } from './alert-thread'
import { conditionLabel, EDGE_DOWN_CONDITION } from './conditions'
import { escapeHtml } from './html'
import type { ConditionState, Env } from './index'

export async function sendTransitionEmail(
  env: Env,
  s: ConditionState,
  kind: 'opened' | 'resolved',
  messageId: string | null
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email ${kind} ${s.condition} for ${s.customer_slug}`)
    return { ok: false }
  }
  const label = conditionLabel(s.condition)
  // A recovery replies to its alert (same subject, In-Reply-To the alert's
  // Message-ID) so Gmail threads the pair as one inbox item. See ./alert-thread.
  const alertSubject = `[SMD Ops] ALERT ${s.customer_slug}: ${label}`
  const subject =
    kind === 'opened'
      ? alertSubject
      : messageId
        ? `Re: ${alertSubject}`
        : `[SMD Ops] RECOVERED ${s.customer_slug}: ${label}`
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  // Escaped: connector_down details embed the seat's `last_error_message`,
  // which is arbitrary text from a customer Machine.
  const html =
    `<p><strong>${kind === 'opened' ? 'ALERT' : 'RECOVERED'}</strong>: ${escapeHtml(label)}</p>` +
    `<ul><li>${s.condition === EDGE_DOWN_CONDITION ? 'Host' : 'Seat'}: ${escapeHtml(s.customer_slug)}</li>` +
    `<li>Detail: ${escapeHtml(s.detail)}</li>` +
    `<li>Severity: SEV1 per ADR 0064 - work begins on detection</li></ul>` +
    `<p><a href="${dashboard}">Fleet dashboard</a>. No automatic action was taken (ADR 0064/0065).</p>`
  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: env.ALERT_FROM_EMAIL ?? 'SMD Services Ops <team@smd.services>',
        to: env.ALERT_TO_EMAIL ?? 'team@smd.services',
        subject,
        html,
        headers: threadHeaders(kind, messageId),
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] resend send failed:', err)
    return { ok: false }
  }
}
