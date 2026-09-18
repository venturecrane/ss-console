/**
 * Fleet alert-feed view-model for the admin Operator console
 * (`/admin/operator/alerts`) — design doc §4.2.
 *
 * The single feed of everything that needs SMD attention across every operator.
 * It reads the shared `cost_anomaly_alerts` store (ADR 0023 §"Cross-cutting
 * calls" #9) via the frozen `listOpenAlerts` reader — that table is already
 * multi-source (cost / sentry / healthchecks / audit_integrity), and the
 * existing snooze/acknowledge API keys on (entity_id, alert_date, driver), so it
 * generalizes to every source row for free.
 *
 * This module owns only the pure view derivations: severity (the table has no
 * severity column, so it is derived per source), the deep-link target per
 * source, the detail line, filtering, and counts. The reader and the
 * snooze/ack writers are the frozen seam.
 *
 * Honest-scope note (foundations §7): the feed renders the alert sources that
 * flow into the store today. Sticky-stop transitions, connector auth-expired,
 * boot-check failures, and structural-config-deferred (design §4.2) light up
 * here as their receiver/writer paths land — the page says so rather than
 * fabricating rows for sources not yet wired.
 */

import type { CostAnomalyAlertRow } from './cost-anomaly'
import { costAnomalyDetail, relativeTimestamp } from './fleet-status'

export type AlertSeverity = 'critical' | 'warning' | 'info'

const SEVERITY_RANK: Record<AlertSeverity, number> = { critical: 2, warning: 1, info: 0 }

/**
 * Derive a severity from the alert source (and, for cost, whether the breach
 * is over its configured threshold). Audit-integrity drift is always critical
 * — a D1-vs-mirror mismatch is a compliance signal. Cost is critical only when
 * the day actually breached its threshold; otherwise it is a warning-grade
 * watch. Operational sources (sentry, healthchecks) are warnings.
 */
export function alertSeverity(row: CostAnomalyAlertRow): AlertSeverity {
  switch (row.source) {
    case 'audit_integrity':
      return 'critical'
    case 'cost':
      return row.ratio_bps >= row.threshold_bps ? 'critical' : 'warning'
    case 'sentry':
    case 'healthchecks':
      return 'warning'
    case 'obligation':
      // The reconciler already decided this — an obligation a week past due is
      // critical, a fresh one is a warning, an unwitnessed certification is
      // always critical. Re-deriving it here from the columns would guess at
      // what the writer already knew; reading a malformed payload falls back to
      // warning rather than inventing a severity.
      return obligationSeverity(row.details_json)
    default:
      return 'info'
  }
}

function obligationSeverity(detailsJson: string | null): AlertSeverity {
  if (!detailsJson) return 'warning'
  try {
    const parsed: unknown = JSON.parse(detailsJson)
    const severity =
      typeof parsed === 'object' && parsed !== null
        ? (parsed as { severity?: unknown }).severity
        : undefined
    return severity === 'critical' || severity === 'warning' || severity === 'info'
      ? severity
      : 'warning'
  } catch {
    return 'warning'
  }
}

/**
 * The per-operator surface this alert deep-links to. Cost anomalies open the
 * cost drill-in; everything else opens the operator overview (the hub from
 * which the relevant domain drill-in is reachable). Always one customer.
 */
export function alertLink(row: CostAnomalyAlertRow): string {
  const slug = encodeURIComponent(row.customer_slug)
  if (row.source === 'cost') return `/admin/operator/costs/${slug}`
  return `/admin/operator/${slug}`
}

/**
 * The human detail line. Cost rows render the numeric breach via the shared
 * costAnomalyDetail; every other source carries its own authored `summary`.
 * Never fabricates a detail for a row that has none.
 */
export function alertDetail(
  row: CostAnomalyAlertRow,
  formatCurrency: (cents: number) => string
): string {
  if (row.source === 'cost') {
    return costAnomalyDetail({
      driver: row.driver,
      daily_cents: row.daily_cents,
      rolling_avg_cents: row.rolling_avg_cents,
      ratio_bps: row.ratio_bps,
      formatCurrency,
    })
  }
  return row.summary ?? '(no detail recorded)'
}

export function alertAge(row: CostAnomalyAlertRow, now: Date = new Date()): string {
  return relativeTimestamp(row.detected_at, now)
}

export interface SeverityBadge {
  label: string
  classes: string
}

const SEVERITY_BADGE_STRUCTURE =
  'inline-flex items-center px-2 py-0.5 rounded-[var(--ss-radius-badge)] ' +
  'text-[10px] font-medium uppercase tracking-wide whitespace-nowrap'

export function severityBadge(severity: AlertSeverity): SeverityBadge {
  switch (severity) {
    case 'critical':
      return {
        label: 'Critical',
        classes: `${SEVERITY_BADGE_STRUCTURE} bg-[color:var(--ss-color-error)] text-white`,
      }
    case 'warning':
      return {
        label: 'Warning',
        classes: `${SEVERITY_BADGE_STRUCTURE} bg-[color:var(--ss-color-attention)] text-white`,
      }
    case 'info':
      return {
        label: 'Info',
        classes: `${SEVERITY_BADGE_STRUCTURE} bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]`,
      }
  }
}

export interface AlertFilters {
  source?: string | null
  severity?: string | null
  customer?: string | null
}

/**
 * Apply the (optional) source / severity / customer filters. Severity is
 * derived per row, so the severity filter computes it; an empty / null filter
 * value matches everything. Pure — own unit test.
 */
export function filterAlerts(
  rows: readonly CostAnomalyAlertRow[],
  filters: AlertFilters
): CostAnomalyAlertRow[] {
  const source = normalize(filters.source)
  const severity = normalize(filters.severity)
  const customer = normalize(filters.customer)
  return rows.filter((row) => {
    if (source && row.source !== source) return false
    if (customer && row.customer_slug !== customer) return false
    if (severity && alertSeverity(row) !== severity) return false
    return true
  })
}

function normalize(v: string | null | undefined): string | null {
  if (v === null || v === undefined) return null
  const trimmed = v.trim()
  return trimmed === '' || trimmed === 'all' ? null : trimmed
}

export interface SeverityCounts {
  critical: number
  warning: number
  info: number
  total: number
}

/** Count rows by derived severity — drives the header summary. */
export function countBySeverity(rows: readonly CostAnomalyAlertRow[]): SeverityCounts {
  const counts: SeverityCounts = { critical: 0, warning: 0, info: 0, total: rows.length }
  for (const row of rows) counts[alertSeverity(row)] += 1
  return counts
}

/**
 * Sort for display: most severe first, then freshest (detected_at desc). The
 * eye lands on the worst, newest thing — same discipline as the roster sort.
 */
export function sortAlerts(rows: readonly CostAnomalyAlertRow[]): CostAnomalyAlertRow[] {
  return [...rows].sort((a, b) => {
    const sev = SEVERITY_RANK[alertSeverity(b)] - SEVERITY_RANK[alertSeverity(a)]
    if (sev !== 0) return sev
    return b.detected_at.localeCompare(a.detected_at)
  })
}

/** Distinct customer slugs present in the feed, for the customer filter. */
export function distinctCustomers(rows: readonly CostAnomalyAlertRow[]): string[] {
  return [...new Set(rows.map((r) => r.customer_slug))].sort()
}
