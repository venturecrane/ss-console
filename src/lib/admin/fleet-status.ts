/**
 * Fleet-status reader for the admin dashboard (ADR 0023 Wave 1).
 *
 * The `/admin/operator/costs/` page already enumerates Operator
 * customers via `listCostCustomers()`. This module returns the
 * per-customer heartbeat snapshot (heartbeat freshness, Sentry-24h
 * error count, uptime, version) so the page can render three new
 * columns alongside the existing cost columns.
 *
 * The page computes heartbeat staleness server-side at render — see
 * `heartbeatDisplay()` below — so the table column NEVER lies in the
 * reassuring direction (the row's `last_heartbeat_ts` could be hours
 * old; the color reflects that age, not the freshness of the last
 * write). Per ADR 0023 implementation-plan §"Design Decisions" #3.
 */

import type { D1Database } from '@cloudflare/workers-types'

/**
 * Seat is "work overdue" (yellow) when its scheduled work is more than this
 * many seconds past due. Single source of the literal 900 default: the
 * ss-fleet-alerts Worker's `work_overdue` condition documents that its
 * `WORK_OVERDUE_RED_SECONDS` env var must match this default (the worker is a
 * separate wrangler package, so a runtime import across the package boundary
 * is avoided by contract, not by a shared module — see the PR body).
 */
export const WORK_OVERDUE_RED_SECONDS = 900

/**
 * A connector's consecutive-failure run must be at least this old (writer-side
 * age) before it renders red / pages. Same documented-contract shape as
 * WORK_OVERDUE_RED_SECONDS: the ss-fleet-alerts Worker's
 * `CONNECTOR_DOWN_RUN_AGE_SECONDS` env var must match this default.
 */
export const CONNECTOR_DOWN_RUN_AGE_SECONDS = 300
/** Conn-class open path: consecutive failures at/above this (Hermes breaker parity). */
export const CONNECTOR_DOWN_MIN_FAILURES = 3
/** Signature-free backstop: any run at/above this count and 900s pages regardless. */
export const CONNECTOR_BACKSTOP_MIN_FAILURES = 10
export const CONNECTOR_BACKSTOP_RUN_AGE_SECONDS = 900

export interface FleetStatusRow {
  entity_id: string
  customer_slug: string
  last_heartbeat_ts: string | null
  last_audit_ts: string | null
  last_skill_ts: string | null
  process_uptime_seconds: number | null
  version: string | null
  heartbeat_status: 'green' | 'yellow' | 'red' | 'unknown'
  /** Sticky-stop ladder level from the Machine (ADR 0062); NULL = not reported. */
  sticky_stop_level: string | null
  /**
   * WHY the ladder tripped (migration 0112, overlay#341). Four meters drive it
   * and each needs a different response, so the Captain deciding whether to
   * clear a stop needs the cause, not just the level. NULL on a seat still
   * running a pre-cause overlay, which is a legitimate state, not a fault.
   */
  sticky_stop_reason: string | null
  sticky_stop_condition: string | null
  /** Scheduler self-check verdict (WP-2): 1 healthy / 0 broken / NULL unreported. */
  scheduler_ok: number | null
  /** Enabled scheduled-job count the gate could read this beat; NULL unreported. */
  scheduler_job_count: number | null
  /** Max seconds any enabled job is past its next_run_at; NULL unreported. */
  scheduler_max_overdue_seconds: number | null
  /** Per-MCP-server health map JSON (ADR 0080); NULL = not reported this beat. */
  connectors_json: string | null
  /** Connector self-check verdict: 1 healthy / 0 broken / NULL unreported. */
  connector_check_ok: number | null
  /** ss#2276: 1 = crons deliberately contained (volume sentinel), 0 normal, NULL unreported. */
  cron_containment: number | null
  /**
   * #2498: audit rows this seat has failed to persist, cumulative and monotonic
   * across reboots. 0 is a REAL value — the writer is up and has lost nothing —
   * and NULL means the seat cannot answer. Rendered beside `last_audit_ts`
   * because the two only mean anything together: a stale `last_audit_ts` with 0
   * failures is a quiet seat, the same timestamp with a non-zero count is a
   * broken one, and before this column they looked identical.
   */
  audit_write_failures: number | null
  /** ss#2488 part 2: 1 = the seat's loop check could look / 0 could not / NULL unreported. */
  gateway_loop_ok: number | null
  /** Seconds since the gateway event loop last beat; NULL = hold (latch, no heartbeat, boot). */
  gateway_loop_age_seconds: number | null
  /** Part-1 supervisor state: armed | not-armed | inert | not-watching | refusing; NULL unreported. */
  gateway_supervisor_state: string | null
  /** Supervisor kill-ledger lines inside the last hour; NULL unreported. */
  gateway_restarts_last_hour: number | null
  /**
   * ss#2547: outbound sends this seat's own gates refused, plus wakes that
   * carried needs-you items and attempted nothing, over the trailing 24h. 0 is
   * a REAL value and the load-bearing one: it is what separates a seat whose
   * escalations are landing from a seat that has gone quiet because it cannot
   * get past itself. NULL means the seat cannot answer.
   */
  send_refusals: number | null
  /** Newest refusal-or-unsent event, canonical UTC; NULL = nothing to show. */
  send_refusals_last_ts: string | null
  /** The newest few events verbatim (ts, routine, tool, kind, reason); NULL = no detail. */
  send_refusals_json: string | null
  sentry_errors_last_24h: number | null
  sentry_errors_synced_at: string | null
  updated_at: string
}

