/**
 * Per-customer cost anomaly detection — pure-function core (#886).
 *
 * Given a sequence of daily costs for one customer (or one driver), decide
 * whether the most recent day breaches the configured threshold against
 * the 7-day rolling average. The default threshold is 150% (15000 bps),
 * matching the PRD requirement quoted in #886.
 *
 * Shared by:
 *   - ss-cost-anomaly Worker (workers/cost-anomaly/) — runs nightly,
 *     fetches per-customer cost_telemetry via the same D1 HTTP API the
 *     cost dashboard uses, applies `detectAnomaly`, and writes alerts
 *     to central D1's `cost_anomaly_alerts` table.
 *   - Captain dashboard banner — reads open alerts via `listOpenAlerts`
 *     and renders the snooze/ack flow via the API endpoints.
 *
 * Fabrication discipline (CLAUDE.md Pattern A/B): the detection works
 * against real cost_telemetry rows. When fewer than 7 prior days of data
 * exist the function returns `{ kind: 'insufficient-history' }` rather
 * than alerting on a too-small sample — this is the same posture
 * cost-query.ts takes for the rolling7dCents series (null entries for
 * the first six days of any window).
 */

import type { CostTelemetryRow, DriverCategory } from './cost-query'
import { categoryForDriver } from './cost-query'

/** Default threshold in basis points — 150% per PRD §15.x / #886. */
export const DEFAULT_THRESHOLD_BPS = 15000

/**
 * The sentinel `driver` value used in `cost_anomaly_alerts` to represent
 * the all-drivers aggregate breach. Stored as empty string rather than
 * NULL so the natural-key PRIMARY KEY dedupes correctly (SQLite treats
 * NULLs in composite PKs as distinct, which would let the worker insert
 * duplicates on re-run).
 */
export const AGGREGATE_DRIVER_SENTINEL = ''

export interface DailyCostPoint {
  date: string
  total_cents: number
}

export type AnomalyDetection =
  | {
      kind: 'no-anomaly'
      date: string
      daily_cents: number
      rolling_avg_cents: number
      ratio_bps: number
    }
  | {
      kind: 'anomaly'
      date: string
      daily_cents: number
      rolling_avg_cents: number
      ratio_bps: number
      threshold_bps: number
    }
  | {
      kind: 'insufficient-history'
      reason: string
    }

/**
 * Inspect the last day in a dense daily-cost series and decide whether it
 * breaches `thresholdBps` of the prior 7-day rolling average.
 *
 * The caller is responsible for zero-filling missing days (mirrors
 * `summarizeCostRows.byDay` in cost-query.ts). A series shorter than 8
 * points (1 candidate + 7 history) yields `insufficient-history`.
 *
 * Rolling average uses the 7 days *prior* to the candidate, not including
 * it — including the candidate would dampen the comparison the alert is
 * trying to surface.
 */
export function detectAnomaly(
  series: readonly DailyCostPoint[],
  thresholdBps: number = DEFAULT_THRESHOLD_BPS
): AnomalyDetection {
  if (series.length < 8) {
    return {
      kind: 'insufficient-history',
      reason: `series has ${series.length} day(s); need at least 8 (7 prior + 1 candidate)`,
    }
  }
  const candidate = series[series.length - 1]
  const priorWindow = series.slice(series.length - 8, series.length - 1)
  if (priorWindow.length !== 7) {
    return {
      kind: 'insufficient-history',
      reason: `prior window has ${priorWindow.length} day(s); expected 7`,
    }
  }
  const sumPrior = priorWindow.reduce((s, p) => s + p.total_cents, 0)
  const rollingAvg = Math.round(sumPrior / 7)

  if (rollingAvg <= 0) {
    // No prior cost to compare against — a non-zero candidate is technically
    // "infinite-ratio" but alerting on it would fire on every new customer's
    // first cost day. Defer to the next run when there is real prior data.
    return {
      kind: 'insufficient-history',
      reason: 'prior 7-day average is zero; need a non-zero baseline to detect a spike',
    }
  }

  // Integer basis-points: 10000 = 1x, 15000 = 1.5x. Use BigInt-style mul
  // before divide to avoid float drift on very large totals.
  const ratioBps = Math.round((candidate.total_cents * 10_000) / rollingAvg)
  if (ratioBps < thresholdBps) {
    return {
      kind: 'no-anomaly',
      date: candidate.date,
      daily_cents: candidate.total_cents,
      rolling_avg_cents: rollingAvg,
      ratio_bps: ratioBps,
    }
  }
  return {
    kind: 'anomaly',
    date: candidate.date,
    daily_cents: candidate.total_cents,
    rolling_avg_cents: rollingAvg,
    ratio_bps: ratioBps,
    threshold_bps: thresholdBps,
  }
}

