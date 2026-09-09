/**
 * Operator runtime-summary reader (ADR 0043 path B) for the admin fleet view.
 *
 * Reads the console-side per-customer summary mirror (`operator_runtime_summary`)
 * that each Machine pushes to. The fleet roster, alert feed, and activity
 * columns render entirely from this store, so the view stays answerable even
 * when an individual Machine is down. Deep, fresh per-operator detail uses the
 * live read path (src/lib/operator/runtime-read.ts) instead — never this mirror.
 *
 * Staleness is computed at render time (`summaryFreshness`) so a column never
 * lies in the reassuring direction: a `pushed_at` hours old reads as stale
 * regardless of when the row was last touched — same discipline as
 * fleet-status heartbeatDisplay.
 */

import type { D1Database } from '@cloudflare/workers-types'

export type SummaryStatus = 'green' | 'yellow' | 'red' | 'unknown'

export interface RuntimeSummaryRow {
  entity_id: string
  customer_slug: string
  summary_status: SummaryStatus
  open_alerts: number
  draft_queue_depth: number | null
  last_activity_ts: string | null
  pushed_at: string
  updated_at: string
}

/**
 * List every customer's runtime summary, ordered by slug. This is a fleet-wide
 * read of per-customer summary rows — never a join across two Machines' runtime
 * D1 (ADR 0009). Each row originated from exactly one Machine's push.
 */
export async function listRuntimeSummary(db: D1Database): Promise<RuntimeSummaryRow[]> {
  const result = await db
    .prepare(
      `SELECT entity_id, customer_slug, summary_status, open_alerts,
              draft_queue_depth, last_activity_ts, pushed_at, updated_at
         FROM operator_runtime_summary
        ORDER BY customer_slug ASC`
    )
    .all<RuntimeSummaryRow>()
  return result.results ?? []
}

/** Read one customer's runtime summary, or null when none has been pushed. */
export async function getRuntimeSummary(
  db: D1Database,
  customerSlug: string
): Promise<RuntimeSummaryRow | null> {
  const row = await db
    .prepare(
      `SELECT entity_id, customer_slug, summary_status, open_alerts,
              draft_queue_depth, last_activity_ts, pushed_at, updated_at
         FROM operator_runtime_summary
        WHERE customer_slug = ?`
    )
    .bind(customerSlug)
    .first<RuntimeSummaryRow>()
  return row ?? null
}