export async function listFleetStatus(db: D1Database): Promise<FleetStatusRow[]> {
  const result = await db
    .prepare(
      `SELECT entity_id, customer_slug, last_heartbeat_ts, last_audit_ts, last_skill_ts,
              process_uptime_seconds, version, heartbeat_status, sticky_stop_level,
              sticky_stop_reason, sticky_stop_condition,
              scheduler_ok, scheduler_job_count, scheduler_max_overdue_seconds,
              connectors_json, connector_check_ok, cron_containment,
              audit_write_failures,
              gateway_loop_ok, gateway_loop_age_seconds,
              gateway_supervisor_state, gateway_restarts_last_hour,
              send_refusals, send_refusals_last_ts, send_refusals_json,
              sentry_errors_last_24h, sentry_errors_synced_at, updated_at
         FROM fleet_status
        ORDER BY customer_slug ASC`
    )
    .all<FleetStatusRow>()
  return result.results ?? []
}

export interface HeartbeatDisplay {
  color: 'green' | 'yellow' | 'red' | 'gray'
  label: string
}

/**
 * Compute the heartbeat column display from `last_heartbeat_ts` and the
 * customer's configured period/grace. Defaults match
 * customer.yaml.observability.health (period_seconds=60, grace_minutes=5).
 *
 * Color rules:
 *   - gray  : no heartbeat ever (row missing or last_heartbeat_ts null)
 *   - green : age < 2 × period_seconds (fresh)
 *   - yellow: age < grace_minutes × 60 (late but inside grace)
 *   - red   : age >= grace_minutes × 60 (grace expired)
 *
 * The label is always relative ("47s ago", "3m ago", "stale 47m") so the
 * admin can see the actual age regardless of the color band.
 */
export function heartbeatDisplay(
  lastHeartbeatTs: string | null,
  periodSeconds = 60,
  graceMinutes = 5,
  now: Date = new Date()
): HeartbeatDisplay {
  if (!lastHeartbeatTs) return { color: 'gray', label: 'no signal yet' }
  const ts = Date.parse(lastHeartbeatTs)
  if (Number.isNaN(ts)) return { color: 'gray', label: 'invalid timestamp' }

  const ageSec = Math.max(0, Math.floor((now.getTime() - ts) / 1000))
  const fresh = ageSec < 2 * periodSeconds
  const inGrace = ageSec < graceMinutes * 60

  if (fresh) return { color: 'green', label: `${formatAge(ageSec)} ago` }
  if (inGrace) return { color: 'yellow', label: `${formatAge(ageSec)} ago` }
  return { color: 'red', label: `stale ${formatAge(ageSec)}` }
}

