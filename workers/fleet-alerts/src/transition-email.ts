/**
 * The ALERT / RECOVERED email for a level condition. Moved out of index.ts
 * (2026-10-05) when threading the recovery under its alert pushed that file
 * past the 500-line ceiling; index.ts decides WHEN to send, this file owns
 * what the message says and how it threads (./alert-thread).
 */

import { threadHeaders } from './alert-thread'
import { conditionLabel, EDGE_DOWN_CONDITION } from './conditions'
import { alertHtml, alertWords } from './alert-words'
import { transitionIsQuiet } from './mail-policy'
import type { ConditionState, Env } from './index'

export async function sendTransitionEmail(
  env: Env,
  s: ConditionState,
  kind: 'opened' | 'resolved' | 'reminder',
  messageId: string | null
): Promise<{ ok: boolean; resendId?: string; quiet?: boolean }> {
  // Recorded and left for the triage agent, not emailed (./mail-policy).
  if (await transitionIsQuiet(env.DB, s.customer_slug, s.condition))
    return { ok: true, quiet: true }
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email ${kind} ${s.condition} for ${s.customer_slug}`)
    return { ok: false }
  }
  const words = alertWords(s.condition)
  // A recovery replies to its alert (same subject, In-Reply-To the alert's
  // Message-ID) so Gmail threads the pair as one inbox item. See ./alert-thread.
  const alertSubject = `[SMD Ops] ${s.customer_slug}: ${words.subject}`
  // A reminder (./reminder) threads under the alert like a recovery does.
  const standalone = kind === 'reminder' ? 'still open' : 'cleared'
  const subject =
    kind === 'opened'
      ? alertSubject
      : messageId
        ? `Re: ${alertSubject}`
        : `[SMD Ops] ${s.customer_slug}: ${standalone}: ${words.subject}`
  const happened =
    kind === 'resolved'
      ? 'This has cleared.'
      : kind === 'reminder'
        ? `Still not fixed. ${words.happened}`
        : words.happened
  // Escaped by alertHtml: connector_down details embed the seat's
  // `last_error_message`, which is arbitrary text from a customer Machine.
  const html = alertHtml(happened, kind === 'resolved' ? 'Nothing.' : words.todo, [
    `${s.condition === EDGE_DOWN_CONDITION ? 'Host' : 'Seat'}: ${s.customer_slug}`,
    `Condition: ${conditionLabel(s.condition)}`,
    `Detail: ${s.detail}`,
  ])
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
