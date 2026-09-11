/**
 * POST /api/internal/runtime-summary
 *
 * Per-customer Operator Machine → control-plane runtime-summary push
 * (ADR 0043 path B). The Machine pushes a small read-relevant summary on a
 * cadence; this handler upserts the per-customer row in
 * `operator_runtime_summary` that the admin fleet view reads. Generalizes the
 * heartbeat ingestion pattern (src/pages/api/internal/heartbeat.ts) — same
 * shared-key + X-Tenant-Slug auth.
 *
 * Body (JSON), all fields validated, none cast:
 *   {
 *     "summary_status":     "green"|"yellow"|"red"|"unknown",  // optional, default unknown
 *     "open_alerts":         <integer ≥ 0>,                    // optional, default 0
 *     "draft_queue_depth":   <integer ≥ 0 | null>,             // optional
 *     "last_activity_ts":    <ISO 8601 UTC | null>,            // optional
 *     "pushed_at":           <ISO 8601 UTC>                    // required
 *   }
 *
 * Isolation (ADR 0009): the row is keyed to the authenticated tenant only;
 * one Machine, one row. No cross-customer surface here.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { verifyMachineRequest } from '../../../lib/auth/machine-key'
import type { SummaryStatus } from '../../../lib/admin/runtime-summary'
import { jsonResponse, errorResponse } from '../../../lib/api/helpers'

const STATUSES: ReadonlySet<string> = new Set(['green', 'yellow', 'red', 'unknown'])

interface SummaryBody {
  summary_status?: unknown
  open_alerts?: unknown
  draft_queue_depth?: unknown
  last_activity_ts?: unknown
  pushed_at?: unknown
}

function nonNegInt(v: unknown): number | null {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null
}

export const POST: APIRoute = async ({ request }) => {
  const auth = await verifyMachineRequest(request, env.MACHINE_HEARTBEAT_KEY, env.DB)
  if (!auth.ok) return errorResponse(401, 'unauthorized')

  let body: SummaryBody
  try {
    body = await request.json<SummaryBody>()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  if (typeof body.pushed_at !== 'string' || body.pushed_at.length === 0) {
    return errorResponse(400, 'missing_pushed_at')
  }

  const status: SummaryStatus =
    typeof body.summary_status === 'string' && STATUSES.has(body.summary_status)
      ? (body.summary_status as SummaryStatus)
      : 'unknown'
  const openAlerts = nonNegInt(body.open_alerts) ?? 0
  const draftDepth = nonNegInt(body.draft_queue_depth)
  const lastActivity =
    typeof body.last_activity_ts === 'string' && body.last_activity_ts.length > 0
      ? body.last_activity_ts
      : null

  await env.DB.prepare(
    `INSERT INTO operator_runtime_summary (
       entity_id, customer_slug, summary_status, open_alerts,
       draft_queue_depth, last_activity_ts, pushed_at, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
     ON CONFLICT(entity_id) DO UPDATE SET
       customer_slug      = excluded.customer_slug,
       summary_status     = excluded.summary_status,
       open_alerts        = excluded.open_alerts,
       draft_queue_depth  = excluded.draft_queue_depth,
       last_activity_ts   = COALESCE(excluded.last_activity_ts, operator_runtime_summary.last_activity_ts),
       pushed_at          = excluded.pushed_at,
       updated_at         = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`
  )
    .bind(auth.entityId, auth.slug, status, openAlerts, draftDepth, lastActivity, body.pushed_at)
    .run()

  return jsonResponse(200, { ok: true })
}
