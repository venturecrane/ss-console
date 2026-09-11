/**
 * POST /api/webhooks/healthchecks
 *
 * Healthchecks.io grace-expiration webhook receiver (ADR 0023 Wave 1).
 * Each per-customer healthchecks.io check is configured (in the
 * healthchecks.io UI at provision time per PR 5) to POST to this
 * endpoint when its grace period expires.
 *
 * Auth: SHARED BEARER TOKEN, not HMAC. Healthchecks.io does not sign
 * outbound webhooks — see the 2024-10-08 blog post "How Healthchecks.io
 * Sends Webhook Notifications." Their webhook configuration UI lets us
 * set user-defined headers, so SMD configures each check with an
 * `Authorization: Bearer <HEALTHCHECKS_WEBHOOK_SECRET>` header. The
 * receiver constant-time-compares against the env secret.
 *
 * Body (JSON, shape configured per check in healthchecks.io UI using
 * placeholders):
 *   {
 *     "tenant":     "<customer_slug>",
 *     "check_name": "<healthchecks.io check name>",
 *     "status":     "down" | "up",
 *     "raw_url":    "<healthchecks.io drill-down URL>"
 *   }
 *
 * On a valid `status='down'` (grace expired) delivery:
 *   1. Writes a `cost_anomaly_alerts` row with `source='healthchecks'`
 *      so the admin dashboard banner surfaces it.
 *   2. Updates `fleet_status.heartbeat_status='red'` for the tenant.
 *      Two writers (heartbeat endpoint derives status from freshness,
 *      this handler forces red on grace expiry) keep the dashboard
 *      column and the alert row from contradicting per ADR 0023
 *      implementation-plan §"Design Decisions" #3.
 *
 * On `status='up'` (recovery): writes a recovery row but does NOT clear
 * `heartbeat_status` — the next heartbeat-endpoint POST recomputes that
 * naturally. We don't try to be clever here.
 */

import { jsonResponse, errorResponse } from '../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'

interface HealthchecksWebhookPayload {
  tenant?: string
  check_name?: string
  status?: 'up' | 'down'
  raw_url?: string
}

export const POST: APIRoute = async ({ request }) => {
  const expected = env.HEALTHCHECKS_WEBHOOK_SECRET
  if (!expected) {
    console.error('[webhook/healthchecks] HEALTHCHECKS_WEBHOOK_SECRET not configured')
    return errorResponse(500, 'server_misconfigured')
  }

  if (!bearerMatches(request, expected)) {
    return errorResponse(401, 'unauthorized')
  }

  let payload: HealthchecksWebhookPayload
  try {
    payload = await request.json<HealthchecksWebhookPayload>()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  const tenant = payload.tenant?.trim()
  const status = payload.status
  if (!tenant || (status !== 'up' && status !== 'down')) {
    return errorResponse(400, 'missing_tenant_or_status')
  }

  const entityRow = await env.DB.prepare(
    'SELECT entity_id FROM customer_configs WHERE customer_slug = ?'
  )
    .bind(tenant)
    .first<{ entity_id: string }>()
  if (!entityRow) {
    return errorResponse(404, 'unknown_tenant')
  }

  const summary =
    status === 'down'
      ? `Heartbeat grace expired: ${payload.check_name ?? tenant}`
      : `Heartbeat recovered: ${payload.check_name ?? tenant}`
  const alertDate = new Date().toISOString().slice(0, 10)

  await env.DB.prepare(
    `INSERT INTO cost_anomaly_alerts (
       entity_id, customer_slug, alert_date, driver, source,
       daily_cents, rolling_avg_cents, ratio_bps, threshold_bps,
       summary, details_json, detected_at
     ) VALUES (?, ?, ?, '', 'healthchecks', 0, 0, 0, 0, ?, ?, datetime('now'))
     ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET
       summary      = excluded.summary,
       details_json = excluded.details_json,
       detected_at  = excluded.detected_at`
  )
    .bind(entityRow.entity_id, tenant, alertDate, summary, JSON.stringify(payload))
    .run()

  if (status === 'down') {
    await env.DB.prepare(
      `UPDATE fleet_status
          SET heartbeat_status = 'red', updated_at = datetime('now')
        WHERE entity_id = ?`
    )
      .bind(entityRow.entity_id)
      .run()
  }

  return jsonResponse(200, { ok: true, source: 'healthchecks', tenant, status })
}

function bearerMatches(request: Request, expected: string): boolean {
  const auth = request.headers.get('Authorization') ?? ''
  if (!auth.startsWith('Bearer ')) return false
  const provided = auth.slice('Bearer '.length)
  if (provided.length !== expected.length) return false
  let mismatch = 0
  for (let i = 0; i < provided.length; i++) {
    mismatch |= provided.charCodeAt(i) ^ expected.charCodeAt(i)
  }
  return mismatch === 0
}
