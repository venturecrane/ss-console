/**
 * Sentry 24h error-count sync for the Operator fleet (ADR 0023 Wave 1).
 *
 * For each Operator customer, query Sentry's Discover Events endpoint
 * for `count()` of events in the last 24h filtered by `tenant:<slug>`,
 * then UPSERT the value into `fleet_status.sentry_errors_last_24h` +
 * `sentry_errors_synced_at`.
 *
 * Shape of the call (per https://docs.sentry.io/api/):
 *   GET https://sentry.io/api/0/organizations/{org}/events/
 *     ?field=count()&query=tenant:<slug>&statsPeriod=24h&project={project_id}
 *   Authorization: Bearer <SENTRY_AUTH_TOKEN>
 *
 * Response shape (success):
 *   { "data": [{ "count()": N }] }
 *
 * Failure handling:
 *   - Missing env (no SENTRY_AUTH_TOKEN / SENTRY_ORG_SLUG / SENTRY_PROJECT_ID)
 *     → skip entirely; log once at INFO. Lets PR 4 ship before PR 5 wires
 *     the credentials.
 *   - Per-customer HTTP error / parse error → write `sentry_errors_last_24h
 *     = NULL` (preserving the row's existing value would silently surface
 *     stale data; null + an absent `synced_at` is honest about the gap).
 *   - The dashboard renders "—" when the column is null (existing
 *     empty-state pattern; never fabricates zero).
 *
 * No customer-level retry. The cron runs daily; transient failure today
 * means tomorrow's sync overwrites the null.
 */

const SENTRY_API_BASE = 'https://sentry.io/api/0'

export interface SentrySyncEnv {
  SENTRY_AUTH_TOKEN?: string
  SENTRY_ORG_SLUG?: string
  SENTRY_PROJECT_ID?: string
}

export interface SentrySyncResult {
  customer_slug: string
  status: 'ok' | 'http_error' | 'parse_error' | 'unavailable'
  count: number | null
  reason?: string
}

interface SentryEventsResponse {
  data?: Array<Record<string, unknown>>
}

/**
 * Fetch the 24h tenant-filtered event count for one customer. Returns
 * `null` on any failure path; the writer treats null as "couldn't sync"
 * and renders "—" in the dashboard. Never throws.
 */
export async function fetchTenantErrorsLast24h(
  env: SentrySyncEnv,
  tenantSlug: string,
  fetchImpl: typeof fetch = fetch
): Promise<SentrySyncResult> {
  if (!env.SENTRY_AUTH_TOKEN || !env.SENTRY_ORG_SLUG || !env.SENTRY_PROJECT_ID) {
    return {
      customer_slug: tenantSlug,
      status: 'unavailable',
      count: null,
      reason: 'sentry env not configured',
    }
  }

  const params = new URLSearchParams({
    field: 'count()',
    query: `tenant:${tenantSlug}`,
    statsPeriod: '24h',
    project: env.SENTRY_PROJECT_ID,
  })
  const url = `${SENTRY_API_BASE}/organizations/${encodeURIComponent(env.SENTRY_ORG_SLUG)}/events/?${params.toString()}`

  let response: Response
  try {
    response = await fetchImpl(url, {
      headers: { Authorization: `Bearer ${env.SENTRY_AUTH_TOKEN}` },
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { customer_slug: tenantSlug, status: 'http_error', count: null, reason: msg }
  }

  if (!response.ok) {
    return {
      customer_slug: tenantSlug,
      status: 'http_error',
      count: null,
      reason: `HTTP ${response.status}`,
    }
  }

  let payload: SentryEventsResponse
  try {
    payload = await response.json<SentryEventsResponse>()
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { customer_slug: tenantSlug, status: 'parse_error', count: null, reason: msg }
  }

  const count = extractCount(payload)
  return { customer_slug: tenantSlug, status: 'ok', count }
}

function extractCount(payload: SentryEventsResponse): number | null {
  const rows = payload.data
  if (!Array.isArray(rows) || rows.length === 0) return 0
  const first = rows[0]
  if (!first || typeof first !== 'object') return null
  // Discover returns count() under multiple keys depending on response
  // version: 'count', 'count()', or 'count_unique(...)'. Accept either of
  // the two plain shapes; anything else means we got an unexpected
  // payload and should null.
  const raw = first['count()'] ?? first['count']
  if (typeof raw === 'number' && Number.isFinite(raw)) return Math.max(0, Math.floor(raw))
  if (typeof raw === 'string') {
    const n = Number(raw)
    if (Number.isFinite(n)) return Math.max(0, Math.floor(n))
  }
  return null
}

export interface FleetStatusWriter {
  prepare: (sql: string) => {
    bind: (...args: (string | number | null)[]) => { run: () => Promise<unknown> }
  }
}

/**
 * Upsert the Sentry sync result into `fleet_status`. Always sets
 * `sentry_errors_synced_at = now` on a successful API call so the
 * dashboard's freshness tooltip is honest. On null count we still
 * stamp the synced_at — the value IS the most recent sync; nothing
 * else surfaces "we couldn't read Sentry today."
 *
 * Uses INSERT...ON CONFLICT to bootstrap a fleet_status row if one
 * doesn't exist yet (e.g. the Machine has never heartbeat'd but the
 * customer is configured). The heartbeat endpoint's later upsert
 * preserves the Sentry columns via COALESCE merging on its side.
 *
 * Keyed on customer_slug, the table's PRIMARY KEY since migration 0093
 * (several seats share one entity, so entity_id is a plain indexed column).
 * This writer named ON CONFLICT(entity_id) until 2026-10-01; SQLite rejects a
 * conflict target that is not a PK or UNIQUE constraint, so every nightly
 * sync failed and the dashboard's Sentry column froze at 2026-07-23
 * (vfy_01M3W2CDFFHMM293YYZMRV74JJ). entity_id is refreshed from the
 * request on every upsert, the same way the heartbeat writer does it.
 */
export async function writeSentrySync(
  db: FleetStatusWriter,
  entityId: string,
  customerSlug: string,
  result: SentrySyncResult,
  nowIso: string
): Promise<void> {
  if (result.status === 'unavailable') return // nothing to write when Sentry isn't configured
  await db
    .prepare(
      `INSERT INTO fleet_status (
         entity_id, customer_slug,
         sentry_errors_last_24h, sentry_errors_synced_at,
         heartbeat_status, updated_at
       ) VALUES (?, ?, ?, ?, 'unknown', ?)
       ON CONFLICT(customer_slug) DO UPDATE SET
         entity_id               = excluded.entity_id,
         sentry_errors_last_24h  = excluded.sentry_errors_last_24h,
         sentry_errors_synced_at = excluded.sentry_errors_synced_at,
         updated_at              = excluded.updated_at`
    )
    .bind(entityId, customerSlug, result.count, nowIso, nowIso)
    .run()
}
