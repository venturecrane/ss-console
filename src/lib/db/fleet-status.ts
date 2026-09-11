/**
 * The `fleet_status` row: the console's projection of each seat's last beat.
 *
 * Written by `POST /api/internal/heartbeat` and read by the admin fleet view
 * and the ss-fleet-alerts pager. The upsert and the freshness derivation
 * lived inside the route until 2026-09-10 (code review 2026-09-10,
 * Architecture 3); the route now parses and delegates here.
 *
 * This module knows nothing about the heartbeat wire body: the route hands
 * it already-parsed values, so a field can never reach the row unparsed.
 */

import type { D1Database } from '@cloudflare/workers-types'

export const DEFAULT_PERIOD_SECONDS = 60
export const DEFAULT_GRACE_MINUTES = 5

/** The five liveness fields, already parsed by the route. */
export interface HeartbeatCore {
  heartbeatTs: string
  lastAuditTs: string | null
  lastSkillTs: string | null
  processUptimeSeconds: number | null
  version: string | null
}

export interface FleetStatusUpsert {
  entityId: string
  slug: string
  beat: HeartbeatCore
  heartbeatStatus: string
  stickyStopLevel: string | null
  stickyStopReason: string | null
  stickyStopCondition: string | null
  schedulerOk: 0 | 1 | null
  schedulerJobCount: number | null
  schedulerMaxOverdueSeconds: number | null
  connectorsJson: string | null
  connectorCheckOk: 0 | 1 | null
  connectorTokenAgeJson: string | null
  specControlJson: string | null
  specControlOk: 0 | 1 | null
  webhookSurfaceJson: string | null
  webhookSurfaceOk: 0 | 1 | null
  cronContainment: 0 | 1 | null
  auditWriteFailures: number | null
  auditHead: string | null
  auditRows: number | null
  gatewayLoopOk: 0 | 1 | null
  gatewayLoopAgeSeconds: number | null
  gatewaySupervisorState: string | null
  gatewayRestartsLastHour: number | null
  sendRefusals: number | null
  sendRefusalsLastTs: string | null
  sendRefusalsJson: string | null
}

/**
 * The upsert, and the two update disciplines it deliberately mixes.
 *
 * `COALESCE` for the four fields where a beat that omits one has nothing to say
 * (timestamps, uptime, version). Plain overwrite — INCLUDING back to NULL — for
 * everything an alert reads, because a stale pinned verdict must never outlive
 * the signal that produced it.
 */