/**
 * Collapse raw cost_telemetry rows into one dense daily series (sum across
 * all drivers) for the aggregate-level anomaly check. Zero-fills missing
 * days against the supplied `dates` axis.
 */
export function buildDailySeries(
  rows: readonly CostTelemetryRow[],
  dates: readonly string[]
): DailyCostPoint[] {
  const sumByDate = new Map<string, number>()
  for (const r of rows) {
    if (r.amount_cents < 0) continue
    sumByDate.set(r.date, (sumByDate.get(r.date) ?? 0) + r.amount_cents)
  }
  return dates.map((d) => ({ date: d, total_cents: sumByDate.get(d) ?? 0 }))
}

/**
 * Build a per-driver dense daily series. Used to identify the "top-1
 * driver by delta" attribution included in the alert.
 *
 * Drivers absent from a given day land at zero. Drivers absent across the
 * entire window are omitted from the result (no point computing zero series).
 */
export function buildPerDriverSeries(
  rows: readonly CostTelemetryRow[],
  dates: readonly string[]
): Map<string, DailyCostPoint[]> {
  const driverByDate = new Map<string, Map<string, number>>()
  for (const r of rows) {
    if (r.amount_cents < 0) continue
    let inner = driverByDate.get(r.driver)
    if (!inner) {
      inner = new Map<string, number>()
      driverByDate.set(r.driver, inner)
    }
    inner.set(r.date, (inner.get(r.date) ?? 0) + r.amount_cents)
  }
  const out = new Map<string, DailyCostPoint[]>()
  for (const [driver, inner] of driverByDate) {
    out.set(
      driver,
      dates.map((d) => ({ date: d, total_cents: inner.get(d) ?? 0 }))
    )
  }
  return out
}

export interface TopDriverAttribution {
  driver: string
  category: DriverCategory
  daily_cents: number
  rolling_avg_cents: number
  delta_cents: number
}

/**
 * Pick the single driver that contributed the largest *absolute* delta
 * between the candidate day and its 7-day rolling average. The intent
 * matches the PRD ask ("which skill drove the spike") given current
 * schema: surface the driver whose increase explains the breach.
 *
 * Returns null when no driver shows a positive delta — implies the
 * aggregate breach came from a broad uptick rather than one driver.
 */
export function pickTopDriverByDelta(
  rows: readonly CostTelemetryRow[],
  dates: readonly string[]
): TopDriverAttribution | null {
  if (dates.length < 8) return null
  const perDriver = buildPerDriverSeries(rows, dates)
  let winner: TopDriverAttribution | null = null
  for (const [driver, series] of perDriver) {
    const candidate = series[series.length - 1]
    const prior = series.slice(series.length - 8, series.length - 1)
    const priorAvg = Math.round(prior.reduce((s, p) => s + p.total_cents, 0) / 7)
    const delta = candidate.total_cents - priorAvg
    if (delta <= 0) continue
    if (!winner || delta > winner.delta_cents) {
      winner = {
        driver,
        category: categoryForDriver(driver),
        daily_cents: candidate.total_cents,
        rolling_avg_cents: priorAvg,
        delta_cents: delta,
      }
    }
  }
  return winner
}

// ---------------------------------------------------------------------------
// Alert storage (central D1)
// ---------------------------------------------------------------------------

/**
 * Source of an alert row in `cost_anomaly_alerts`. The table is shared
 * across observability sources per ADR 0023 §"Cross-cutting calls" #9 —
 * the admin dashboard banner renders rows from every source with the
 * existing snooze/acknowledge UI for free.
 *
 * - `cost` rows are written by the cost-anomaly Worker; they populate
 *   `daily_cents`, `rolling_avg_cents`, `ratio_bps`, `threshold_bps` and
 *   leave `summary` / `details_json` null.
 * - `sentry`, `healthchecks`, `audit_integrity` rows are written by the
 *   matching webhook receivers; they populate `summary` (and
 *   `details_json` for drill-down) and carry 0 sentinels for the
 *   cost-specific columns. The dashboard reader switches on `source`.
 */
export type AlertSource =
  | 'cost'
  | 'sentry'
  | 'healthchecks'
  | 'audit_integrity'
  // Written by scripts/ci-reconcile-obligations.ts: client work that is
  // overdue, certified by no CI run, or a source class that stopped producing
  // obligations (ADR 0088, migration 0118).
  | 'obligation'

