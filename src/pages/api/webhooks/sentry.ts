/**
 * POST /api/webhooks/sentry
 *
 * Sentry alert-rule webhook receiver (ADR 0023 Wave 1). Configured as
 * an Internal Integration webhook in the shared `smd-operator` Sentry
 * project; each customer's alert rules POST here when they fire.
 *
 * Auth: HMAC-SHA256 over the raw body, signature in
 * `Sentry-Hook-Signature` header, key = `SENTRY_WEBHOOK_SECRET`
 * (the Internal Integration's Client Secret). Replay protection via
 * `Sentry-Hook-Timestamp` header — events older than 5 minutes rejected.
 *
 * Ref: https://docs.sentry.io/organization/integrations/integration-platform/webhooks/
 *
 * On valid delivery, writes one `cost_anomaly_alerts` row with
 * `source='sentry'` so the admin dashboard banner surfaces it alongside
 * cost-anomaly rows (single alerts surface per ADR 0023 §"Cross-cutting
 * calls" #9). Cost-specific columns (`daily_cents` etc.) carry 0 sentinels
 * for non-cost rows; the dashboard reader switches on `source` for display.
 *
 * Tenant identification: the alert payload's `installation.uuid` or
 * `data.event.tags` carries the `tenant` tag set at SDK init in the
 * overlay (`sentry-sdk.set_tag('tenant', customer_id)`). Wave 1 expects
 * each Sentry alert rule to be configured per-customer with a `tenant`
 * tag filter, so the delivered payload always includes the tag — we
 * read it from the event tags directly. If the tag is missing the row
 * is rejected (400) rather than misattributed.
 */

import { jsonResponse, errorResponse } from '../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'

const MAX_WEBHOOK_AGE_SECONDS = 300

interface SentryWebhookPayload {
  action?: string
  data?: {
    event?: { tags?: Array<[string, string]>; event_id?: string; title?: string }
    issue?: { id?: string; shortId?: string; title?: string }
  }
}

export const POST: APIRoute = async ({ request }) => {
  const secret = env.SENTRY_WEBHOOK_SECRET
  if (!secret) {
    console.error('[webhook/sentry] SENTRY_WEBHOOK_SECRET not configured')
    return errorResponse(500, 'server_misconfigured')
  }

  const rawBody = await request.text()
  const signatureHeader = request.headers.get('sentry-hook-signature') ?? ''
  const timestampHeader = request.headers.get('sentry-hook-timestamp') ?? ''

  if (!signatureHeader) {
    return errorResponse(401, 'missing_signature')
  }

  if (!(await verifyHmac(rawBody, signatureHeader, secret))) {
    console.error('[webhook/sentry] invalid signature')
    return errorResponse(401, 'invalid_signature')
  }

  // The timestamp is the replay window. It is not bound into the HMAC (Sentry
  // signs the raw body only), so a missing or non-numeric header must be a
  // refusal, not a skipped check: otherwise `abc` replays a captured body
  // forever (2026-09-09 review, Security LOW 5).
  const timestampSec = Number(timestampHeader)
  if (timestampHeader.trim() === '' || !Number.isFinite(timestampSec)) {
    console.error('[webhook/sentry] missing or non-numeric timestamp header')
    return errorResponse(401, 'invalid_timestamp')
  }
  const ageSec = Math.floor(Date.now() / 1000) - timestampSec
  if (ageSec > MAX_WEBHOOK_AGE_SECONDS) {
    console.error(`[webhook/sentry] stale webhook (age ${ageSec}s)`)
    return errorResponse(401, 'stale')
  }

  let payload: SentryWebhookPayload
  try {
    payload = JSON.parse(rawBody) as SentryWebhookPayload
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  const tenant = extractTenantTag(payload)
  if (!tenant) {
    console.warn('[webhook/sentry] payload missing tenant tag; rejected')
    return errorResponse(400, 'missing_tenant_tag')
  }

  const entityRow = await env.DB.prepare(
    'SELECT entity_id FROM customer_configs WHERE customer_slug = ?'
  )
    .bind(tenant)
    .first<{ entity_id: string }>()
  if (!entityRow) {
    console.warn(`[webhook/sentry] tenant ${tenant} not found in customer_configs`)
    return errorResponse(404, 'unknown_tenant')
  }

  const summary = buildSummary(payload)
  const alertDate = new Date().toISOString().slice(0, 10)

  await env.DB.prepare(
    `INSERT INTO cost_anomaly_alerts (
       entity_id, customer_slug, alert_date, driver, source,
       daily_cents, rolling_avg_cents, ratio_bps, threshold_bps,
       summary, details_json, detected_at
     ) VALUES (?, ?, ?, '', 'sentry', 0, 0, 0, 0, ?, ?, datetime('now'))
     ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET
       summary       = excluded.summary,
       details_json  = excluded.details_json,
       detected_at   = excluded.detected_at`
  )
    .bind(entityRow.entity_id, tenant, alertDate, summary, rawBody)
    .run()

  return jsonResponse(200, { ok: true, source: 'sentry', tenant })
}

function extractTenantTag(payload: SentryWebhookPayload): string | null {
  const tags = payload.data?.event?.tags
  if (!Array.isArray(tags)) return null
  for (const entry of tags) {
    if (Array.isArray(entry) && entry[0] === 'tenant' && typeof entry[1] === 'string') {
      return entry[1]
    }
  }
  return null
}

function buildSummary(payload: SentryWebhookPayload): string {
  const issueTitle = payload.data?.issue?.title ?? payload.data?.event?.title
  const shortId = payload.data?.issue?.shortId
  if (issueTitle && shortId) return `Sentry ${shortId}: ${issueTitle}`
  if (issueTitle) return `Sentry alert: ${issueTitle}`
  return `Sentry alert (action: ${payload.action ?? 'unknown'})`
}

async function verifyHmac(rawBody: string, signatureHex: string, secret: string): Promise<boolean> {
  const encoder = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, encoder.encode(rawBody))
  const digest = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')

  if (digest.length !== signatureHex.length) return false
  let mismatch = 0
  for (let i = 0; i < digest.length; i++) {
    mismatch |= digest.charCodeAt(i) ^ signatureHex.charCodeAt(i)
  }
  return mismatch === 0
}
