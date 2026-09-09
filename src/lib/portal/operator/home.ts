/**
 * Home / Today runtime feed (client-portal §5.1): the one live number the
 * dashboard shows for the signed-in client's own operator.
 *
 * `needsAttentionCount` is the Machine-pushed `draft_queue_depth` in this
 * customer's own `operator_runtime_summary` row (ADR 0043 path B mirror). The
 * Home must never fabricate a review queue the entitlements did not author
 * (ADR 0035 / client-portal §5.1): the count renders only when the Machine
 * itself reported a pending-review depth. No row, NULL depth, or an unreadable
 * mirror all resolve to 0 — an honest absence, not a zeroed queue implying one
 * exists.
 *
 * The recent-activity and escalation feeds that once lived beside this
 * (`loadHomeFeeds`, a live `audit_log` read through the ADR 0043 seam) were
 * removed 2026-09-09: no page called them. The Activity page remains the
 * client's live audit view.
 */

import type { D1Database } from '@cloudflare/workers-types'

/**
 * This customer's Machine-pushed pending-review depth from the path-B summary
 * mirror. Reads exactly one row for exactly this customer — never a fleet-wide
 * read (that is the admin view's job; ADR 0052 keeps this surface scoped to
 * the client's own operator). Missing row / NULL depth / read failure → 0.
 */
export async function readDraftQueueDepth(db: D1Database, customerSlug: string): Promise<number> {
  try {
    const row = await db
      .prepare('SELECT draft_queue_depth FROM operator_runtime_summary WHERE customer_slug = ?')
      .bind(customerSlug)
      .first<{ draft_queue_depth: number | null }>()
    const depth = row?.draft_queue_depth
    return typeof depth === 'number' && Number.isFinite(depth) && depth > 0 ? Math.floor(depth) : 0
  } catch {
    // A missing mirror table (fresh environment) is an honest 0, not a crash.
    return 0
  }
}