export interface CostAnomalyAlertRow {
  entity_id: string
  customer_slug: string
  alert_date: string
  driver: string
  source: AlertSource
  daily_cents: number
  rolling_avg_cents: number
  ratio_bps: number
  threshold_bps: number
  summary: string | null
  details_json: string | null
  detected_at: string
  snoozed_until: string | null
  acknowledged_at: string | null
  acknowledged_by: string | null
}

/**
 * Insert or refresh a cost-anomaly alert row. The PK is
 * (entity_id, alert_date, driver) so a re-run for the same day collapses
 * to one row. Existing snooze / acknowledged columns are preserved on
 * conflict — the worker never undoes Captain's action.
 *
 * This writer is COST-SPECIFIC. Non-cost sources (sentry, healthchecks,
 * audit_integrity) write their rows directly from their webhook
 * handlers with `source` set explicitly; they use the same `cost_anomaly_alerts`
 * table per ADR 0023 §"Cross-cutting calls" #9 but a different write
 * path because the column shape they care about (summary, details_json)
 * is different from cost's (daily_cents, rolling_avg_cents, ratio_bps).
 *
 * For cost rows the table's `source` column defaults to 'cost' (per
 * migration 0044), so this writer does not need to specify it.
 */
export interface CostAlertInput {
  entity_id: string
  customer_slug: string
  alert_date: string
  driver: string
  daily_cents: number
  rolling_avg_cents: number
  ratio_bps: number
  threshold_bps: number
}

export async function upsertAlert(db: D1Database, alert: CostAlertInput): Promise<void> {
  await db
    .prepare(
      `INSERT INTO cost_anomaly_alerts
         (entity_id, customer_slug, alert_date, driver,
          daily_cents, rolling_avg_cents, ratio_bps, threshold_bps)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (entity_id, alert_date, driver) DO UPDATE SET
         daily_cents = excluded.daily_cents,
         rolling_avg_cents = excluded.rolling_avg_cents,
         ratio_bps = excluded.ratio_bps,
         threshold_bps = excluded.threshold_bps,
         customer_slug = excluded.customer_slug`
    )
    .bind(
      alert.entity_id,
      alert.customer_slug,
      alert.alert_date,
      alert.driver,
      alert.daily_cents,
      alert.rolling_avg_cents,
      alert.ratio_bps,
      alert.threshold_bps
    )
    .run()
}

/**
 * List alerts that should currently be visible on the Captain dashboard:
 * not acknowledged, and (no snooze OR snooze elapsed). Ordered by
 * detected_at descending so the freshest land at the top.
 */
export async function listOpenAlerts(
  db: D1Database,
  now: Date = new Date()
): Promise<CostAnomalyAlertRow[]> {
  const nowIso = now.toISOString()
  const result = await db
    .prepare(
      `SELECT entity_id, customer_slug, alert_date, driver, source,
              daily_cents, rolling_avg_cents, ratio_bps, threshold_bps,
              summary, details_json,
              detected_at, snoozed_until, acknowledged_at, acknowledged_by
         FROM cost_anomaly_alerts
        WHERE acknowledged_at IS NULL
          AND (snoozed_until IS NULL OR snoozed_until <= ?)
        ORDER BY detected_at DESC`
    )
    .bind(nowIso)
    .all<CostAnomalyAlertRow>()
  return result.results ?? []
}

/**
 * Apply a snooze to one alert. `snoozedUntil` is an ISO 8601 UTC string —
 * the dashboard hides the alert until that point. Setting null clears
 * the snooze.
 */
export async function snoozeAlert(
  db: D1Database,
  identity: { entity_id: string; alert_date: string; driver: string },
  snoozedUntil: string | null
): Promise<void> {
  await db
    .prepare(
      `UPDATE cost_anomaly_alerts
          SET snoozed_until = ?
        WHERE entity_id = ? AND alert_date = ? AND driver = ?`
    )
    .bind(snoozedUntil, identity.entity_id, identity.alert_date, identity.driver)
    .run()
}

/**
 * Acknowledge an alert. Acknowledged alerts disappear from the dashboard
 * but remain in the table for audit. Re-detection on a later day produces
 * a fresh row with its own ack state.
 */
export async function acknowledgeAlert(
  db: D1Database,
  identity: { entity_id: string; alert_date: string; driver: string },
  ackedBy: string,
  now: Date = new Date()
): Promise<void> {
  await db
    .prepare(
      `UPDATE cost_anomaly_alerts
          SET acknowledged_at = ?, acknowledged_by = ?
        WHERE entity_id = ? AND alert_date = ? AND driver = ?`
    )
    .bind(now.toISOString(), ackedBy, identity.entity_id, identity.alert_date, identity.driver)
    .run()
}
