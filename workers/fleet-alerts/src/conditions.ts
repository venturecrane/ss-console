/**
 * The prefixed condition classes, in one place (ss#2316).
 *
 * Four conditions carry a payload after a colon: the MCP server, the credential's
 * server, the authored spec key, the expected webhook tool. Before this module the
 * prefixes were repeated string literals across five files, and `stale-holds.ts`
 * additionally sliced them in SQL by hardcoded character offsets (16 and 26) that
 * duplicated the literals' LENGTH. That coupling was invisible: renaming a prefix
 * updated every TypeScript site (they all slice by `.length`) while the SQL kept
 * cutting at the old column, handing `json_extract` a garbage path that returns
 * NULL — which is exactly this query's "stranded" signal, so every alert of that
 * class would report stranded forever and nothing would fail.
 *
 * The rule this module exists to enforce: a prefix's length is never written down.
 * TypeScript slices with `.length`; SQL receives the prefix as a bound parameter
 * and derives the offset with `length(?) + 1`. There is no third place to update.
 */

import type { FleetCondition } from './index'

/** Per-MCP-server outage (ADR 0080 / ss#1990). Payload: the server name. */
export const CONNECTOR_DOWN_PREFIX = 'connector_down:'

/** Pre-expiry credential warning (ss#2148). Payload: the server name. */
export const CONNECTOR_TOKEN_EXPIRING_PREFIX = 'connector_token_expiring:'

/** Authored spec declared but not installed (ss#2234). Payload: `<class>.<prop>`. */
export const SPEC_CONTROL_BROKEN_PREFIX = 'spec_control_broken:'

/** Expected webhook tool not offered (ss#2287). Payload: the tool name. */
export const WEBHOOK_SURFACE_MISSING_PREFIX = 'webhook_surface_missing:'

/**
 * The web Worker's own edge failing its outside-in probe (review 2026-09-10,
 * wave 8.2). Not prefixed: the target rides in `customer_slug`, because there
 * is no seat behind this alert. stale-holds.ts excludes it by this constant.
 */
export const EDGE_DOWN_CONDITION = 'edge_down'

/**
 * Every prefixed class, for the guard test that asserts no caller has
 * reintroduced a hardcoded offset.
 *
 * @public Guard surface. workers/fleet-alerts/src/stale-holds.test.ts imports it to assert no
 * caller reintroduced a hardcoded offset. No runtime caller, by design.
 */
export const CONDITION_PREFIXES = [
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  SPEC_CONTROL_BROKEN_PREFIX,
  WEBHOOK_SURFACE_MISSING_PREFIX,
] as const

/**
 * The payload after a prefix, or null when the condition is not of that class.
 * Single accessor so no call site writes an offset of its own.
 */
export function conditionPayload(condition: string, prefix: string): string | null {
  return condition.startsWith(prefix) ? condition.slice(prefix.length) : null
}

/**
 * Human labels for the unprefixed conditions; the prefixed classes are labelled
 * in conditionLabel() from their payload. Moved here from index.ts when that
 * file crossed the 500-line ceiling (ss#2488 part 2) -- the labels depend on
 * the prefix constants above, so this is where they belong anyway.
 */
const CONDITION_LABEL: Record<string, string> = {
  heartbeat_red: 'Machine not heartbeating',
  // NOT "Cost breaker": this condition fires on the sticky-stop ladder, which
  // four meters drive (consecutive tool failures, refusal cascade, runtime
  // budget, cost threshold). Naming one of them in the subject asserts a cause
  // the condition never measured -- on 2026-09-01 ashton-price stopped on a
  // bad credential and the SEV1 subject said "Cost breaker". The actual cause
  // now travels on the beat and is rendered in the detail line instead.
  hard_stop: 'Seat stopped itself (HARD_STOP)',
  scheduler_error: 'Cron scheduler broken/unreadable',
  work_overdue: 'Scheduled work not firing',
  connector_check_error: 'Connector health check broken (outages not counted)',
  spec_control_unprovable: 'Authored-spec manifest unreadable (spec health unknown)',
  webhook_surface_unprovable: 'Webhook tool surface unresolvable (warn-tier health unknown)',
  gateway_loop_wedged: 'Gateway event loop wedged (Operator not answering)',
  gateway_loop_unprovable: 'Gateway loop heartbeat unreadable (loop health unknown)',
  gateway_restarted: 'Seat supervisor restarted the gateway',
  gateway_supervisor_refusing: 'Seat supervisor STOPPED restarting (budget spent, needs a human)',
  gateway_supervisor_inert: 'Seat supervisor cannot act (wedge would not self-recover)',
  edge_down: 'Web edge not answering /api/health from outside',
  send_refused:
    "a routine's outbound send was refused by a gate, or a wake with needs-you items sent nothing",
}

/**
 * The hard_stop detail line, which is the only place the reader learns WHY.
 *
 * The subject deliberately names no meter (see CONDITION_LABEL.hard_stop), so
 * if the cause is on the row it has to appear here. A seat not yet
 * reprovisioned onto the cause-carrying overlay (hermes-smd-overlay#341)
 * reports the level alone, and this degrades to exactly what it always said
 * rather than claiming a cause it does not have.
 *
 * Takes the structural fields rather than a FleetStatusRow so this file stays
 * free of an import from index.ts, which imports the labels from here.
 *
 * The line always ends with the clear surface and its runbook: on 2026-09-01
 * a responder who never saw either cleared a HARD_STOP by raw sqlite on the
 * seat while the admin form sat built and unused
 * (docs/runbooks/operator/incidents/2026-09-01-sticky-stop-raw-sqlite-bypass.md).
 * The page is where a responder actually looks, so the page hands them the
 * path.
 */
export function hardStopDetail(stop: {
  customer_slug: string
  sticky_stop_level: string | null
  sticky_stop_reason: string | null
  sticky_stop_condition: string | null
}): string {
  const parts = [`sticky_stop_level=${stop.sticky_stop_level ?? 'null'}`]
  if (stop.sticky_stop_condition) parts.push(`condition=${stop.sticky_stop_condition}`)
  if (stop.sticky_stop_reason) parts.push(stop.sticky_stop_reason)
  parts.push(
    `clear: admin.smd.services/admin/operator/${stop.customer_slug} ` +
      '(runbook docs/runbooks/operator/sticky-stop-clear.md)'
  )
  // Joined with a pipe, not an em dash: this string is rendered into the alert
  // email, and em dashes are banned in shipped copy (tests/forbidden-strings).
  return parts.join(' | ')
}

/** Label lookup with the per-connector prefix form (ADR 0080). */
export function conditionLabel(condition: FleetCondition): string {
  const down = conditionPayload(condition, CONNECTOR_DOWN_PREFIX)
  if (down !== null) return `Connector failing: ${down}`
  const expiring = conditionPayload(condition, CONNECTOR_TOKEN_EXPIRING_PREFIX)
  if (expiring !== null) return `Connector credential expiring: ${expiring}`
  const spec = conditionPayload(condition, SPEC_CONTROL_BROKEN_PREFIX)
  if (spec !== null) return `Authored spec declared but not installed: ${spec}`
  const tool = conditionPayload(condition, WEBHOOK_SURFACE_MISSING_PREFIX)
  if (tool !== null) return `Webhook tool expected but not offered: ${tool}`
  return CONDITION_LABEL[condition] ?? condition
}
