/**
 * POST /api/webhooks/sentry
 *
 * Sentry -> SMD alert sink (ADR 0023 Wave 1). The `smd-ops-alert-bridge`
 * Internal Integration in the shared `smd-operator` project POSTs here; each
 * accepted delivery writes one `cost_anomaly_alerts` row with
 * `source='sentry'`, which the fleet-alerts Worker emails to team@smd.services
 * (`workers/fleet-alerts/src/sink-notify.ts`).
 *
 * WHY THIS ACCEPTS TWO PAYLOAD SHAPES (2026-10-05). The receiver was written
 * for alert-rule deliveries (`Sentry-Hook-Resource: event_alert`), whose
 * `data.event.tags` carries the `tenant` tag the overlay sets at SDK init. But
 * no alert rule ever pointed at the integration; it was subscribed to the
 * `issue` resource instead, whose payload carries no tags at all. Every
 * delivery from 2026-09-14 to 2026-10-01 was refused `missing_tenant_tag`
 * (19 of 19, read from Sentry's own delivery log), so no Operator error ever
 * reached anyone. Many issues also carry no tenant tag, or span several seats.
 * An unattributable error is still an error: it is now written against the
 * `fleet` slug and paged, never refused.
 *
 *   - `event_alert`: attributed to the `tenant` tag when it names a known seat,
 *     otherwise `fleet`.
 *   - `issue`: paged only when `SENTRY_ISSUE_RESOURCE` is `page` (the default
 *     until the alert rule exists; set `ignore` once it does, or every issue
 *     pages twice). Only `created` and `unresolved` page; assignment,
 *     resolution and archive are acknowledged and dropped.
 *   - anything else: acknowledged and dropped.
 *
 * One row per Sentry issue per day (`driver = sentry:<issue id>`). A repeat
 * delivery for the same issue clears `notified_at`, so a regression or a
 * re-fired alert pages again; the old upsert kept `notified_at`, which meant a
 * second error on the same seat on the same day was never emailed.
 *
 * Auth: HMAC-SHA256 over the raw body, signature in `Sentry-Hook-Signature`,
 * key = `SENTRY_WEBHOOK_SECRET` (the Internal Integration's Client Secret).
 * Replay protection via `Sentry-Hook-Timestamp`: older than 5 minutes is
 * refused.
 *
 * Ref: https://docs.sentry.io/organization/integrations/integration-platform/webhooks/
 */

import { jsonResponse, errorResponse, isRecord, parseJsonRecord } from '../../../lib/api/helpers'
import { misconfiguredResponse } from '../../../lib/api/failures'
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'

const MAX_WEBHOOK_AGE_SECONDS = 300

/** The slug an unattributable Sentry error is paged under. */
export const FLEET_SLUG = 'fleet'

/**
 * The seat whose entity owns `fleet` rows. The sink table keys every row to an
 * entity, and SMD's own seats roll up to SMD's own entity, so an error that
 * cannot be pinned to one client lands on SMD rather than on a client. Looked
 * up at runtime; a miss is a loud 500, never a silent drop.
 */
export const FLEET_ENTITY_SEAT = 'scott'

/** Issue actions that mean "something is broken now". */
const PAGING_ISSUE_ACTIONS = new Set(['created', 'unresolved'])

/** Step into a parsed JSON value by key; undefined when it is not an object. */
function field(value: unknown, key: string): unknown {
  return isRecord(value) ? value[key] : undefined
}

function stringField(value: unknown, key: string): string | undefined {
  const v = field(value, key)
  if (typeof v === 'string') return v
  if (typeof v === 'number') return String(v)
  return undefined
}

/** What a delivery is about, once its shape is known. */
export interface SentryDelivery {
  /** Sentry issue (group) id; the row's identity. */
  issueId: string
  /** Seat slug from the `tenant` tag, or null when there is none. */
  tenant: string | null
  summary: string
}

/**
 * Decide what a verified delivery means. Returns null for a delivery that is
 * acknowledged but does not page.
 */
export function interpretDelivery(
  resource: string,
  payload: Record<string, unknown>,
  issueResourceMode: string
): SentryDelivery | null {
  const action = stringField(payload, 'action') ?? ''
  const data = field(payload, 'data')
  if (resource === 'event_alert') {
    const event = field(data, 'event')
    const issueId = stringField(event, 'issue_id') ?? stringField(event, 'group_id')
    if (!issueId) return null
    return { issueId, tenant: extractTenantTag(event), summary: buildSummary(data) }
  }
  if (resource === 'issue') {
    if (issueResourceMode !== 'page') return null
    if (!PAGING_ISSUE_ACTIONS.has(action)) return null
    const issue = field(data, 'issue')
    const issueId = stringField(issue, 'id')
    if (!issueId) return null
    return { issueId, tenant: null, summary: buildSummary(data) }
  }
  return null
}

