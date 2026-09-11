/**
 * POST /api/internal/heartbeat
 *
 * Per-customer Operator Machine → control-plane heartbeat ingestion
 * (ADR 0023 Wave 1). The overlay-side ticker in the Machine POSTs every
 * ~60s; this handler upserts the row in `fleet_status` and replies with
 * 200 + the resolved heartbeat status so the Machine can log it.
 *
 * Auth: per-seat Bearer key + X-Tenant-Slug header (see
 * `src/lib/auth/machine-key.ts`, migration 0114).
 *
 * This route is gate / parse / delegate. The body parsers live in
 * `src/lib/operator/heartbeat-parsers.ts`, the `fleet_status` upsert and the
 * freshness derivation in `src/lib/db/fleet-status.ts`, and the audit-loss
 * alert in `src/lib/operator/heartbeat-alerts.ts` (code review 2026-09-10,
 * Architecture 3).
 *
 * Body (JSON):
 *   {
 *     "heartbeat_ts":            <ISO 8601 UTC>,   // required
 *     "last_audit_ts":           <ISO 8601 UTC>,   // optional
 *     "last_skill_ts":           <ISO 8601 UTC>,   // optional
 *     "process_uptime_seconds":  <integer>,        // optional
 *     "version":                 <string>,         // optional
 *     "sticky_stop_level":       <string>,         // optional (ADR 0062)
 *     "sticky_stop_reason":      <string>,         // optional, only with a level
 *     "sticky_stop_condition":   <string>,         // optional, only with a level
 *     "scheduler_ok":            <boolean | 0/1>,  // optional (WP-2 work-liveness)
 *     "scheduler_job_count":     <integer>,        // optional
 *     "scheduler_max_overdue_seconds": <integer>,  // optional
 *     "connector_check_ok":      <boolean | 0/1>,  // optional (ADR 0080)
 *     "connectors":              <map server → entry> // optional (ADR 0080)
 *     "cron_containment":        <boolean | 0/1>,  // optional (ss#2276 sentinel)
 *     "webhook_surface_ok":      <boolean | 0/1>,  // optional (ss#2222 warn tier)
 *     "webhook_surface":         <map tool → entry> // optional (ss#2222 warn tier)
 *     "audit_write_failures":    <integer>,        // optional (ss#2498 lost rows, cumulative)
 *     "audit_head":              <64 hex chars>,   // optional (ss#2500 chain pin)
 *     "audit_rows":              <integer>,        // optional (ss#2500 chain pin)
 *     "send_refusals":           <integer>,        // optional (ss#2547 muted routine)
 *     "send_refusals_last_ts":   <ISO 8601 UTC>,   // optional (ss#2547 pager marker)
 *     "send_refusals_json":      <array of <=5>    // optional (ss#2547 newest events)
 *   }
 *
 * `audit_head` / `audit_rows` are the one pair here that does NOT land only in
 * `fleet_status`. They are also appended to `audit_head_history` (migration
 * 0108) as an off-Machine pin, because tail truncation of the seat's hash chain
 * is undetectable from an export alone -- see `src/lib/operator/audit-head.ts`.
 *
 * The authoritative list of fields the pinned overlay can emit is
 * `operator/observability/heartbeat-fields.json`, enforced by
 * `tests/heartbeat-field-parity.test.ts` — a field the seat sends that this
 * handler has no reader for is the ss#2287 defect class, and that gate is what
 * makes it fail a test instead of degrading silently.
 *
 * The handler doesn't trust the Machine's `heartbeat_status` — it derives
 * it from the freshness math the admin dashboard uses (green/yellow/red
 * thresholds based on customer.yaml.observability.health.period_seconds
 * and grace_minutes, defaults 60 and 5). Wave 1 hardcodes the same
 * thresholds the dashboard uses to avoid reading customer.yaml on every
 * heartbeat; the dashboard re-derives the color at render time anyway,
 * so any discrepancy is corrected on the next page load. The
 * healthchecks.io webhook handler additionally writes
 * `heartbeat_status='red'` on grace expiration so the alert path is the
 * authoritative red signal.
 */

import { jsonResponse, errorResponse } from '../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { verifyMachineRequest } from '../../../lib/auth/machine-key'
import {
  DEFAULT_GRACE_MINUTES,
  DEFAULT_PERIOD_SECONDS,
  deriveHeartbeatStatus,
  upsertFleetStatus,
} from '../../../lib/db/fleet-status'
import { recordAuditHead } from '../../../lib/operator/audit-head'
import { reportAuditWriteFailureDelta } from '../../../lib/operator/heartbeat-alerts'
import {
  parseObservability,
  parseStickyStop,
  type HeartbeatBody,
} from '../../../lib/operator/heartbeat-parsers'

export const POST: APIRoute = async ({ request }) => {
  const auth = await verifyMachineRequest(request, env.MACHINE_HEARTBEAT_KEY, env.DB)
  if (!auth.ok) {
    return errorResponse(401, 'unauthorized')
  }

  let body: HeartbeatBody
  try {
    body = await request.json<HeartbeatBody>()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  if (typeof body.heartbeat_ts !== 'string' || body.heartbeat_ts.length === 0) {
    return errorResponse(400, 'missing_heartbeat_ts')
  }

  const heartbeatStatus = deriveHeartbeatStatus(
    body.heartbeat_ts,
    DEFAULT_PERIOD_SECONDS,
    DEFAULT_GRACE_MINUTES
  )
  const stickyStop = parseStickyStop(body)
  const obs = parseObservability(body)

  // #2498: read the prior count BEFORE the upsert overwrites it. A rise means
  // the seat lost audit rows since the last beat — the state that is otherwise
  // indistinguishable from a seat with nothing to record.
  await reportAuditWriteFailureDelta(env.DB, auth.slug, obs.auditWriteFailures)

  // One parsed beat, spread into the upsert: every alert-driving field in
  // parseObservability reaches the row by name, and a field added there
  // cannot be forgotten here.
  await upsertFleetStatus(env.DB, {
    entityId: auth.entityId,
    slug: auth.slug,
    beat: {
      heartbeatTs: body.heartbeat_ts,
      lastAuditTs: typeof body.last_audit_ts === 'string' ? body.last_audit_ts : null,
      lastSkillTs: typeof body.last_skill_ts === 'string' ? body.last_skill_ts : null,
      processUptimeSeconds:
        typeof body.process_uptime_seconds === 'number' ? body.process_uptime_seconds : null,
      version: typeof body.version === 'string' ? body.version : null,
    },
    heartbeatStatus,
    stickyStopLevel: stickyStop.level,
    stickyStopReason: stickyStop.reason,
    stickyStopCondition: stickyStop.condition,
    ...obs,
  })

  // ss#2500. Appended, not upserted: this is the off-Machine pin history, and a
  // pin a later beat could overwrite would not be a pin. Deliberately AFTER the
  // fleet_status upsert so a failure here cannot cost the health projection --
  // the Machine retries the whole beat, and the append is idempotent for an
  // unchanged head. The values come from `parseObservability` above, NOT from
  // a second parse of the body: one intake, two destinations.
  await recordAuditHead(env.DB, {
    entityId: auth.entityId,
    slug: auth.slug,
    heartbeatTs: body.heartbeat_ts,
    auditHead: obs.auditHead,
    auditRows: obs.auditRows,
  })

  return jsonResponse(200, { ok: true, heartbeat_status: heartbeatStatus })
}
