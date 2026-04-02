/**
 * Analytics query layer for business intelligence views.
 *
 * All queries are parameterized to prevent SQL injection.
 * All queries are scoped to org_id for multi-tenancy.
 * All functions handle empty data gracefully (no division by zero, sensible defaults).
 */

// ── Pipeline Conversion ─────────────────────────────────────────────
export interface PipelineConversion {
  prospect: number
  assessing: number
  proposing: number
  engaged: number
  delivered: number
  lost: number
}

/**
 * Count entities at each pipeline stage.
 */
export async function getPipelineConversion(
  db: D1Database,
  orgId: string
): Promise<PipelineConversion> {
  const result = await db
    .prepare(
      `SELECT stage, COUNT(*) as count
       FROM entities
       WHERE org_id = ?
       GROUP BY stage`
    )
    .bind(orgId)
    .all<{ stage: string; count: number }>()

  const counts: PipelineConversion = {
    prospect: 0,
    assessing: 0,
    proposing: 0,
    engaged: 0,
    delivered: 0,
    lost: 0,
  }

  for (const row of result.results) {
    if (row.stage in counts) {
      counts[row.stage as keyof PipelineConversion] = row.count
    }
  }

  return counts
}

// ── Quote Accuracy ──────────────────────────────────────────────────
export interface QuoteAccuracyRow {
  engagement_id: string
  client_name: string
  estimated_hours: number
  actual_hours: number
  accuracy_pct: number
}

/**
 * Compare estimated vs actual hours for completed engagements.
 * accuracy_pct = (actual_hours / estimated_hours) * 100
 * Only includes completed engagements with non-null estimated_hours.
 */
export async function getQuoteAccuracy(db: D1Database, orgId: string): Promise<QuoteAccuracyRow[]> {
  const result = await db
    .prepare(
      `SELECT
         e.id AS engagement_id,
         en.business_name AS client_name,
         e.estimated_hours,
         e.actual_hours
       FROM engagements e
       JOIN entities en ON en.id = e.entity_id AND en.org_id = ?
       WHERE e.org_id = ?
         AND e.status = 'completed'
         AND e.estimated_hours IS NOT NULL
         AND e.estimated_hours > 0
       ORDER BY e.actual_end DESC`
    )
    .bind(orgId, orgId)
    .all<{
      engagement_id: string
      client_name: string
      estimated_hours: number
      actual_hours: number
    }>()

  return result.results.map((row) => ({
    engagement_id: row.engagement_id,
    client_name: row.client_name,
    estimated_hours: row.estimated_hours,
    actual_hours: row.actual_hours,
    accuracy_pct:
      row.estimated_hours > 0
        ? Math.round((row.actual_hours / row.estimated_hours) * 1000) / 10
        : 0,
  }))
}

// ── Revenue Report ──────────────────────────────────────────────────
export interface RevenueReport {
  total_revenue: number
  total_invoiced: number
  total_paid: number
  by_month: Array<{ month: string; amount: number }>
  by_vertical: Array<{ vertical: string; amount: number }>
}

/**
 * Aggregate revenue from invoices.
 * total_revenue = sum of all invoice amounts (any status except void)
 * total_invoiced = sum of sent + paid + overdue
 * total_paid = sum of paid
 * by_month = paid invoices grouped by month (from paid_at)
 * by_vertical = paid invoices grouped by client vertical
 */
export async function getRevenueReport(
  db: D1Database,
  orgId: string,
  _period?: string
): Promise<RevenueReport> {
  // Total revenue (all non-void invoices)
  const totalsResult = await db
    .prepare(
      `SELECT
         COALESCE(SUM(CASE WHEN status != 'void' THEN amount ELSE 0 END), 0) AS total_revenue,
         COALESCE(SUM(CASE WHEN status IN ('sent', 'paid', 'overdue') THEN amount ELSE 0 END), 0) AS total_invoiced,
         COALESCE(SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END), 0) AS total_paid
       FROM invoices
       WHERE org_id = ?`
    )
    .bind(orgId)
    .first<{ total_revenue: number; total_invoiced: number; total_paid: number }>()

  // Paid invoices by month (using paid_at)
  const byMonthResult = await db
    .prepare(
      `SELECT
         substr(paid_at, 1, 7) AS month,
         SUM(amount) AS amount
       FROM invoices
       WHERE org_id = ?
         AND status = 'paid'
         AND paid_at IS NOT NULL
       GROUP BY substr(paid_at, 1, 7)
       ORDER BY month ASC`
    )
    .bind(orgId)
    .all<{ month: string; amount: number }>()

  // Paid invoices by client vertical
  const byVerticalResult = await db
    .prepare(
      `SELECT
         COALESCE(en.vertical, 'unknown') AS vertical,
         SUM(i.amount) AS amount
       FROM invoices i
       JOIN entities en ON en.id = i.entity_id AND en.org_id = ?
       WHERE i.org_id = ?
         AND i.status = 'paid'
       GROUP BY en.vertical
       ORDER BY amount DESC`
    )
    .bind(orgId, orgId)
    .all<{ vertical: string; amount: number }>()

  return {
    total_revenue: totalsResult?.total_revenue ?? 0,
    total_invoiced: totalsResult?.total_invoiced ?? 0,
    total_paid: totalsResult?.total_paid ?? 0,
    by_month: byMonthResult.results,
    by_vertical: byVerticalResult.results,
  }
}