export const POST: APIRoute = async ({ request }) => {
  const secret = env.SENTRY_WEBHOOK_SECRET
  if (!secret) {
    return misconfiguredResponse('webhook/sentry', 'SENTRY_WEBHOOK_SECRET')
  }

  const rawBody = await request.text()
  const refused = await refuseUnverified(request, rawBody, secret)
  if (refused) return refused

  // Parsed as unknown and read field by field (review 2026-09-25, Code
  // Quality 2): the signature proves Sentry sent it, not its shape.
  const payload = parseJsonRecord(rawBody)
  if (!payload) return errorResponse(400, 'invalid_json')

  // An absent header is read as event_alert: that is the shape this receiver
  // was built for, and the one a hand-signed test delivery sends.
  const resource = request.headers.get('sentry-hook-resource') ?? 'event_alert'
  const delivery = interpretDelivery(resource, payload, env.SENTRY_ISSUE_RESOURCE ?? 'page')
  if (!delivery) {
    return jsonResponse(200, { ok: true, source: 'sentry', paged: false, resource })
  }

  const seat = await resolveSeat(delivery.tenant)
  if (!seat) {
    return misconfiguredResponse('webhook/sentry', `customer_configs row for ${FLEET_ENTITY_SEAT}`)
  }

  const alertDate = new Date().toISOString().slice(0, 10)
  await env.DB.prepare(
    `INSERT INTO cost_anomaly_alerts (
       entity_id, customer_slug, alert_date, driver, source,
       daily_cents, rolling_avg_cents, ratio_bps, threshold_bps,
       summary, details_json, detected_at
     ) VALUES (?, ?, ?, ?, 'sentry', 0, 0, 0, 0, ?, ?, datetime('now'))
     ON CONFLICT(entity_id, alert_date, driver) DO UPDATE SET
       summary       = excluded.summary,
       details_json  = excluded.details_json,
       detected_at   = excluded.detected_at,
       notified_at   = NULL`
  )
    .bind(
      seat.entityId,
      seat.slug,
      alertDate,
      `sentry:${delivery.issueId}`,
      delivery.summary,
      rawBody
    )
    .run()

  return jsonResponse(200, { ok: true, source: 'sentry', paged: true, tenant: seat.slug })
}

/**
 * The signature and replay checks. Returns the refusal, or null when the
 * delivery is Sentry's and fresh.
 */
async function refuseUnverified(
  request: Request,
  rawBody: string,
  secret: string
): Promise<Response | null> {
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
  return null
}

async function entityFor(slug: string): Promise<string | null> {
  const row = await env.DB.prepare('SELECT entity_id FROM customer_configs WHERE customer_slug = ?')
    .bind(slug)
    .first<{ entity_id: string }>()
  return row?.entity_id ?? null
}

/**
 * The seat a delivery pages under: the tenant it names when the console knows
 * that seat, otherwise `fleet` on SMD's own entity. Null only when SMD's own
 * seat is missing, which is a misconfiguration and must be loud.
 */
async function resolveSeat(
  tenant: string | null
): Promise<{ slug: string; entityId: string } | null> {
  if (tenant) {
    const entityId = await entityFor(tenant)
    if (entityId) return { slug: tenant, entityId }
    console.warn(`[webhook/sentry] tenant ${tenant} not in customer_configs; paging as fleet`)
  }
  const fleetEntity = await entityFor(FLEET_ENTITY_SEAT)
  return fleetEntity ? { slug: FLEET_SLUG, entityId: fleetEntity } : null
}

/** The `tenant` tag from an event's `[key, value]` tag pairs, or null. */
export function extractTenantTag(event: unknown): string | null {
  const tags = field(event, 'tags')
  if (!Array.isArray(tags)) return null
  const entries: unknown[] = tags
  for (const entry of entries) {
    if (Array.isArray(entry) && entry[0] === 'tenant' && typeof entry[1] === 'string') {
      const value = entry[1].trim()
      return value && value !== 'unknown' ? value : null
    }
  }
  return null
}

function buildSummary(data: unknown): string {
  const issue = field(data, 'issue')
  const event = field(data, 'event')
  const title = stringField(issue, 'title') ?? stringField(event, 'title')
  const shortId = stringField(issue, 'shortId')
  const url = stringField(event, 'web_url') ?? stringField(issue, 'web_url')
  const head =
    title && shortId ? `Sentry ${shortId}: ${title}` : title ? `Sentry: ${title}` : 'Sentry alert'
  return url ? `${head} (${url})` : head
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