// Lifted out of the function body (ss#2547) rather than shortened: the
// statement is one declaration and the ESLint per-function line ceiling counts
// it as flow. Splitting the SQL itself would be worse than the ceiling it was
// tripping. Re-keyed on customer_slug (migration 0093): several seats share one
// entity, so ON CONFLICT(entity_id) would collide them into one row. entity_id
// is now a plain column and is refreshed from the request on every upsert.
const FLEET_STATUS_UPSERT_SQL = `INSERT INTO fleet_status (
       entity_id, customer_slug, last_heartbeat_ts, last_audit_ts, last_skill_ts,
       process_uptime_seconds, version, heartbeat_status, sticky_stop_level,
       sticky_stop_reason, sticky_stop_condition,
       scheduler_ok, scheduler_job_count, scheduler_max_overdue_seconds,
       connectors_json, connector_check_ok, connector_token_age_json,
       spec_control_json, spec_control_ok,
       webhook_surface_json, webhook_surface_ok, cron_containment,
       audit_write_failures, audit_head, audit_rows,
       gateway_loop_ok, gateway_loop_age_seconds, gateway_supervisor_state,
       gateway_restarts_last_hour,
       send_refusals, send_refusals_last_ts, send_refusals_json, updated_at
     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(customer_slug) DO UPDATE SET
       entity_id               = excluded.entity_id,
       last_heartbeat_ts       = excluded.last_heartbeat_ts,
       last_audit_ts           = COALESCE(excluded.last_audit_ts, fleet_status.last_audit_ts),
       last_skill_ts           = COALESCE(excluded.last_skill_ts, fleet_status.last_skill_ts),
       process_uptime_seconds  = COALESCE(excluded.process_uptime_seconds, fleet_status.process_uptime_seconds),
       version                 = COALESCE(excluded.version, fleet_status.version),
       heartbeat_status        = excluded.heartbeat_status,
       sticky_stop_level       = excluded.sticky_stop_level,
       sticky_stop_reason      = excluded.sticky_stop_reason,
       sticky_stop_condition   = excluded.sticky_stop_condition,
       scheduler_ok                  = excluded.scheduler_ok,
       scheduler_job_count           = excluded.scheduler_job_count,
       scheduler_max_overdue_seconds = excluded.scheduler_max_overdue_seconds,
       connectors_json         = excluded.connectors_json,
       connector_check_ok      = excluded.connector_check_ok,
       connector_token_age_json = excluded.connector_token_age_json,
       spec_control_json       = excluded.spec_control_json,
       spec_control_ok         = excluded.spec_control_ok,
       webhook_surface_json    = excluded.webhook_surface_json,
       webhook_surface_ok      = excluded.webhook_surface_ok,
       cron_containment        = excluded.cron_containment,
       audit_write_failures    = COALESCE(excluded.audit_write_failures, fleet_status.audit_write_failures),
       audit_head              = COALESCE(excluded.audit_head, fleet_status.audit_head),
       audit_rows              = COALESCE(excluded.audit_rows, fleet_status.audit_rows),
       gateway_loop_ok            = excluded.gateway_loop_ok,
       gateway_loop_age_seconds   = excluded.gateway_loop_age_seconds,
       gateway_supervisor_state   = excluded.gateway_supervisor_state,
       gateway_restarts_last_hour = excluded.gateway_restarts_last_hour,
       send_refusals           = COALESCE(excluded.send_refusals, fleet_status.send_refusals),
       send_refusals_last_ts   = COALESCE(excluded.send_refusals_last_ts, fleet_status.send_refusals_last_ts),
       send_refusals_json      = COALESCE(excluded.send_refusals_json, fleet_status.send_refusals_json),
       updated_at              = datetime('now')`

export async function upsertFleetStatus(db: D1Database, u: FleetStatusUpsert): Promise<void> {
  await db
    .prepare(FLEET_STATUS_UPSERT_SQL)
    .bind(
      u.entityId,
      u.slug,
      u.beat.heartbeatTs,
      u.beat.lastAuditTs,
      u.beat.lastSkillTs,
      u.beat.processUptimeSeconds,
      u.beat.version,
      u.heartbeatStatus,
      u.stickyStopLevel,
      u.stickyStopReason,
      u.stickyStopCondition,
      u.schedulerOk,
      u.schedulerJobCount,
      u.schedulerMaxOverdueSeconds,
      u.connectorsJson,
      u.connectorCheckOk,
      u.connectorTokenAgeJson,
      u.specControlJson,
      u.specControlOk,
      u.webhookSurfaceJson,
      u.webhookSurfaceOk,
      u.cronContainment,
      u.auditWriteFailures,
      u.auditHead,
      u.auditRows,
      u.gatewayLoopOk,
      u.gatewayLoopAgeSeconds,
      u.gatewaySupervisorState,
      u.gatewayRestartsLastHour,
      u.sendRefusals,
      u.sendRefusalsLastTs,
      u.sendRefusalsJson
    )
    .run()
}

/**
 * The prior cumulative audit-write-failure count for a seat, or null when the
 * seat has never reported one. Read BEFORE the upsert overwrites it (#2498).
 */
export async function readAuditWriteFailures(db: D1Database, slug: string): Promise<number | null> {
  const row = await db
    .prepare('SELECT audit_write_failures FROM fleet_status WHERE customer_slug = ?')
    .bind(slug)
    .first<{ audit_write_failures: number | null }>()
  return row?.audit_write_failures ?? null
}

export function deriveHeartbeatStatus(
  heartbeatIso: string,
  periodSec: number,
  graceMin: number
): 'green' | 'yellow' | 'red' | 'unknown' {
  const ts = Date.parse(heartbeatIso)
  if (Number.isNaN(ts)) return 'unknown'
  const ageSec = Math.floor((Date.now() - ts) / 1000)
  if (ageSec < 2 * periodSec) return 'green'
  if (ageSec < graceMin * 60) return 'yellow'
  return 'red'
}