// ── Engagement Health ───────────────────────────────────────────────
export interface EngagementHealth {
  avg_days_to_completion: number
  avg_parking_lot_items: number
  total_engagements: number
  on_time_pct: number
}

/**
 * Engagement health metrics from completed engagements.
 * avg_days_to_completion = avg(julianday(actual_end) - julianday(start_date))
 * avg_parking_lot_items = avg count of parking lot items per engagement
 * on_time_pct = percentage of completed engagements where actual_end <= estimated_end
 */
export async function getEngagementHealth(
  db: D1Database,
  orgId: string
): Promise<EngagementHealth> {
  // Core metrics from completed engagements
  const metricsResult = await db
    .prepare(
      `SELECT
         COUNT(*) AS total_engagements,
         COALESCE(AVG(
           CASE
             WHEN actual_end IS NOT NULL AND start_date IS NOT NULL
             THEN julianday(actual_end) - julianday(start_date)
             ELSE NULL
           END
         ), 0) AS avg_days_to_completion,
         COALESCE(SUM(
           CASE
             WHEN actual_end IS NOT NULL AND estimated_end IS NOT NULL
                  AND actual_end <= estimated_end
             THEN 1
             ELSE 0
           END
         ), 0) AS on_time_count,
         COALESCE(SUM(
           CASE
             WHEN actual_end IS NOT NULL AND estimated_end IS NOT NULL
             THEN 1
             ELSE 0
           END
         ), 0) AS has_both_dates_count
       FROM engagements
       WHERE org_id = ?
         AND status = 'completed'`
    )
    .bind(orgId)
    .first<{
      total_engagements: number
      avg_days_to_completion: number
      on_time_count: number
      has_both_dates_count: number
    }>()

  // Average parking lot items per completed engagement
  const parkingLotResult = await db
    .prepare(
      `SELECT
         COALESCE(AVG(pl_count), 0) AS avg_parking_lot_items
       FROM (
         SELECT e.id, COUNT(p.id) AS pl_count
         FROM engagements e
         LEFT JOIN parking_lot p ON p.engagement_id = e.id
         WHERE e.org_id = ?
           AND e.status = 'completed'
         GROUP BY e.id
       )`
    )
    .bind(orgId)
    .first<{ avg_parking_lot_items: number }>()

  const totalEngagements = metricsResult?.total_engagements ?? 0
  const hasBothDatesCount = metricsResult?.has_both_dates_count ?? 0
  const onTimeCount = metricsResult?.on_time_count ?? 0

  return {
    avg_days_to_completion: Math.round((metricsResult?.avg_days_to_completion ?? 0) * 10) / 10,
    avg_parking_lot_items: Math.round((parkingLotResult?.avg_parking_lot_items ?? 0) * 10) / 10,
    total_engagements: totalEngagements,
    on_time_pct:
      hasBothDatesCount > 0 ? Math.round((onTimeCount / hasBothDatesCount) * 1000) / 10 : 0,
  }
}

// ── Follow-up Compliance ────────────────────────────────────────────
export interface FollowUpCompliance {
  total: number
  completed_on_time: number
  completed_late: number
  missed: number
  compliance_pct: number
}

/**
 * Follow-up compliance metrics.
 * completed_on_time = completed where completed_at <= scheduled_for
 * completed_late = completed where completed_at > scheduled_for
 * missed = status = 'scheduled' and scheduled_for < now (past due, never completed)
 * compliance_pct = completed_on_time / total * 100
 */
export async function getFollowUpCompliance(
  db: D1Database,
  orgId: string
): Promise<FollowUpCompliance> {
  const result = await db
    .prepare(
      `SELECT
         COUNT(*) AS total,
         COALESCE(SUM(
           CASE
             WHEN status = 'completed' AND completed_at IS NOT NULL
                  AND completed_at <= scheduled_for
             THEN 1
             ELSE 0
           END
         ), 0) AS completed_on_time,
         COALESCE(SUM(
           CASE
             WHEN status = 'completed' AND completed_at IS NOT NULL
                  AND completed_at > scheduled_for
             THEN 1
             ELSE 0
           END
         ), 0) AS completed_late,
         COALESCE(SUM(
           CASE
             WHEN status = 'scheduled' AND scheduled_for < datetime('now')
             THEN 1
             ELSE 0
           END
         ), 0) AS missed
       FROM follow_ups
       WHERE org_id = ?`
    )
    .bind(orgId)
    .first<{
      total: number
      completed_on_time: number
      completed_late: number
      missed: number
    }>()

  const total = result?.total ?? 0
  const completedOnTime = result?.completed_on_time ?? 0
  const completedLate = result?.completed_late ?? 0
  const missed = result?.missed ?? 0

  return {
    total,
    completed_on_time: completedOnTime,
    completed_late: completedLate,
    missed,
    compliance_pct: total > 0 ? Math.round((completedOnTime / total) * 1000) / 10 : 0,
  }
}
