/**
 * Alert-sink delivery for the fleet-alerts Worker (migration 0095).
 *
 * `cost_anomaly_alerts` is the shared alert sink: the Sentry and healthchecks
 * webhook receivers write rows here (source != 'cost'), but nothing pushed them
 * anywhere — they surfaced only as an admin dashboard banner. This module is
 * the push path. See the Worker header for the incident that motivated it.
 *
 * These rows are event-shaped, not condition-shaped: a Sentry issue has no
 * green state to transition back to, so they are one-shot notifications marked
 * via `notified_at` rather than tracked in `fleet_alert_state`.
 */

import { seatIsQuiet, sinkSourceIsQuiet } from './mail-policy'
import { escapeHtml } from './html'
import type { Env } from './index'

/**
 * One alert-sink row delivered to the ops inbox.
 */
export interface SinkNotification {
  customer_slug: string
  source: string
  summary: string
  emailed: boolean
  resendId?: string
}

/**
 * Max sink rows delivered per run. A bounded batch keeps a flood of webhook
 * rows from turning one cron tick into hundreds of Resend calls; the remainder
 * is picked up on the next run two minutes later. Undelivered rows are never
 * dropped, only deferred.
 */
export const SINK_NOTIFY_BATCH = 10

/**
 * Deliver undelivered alert-sink rows (source != 'cost') to the ops inbox.
 *
 * Cost rows are excluded because the cost-anomaly Worker already emails its own
 * alerts inside its nightly run — including them here would double-send.
 *
 * `notified_at` is stamped ONLY after the email actually sent, so a Resend
 * outage retries on the next cron instead of silencing the alert forever
 * (ADR 0079 doctrine #5). A row whose send fails stays undelivered and is
 * retried; it is never marked and never dropped.
 *
 * Fail-soft per row: one malformed row cannot abort delivery of the others,
 * for the same reason per-seat evaluation is isolated above.
 */
export async function notifySinkAlerts(env: Env): Promise<SinkNotification[]> {
  const out: SinkNotification[] = []
  let rows: SinkAlertRow[]
  try {
    const res = await env.DB.prepare(
      `SELECT rowid AS rowid, customer_slug, source, summary, alert_date, driver, entity_id
         FROM cost_anomaly_alerts
        WHERE notified_at IS NULL AND source != 'cost'
        ORDER BY detected_at ASC
        LIMIT ?`
    )
      .bind(SINK_NOTIFY_BATCH)
      .all<SinkAlertRow>()
    rows = res.results ?? []
  } catch (err) {
    console.error('[fleet-alerts] sink notify query failed:', err)
    return out
  }

  for (const row of rows) {
    try {
      const summary = row.summary ?? `${row.source} alert`
      // Recorded and left for the triage agent, not emailed (./mail-policy).
      const quiet = sinkSourceIsQuiet(row.source) || (await seatIsQuiet(env.DB, row.customer_slug))
      const sent = quiet
        ? { ok: true, resendId: undefined }
        : await sendSinkEmail(env, row.customer_slug, row.source, summary)
      if (sent.ok) {
        await env.DB.prepare(
          `UPDATE cost_anomaly_alerts SET notified_at = datetime('now') WHERE rowid = ?`
        )
          .bind(row.rowid)
          .run()
      }
      out.push({
        customer_slug: row.customer_slug,
        source: row.source,
        summary,
        emailed: sent.ok && !quiet,
        resendId: sent.resendId,
      })
    } catch (err) {
      console.error('[fleet-alerts] sink notify failed for row', row.rowid, err)
    }
  }
  return out
}

interface SinkAlertRow {
  rowid: number
  customer_slug: string
  source: string
  summary: string | null
  alert_date: string
  driver: string
  entity_id: string
}

const SOURCE_LABEL: Record<string, string> = {
  sentry: 'Sentry issue alert',
  healthchecks: 'healthchecks.io grace expired',
  audit_integrity: 'Audit-log integrity violation',
}

async function sendSinkEmail(
  env: Env,
  slug: string,
  source: string,
  summary: string
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email ${source} alert for ${slug}: ${summary}`)
    return { ok: false }
  }
  const label = SOURCE_LABEL[source] ?? `${source} alert`
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  const html =
    `<p><strong>ALERT</strong>: ${escapeHtml(label)}</p>` +
    `<ul><li>Seat: ${escapeHtml(slug)}</li>` +
    `<li>Detail: ${escapeHtml(summary)}</li></ul>` +
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
        subject: `[SMD Ops] ALERT ${slug}: ${label}`,
        html,
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] sink email send failed:', err)
    return { ok: false }
  }
}
