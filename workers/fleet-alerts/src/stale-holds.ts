/**
 * Open alerts stranded by the per-field NULL-hold.
 *
 * Every condition holds rather than resolving when its source field goes NULL —
 * a seat that stopped reporting has gone quiet, not recovered. The cost of that
 * correctness is that an alert can sit open with nothing left to evaluate it.
 * This query finds those, one LEFT JOIN, per-condition clauses hand-written
 * because each condition knows its own source column. No UI (four seats); the
 * runbook documents the manual-resolve UPDATE.
 *
 * Extracted from index.ts (ss#2234) to keep that file under its line ceiling,
 * the same move `token-expiry.ts` and `spec-control.ts` made.
 *
 * ss#2316 removed two hazards from the payload-carrying clauses:
 *
 *   1. The prefixes were sliced by hardcoded 1-indexed offsets (16, 26) that
 *      silently duplicated the prefix literals' length. They are now bound
 *      parameters and the offset is derived in SQL with `length(?) + 1`, so a
 *      rename cannot desync the query from the constant. See ./conditions.
 *
 *   2. The key was interpolated into a JSON path (`'$."' || key || '"'`). A key
 *      containing a double quote produced a malformed path, and SQLite answers a
 *      malformed path with NULL rather than an error — which is this query's
 *      "stranded" signal, so a healthy connector whose name contained a quote
 *      reported stranded forever. `json_each` takes the key as a VALUE and
 *      compares it, so no key can be read as syntax. Semantics are preserved
 *      exactly: `NOT EXISTS (key present AND value non-null)` is true in the same
 *      two cases the old `json_extract(...) IS NULL` was — key absent, or key
 *      present with a JSON null value.
 */

import {
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  SPEC_CONTROL_BROKEN_PREFIX,
  WEBHOOK_SURFACE_MISSING_PREFIX,
} from './conditions'
import type { StaleHold } from './index'

/**
 * A payload-carrying clause: the condition matches the prefix, and either the
 * whole map is NULL or the map holds nothing live under the sliced key.
 *
 * Two placeholders per clause, both bound to the SAME prefix constant — the first
 * builds the LIKE pattern, the second sets the slice offset. Written as `?` rather
 * than `?1`/`?2` so the statement stays on D1's plainest binding path.
 */
function strandedKeyClause(mapColumn: string): string {
  return `(
              s.condition LIKE ? || '%'
              AND (
                f.${mapColumn} IS NULL
                OR NOT EXISTS (
                  SELECT 1 FROM json_each(f.${mapColumn}) je
                   WHERE je.key = substr(s.condition, length(?) + 1)
                     AND je.value IS NOT NULL
                )
              )
            )`
}

export const STALE_HOLDS_SQL = `SELECT s.customer_slug AS customer_slug, s.condition AS condition
         FROM fleet_alert_state s
         LEFT JOIN fleet_status f ON f.customer_slug = s.customer_slug
        WHERE s.status = 'open'
          AND (
            f.customer_slug IS NULL
            OR (s.condition = 'scheduler_error' AND f.scheduler_ok IS NULL)
            OR (s.condition = 'work_overdue' AND f.scheduler_max_overdue_seconds IS NULL)
            OR (s.condition = 'heartbeat_red' AND f.last_heartbeat_ts IS NULL)
            OR (s.condition = 'hard_stop' AND f.sticky_stop_level IS NULL)
            OR (s.condition = 'connector_check_error' AND f.connector_check_ok IS NULL)
            OR ${strandedKeyClause('connectors_json')}
            OR ${strandedKeyClause('connector_token_age_json')}
            OR (s.condition = 'spec_control_unprovable' AND f.spec_control_ok IS NULL)
            -- A spec_control_broken key that VANISHES from the map is not
            -- stranded: a withdrawn declaration auto-resolves through
            -- openSpecControlKeys. Only a whole-map NULL strands it, so this
            -- clause deliberately does not test the individual key.
            OR (s.condition LIKE ? || '%' AND f.spec_control_json IS NULL)
            OR (s.condition = 'webhook_surface_unprovable' AND f.webhook_surface_ok IS NULL)
            -- ss#2287, same shape as spec_control_broken above: a tool that
            -- merely vanishes from the map is a WITHDRAWN expectation and
            -- auto-resolves through openWebhookSurfaceKeys. Only a whole-map
            -- NULL strands it.
            OR (s.condition LIKE ? || '%' AND f.webhook_surface_json IS NULL)
            -- ss#2488 part 2. Five conditions, one source column each. An alert
            -- stranded here is a seat that stopped reporting the field: an
            -- overlay rollback to a pre-part-2 ref, a reprovision to a Hermes
            -- pin with no loop heartbeat, or a gate that died. The NULL-hold
            -- means it will never auto-resolve; surface it for the manual path.
            OR (s.condition = 'gateway_loop_wedged' AND f.gateway_loop_age_seconds IS NULL)
            OR (s.condition = 'gateway_loop_unprovable' AND f.gateway_loop_ok IS NULL)
            OR (s.condition = 'gateway_restarted' AND f.gateway_restarts_last_hour IS NULL)
            OR (s.condition = 'gateway_supervisor_refusing' AND f.gateway_supervisor_state IS NULL)
            OR (s.condition = 'gateway_supervisor_inert' AND f.gateway_supervisor_state IS NULL)
            -- ss#2547 has NO clause here on purpose. send_refused is
            -- event-shaped: its row is written with status='resolved' the
            -- moment it pages and is never open, so the s.status = 'open'
            -- filter above already excludes it. A clause of its own would be
            -- dead code that reads as coverage. See ./send-refused.
          )
        ORDER BY s.customer_slug ASC, s.condition ASC`

/**
 * Bindings for {@link STALE_HOLDS_SQL}, in placeholder order. Every value is a
 * prefix constant — the query contains no prefix literal and no offset, so this
 * list is the ONLY thing that decides what the SQL matches and where it slices.
 */
export const STALE_HOLDS_BINDINGS: readonly string[] = [
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  SPEC_CONTROL_BROKEN_PREFIX,
  WEBHOOK_SURFACE_MISSING_PREFIX,
]

export async function getStaleHolds(db: D1Database): Promise<StaleHold[]> {
  const result = await db
    .prepare(STALE_HOLDS_SQL)
    .bind(...STALE_HOLDS_BINDINGS)
    .all<StaleHold>()
  return result.results ?? []
}
