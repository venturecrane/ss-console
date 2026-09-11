/**
 * The `fleet_status` projection: the row shape the alerter evaluates, and the
 * single read that produces it.
 *
 * Split out of index.ts when that file hit the 500-line ceiling again (the
 * same remedy, and the same reason, as the condition labels moving to
 * conditions.ts in ss#2488 part 2). index.ts orchestrates; this file owns what
 * a seat's health row IS and how it is fetched.
 *
 * Every column named in the SELECT must exist before this Worker runs, because
 * SQLite fails the whole statement on an unknown column and a failed read is
 * silence from the pager. Migrations therefore apply BEFORE the Worker
 * deploys, never after.
 */

export interface FleetStatusRow {
  customer_slug: string
  last_heartbeat_ts: string | null
  sticky_stop_level: string | null
  /**
   * The cause behind the level (migration 0112, overlay#341). Four meters
   * drive the sticky-stop ladder and each needs a different investigation, so
   * the level alone cannot say what happened. Null on a seat still running a
   * pre-cause overlay, which is a legitimate state, not a fault.
   */
  sticky_stop_reason: string | null
  sticky_stop_condition: string | null
  scheduler_ok: number | null
  scheduler_max_overdue_seconds: number | null
  connectors_json: string | null
  connector_check_ok: number | null
  connector_token_age_json: string | null
  spec_control_json: string | null
  spec_control_ok: number | null
  webhook_surface_json: string | null
  webhook_surface_ok: number | null
  gateway_loop_ok: number | null
  gateway_loop_age_seconds: number | null
  gateway_supervisor_state: string | null
  gateway_restarts_last_hour: number | null
  /**
   * ss#2547. Optional on the TYPE, not merely nullable: before migration 0109
   * is applied these columns do not exist, and the SELECT returns rows without
   * the property at all. `undefined` and `null` must both hold, so the pager
   * checks the value rather than trusting the column to be there.
   */
  send_refusals?: number | null
  send_refusals_last_ts?: string | null
  send_refusals_json?: string | null
}

export async function listFleetStatus(db: D1Database): Promise<FleetStatusRow[]> {
  const result = await db
    .prepare(
      `SELECT customer_slug, last_heartbeat_ts, sticky_stop_level,
              sticky_stop_reason, sticky_stop_condition,
              scheduler_ok, scheduler_max_overdue_seconds,
              connectors_json, connector_check_ok, connector_token_age_json,
              spec_control_json, spec_control_ok,
              webhook_surface_json, webhook_surface_ok,
              gateway_loop_ok, gateway_loop_age_seconds,
              gateway_supervisor_state, gateway_restarts_last_hour,
              send_refusals, send_refusals_last_ts, send_refusals_json
         FROM fleet_status`
    )
    .all<FleetStatusRow>()
  return result.results ?? []
}