export function formatAge(sec: number): string {
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`
  return `${Math.floor(sec / 86400)}d`
}

export function formatUptime(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return '—'
  return formatAge(Math.floor(seconds))
}

/**
 * Tailwind class binding for the heartbeat-column color band. Kept out
 * of the .astro page so the page stays under the file-length ceiling
 * and so the binding is testable.
 */
export function heartbeatColorClass(color: 'green' | 'yellow' | 'red' | 'gray'): string {
  switch (color) {
    case 'green':
      return 'text-[color:var(--ss-color-success)]'
    case 'yellow':
      return 'text-[color:var(--ss-color-attention)]'
    case 'red':
      return 'text-[color:var(--ss-color-error)]'
    case 'gray':
      return 'text-[color:var(--ss-color-text-muted)]'
  }
}

export interface AuditWriteFailureDisplay {
  label: string
  colorClass: string
}

/**
 * Render the audit-write-failure counter for the lifecycle page (#2498).
 *
 * Three states, and keeping them apart IS the feature. Before this column, a
 * seat that had sent nothing for days and a seat whose audit writer had been
 * failing silently produced the same page, because every audit hook on the
 * Machine swallows a write failure by design and `last_audit_ts` alone cannot
 * tell a gap from a quiet week.
 *
 *   - null → "not reported". The seat has no opinion (the audit plugin has
 *     never registered, so the volume tally has no home). NEVER rendered as
 *     "none" — a reassuring answer we did not receive is the failure this
 *     whole issue is about.
 *   - 0    → "none". A real, load-bearing zero: the writer is up and has lost
 *     nothing, so a stale `last_audit_ts` next to it means a quiet seat.
 *   - n>0  → "N lost", in the error color. The ledger has gaps and the beside
 *     -it timestamp cannot be trusted as "nothing happened".
 */
export function auditWriteFailureDisplay(count: number | null): AuditWriteFailureDisplay {
  if (count === null || !Number.isFinite(count) || count < 0) {
    return { label: 'not reported', colorClass: 'text-[color:var(--ss-color-text-muted)]' }
  }
  if (count === 0) {
    return { label: 'none', colorClass: 'text-[color:var(--ss-color-text-primary)]' }
  }
  return {
    label: `${count} lost`,
    colorClass: 'text-[color:var(--ss-color-error)]',
  }
}

/**
 * Render an absolute ISO timestamp as "Xs/m/h/d ago" (or "—" on null /
 * malformed). Used for the Sentry-sync freshness tooltip on the costs
 * page.
 */
export function relativeTimestamp(iso: string | null, now: Date = new Date()): string {
  if (!iso) return '—'
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return '—'
  const ageSec = Math.max(0, Math.floor((now.getTime() - ts) / 1000))
  return `${formatAge(ageSec)} ago`
}

/**
 * Alerts-banner pill metadata for a `cost_anomaly_alerts.source` value.
 * Centralized here (not inline in the .astro page) so the source-tag
 * vocabulary stays in one place — adding a new source means touching
 * one switch.
 */
export interface SourcePill {
  label: string
  classes: string
}

export function sourcePill(source: string): SourcePill {
  switch (source) {
    case 'sentry':
      return { label: 'Sentry', classes: 'bg-purple-100 text-purple-800' }
    case 'healthchecks':
      return { label: 'Heartbeat', classes: 'bg-red-100 text-red-800' }
    case 'audit_integrity':
      return { label: 'Audit', classes: 'bg-amber-100 text-amber-800' }
    case 'cost':
    default:
      return { label: 'Cost', classes: 'bg-yellow-100 text-yellow-800' }
  }
}

/**
 * Format the COGS-anomaly detail line on the alerts banner. Pure
 * function so the .astro page stays under complexity-15 — the row map
 * uses this without nesting more ternaries inline.
 */
export function costAnomalyDetail(args: {
  driver: string
  daily_cents: number
  rolling_avg_cents: number
  ratio_bps: number
  formatCurrency: (cents: number) => string
}): string {
  const driverLabel = args.driver === '' ? 'all drivers (aggregate)' : args.driver
  const ratio = (args.ratio_bps / 100).toFixed(0)
  return `Top driver: ${driverLabel} · ${args.formatCurrency(args.daily_cents)} vs ${args.formatCurrency(args.rolling_avg_cents)} 7-day avg · ${ratio}%`
}
