/**
 * tool_failing:<tool> — a watched tool failing on every recent call
 * (ss#2793 follow-on, migration 0119, hermes-smd-overlay#401).
 *
 * THE GAP. A rostered real estate agent emails a voice memo to their seat, the
 * transcriber refuses, and the agent reads "could not transcribe" in their own
 * thread. Nobody at SMD knows. The sticky-stop ladder halts a seat at eight
 * tool failures in ten minutes and pages nothing before that; send_refused
 * watches only a cron turn's external sends; connector_down watches only MCP
 * servers. Three failures a day of the one tool a person depends on was
 * invisible from here, because no per-tool outcome left the Machine.
 *
 * THE SIGNAL. The seat reads its own audit ledger each beat and ships
 * `tool_failures`: per watched tool (the overlay's WATCHED_TOOLS, today
 * voice_note_transcribe and record_store_write), the run of consecutive
 * non-ok outcomes ending at the tool's newest call in the trailing day. The
 * map is keyed by tool name, so this Worker never needs to know which tools
 * are watched: adding one on the seat is the whole act.
 *
 * THE DECISION, the connector_down tri-state without the age gate:
 *   consecutive_failures === 0  → proven success: push inactive (resolves).
 *   >= TOOL_FAILING_MIN_FAILURES → push active.
 *   anything between             → push NOTHING (hold): one or two failures are
 *     a bad recording or a transient refusal, and pushing inactive would emit
 *     a false RECOVERED on the way INTO an outage.
 * No run-age gate, unlike connector_down: a connector's failure burst can
 * self-heal inside Hermes' breaker cooldown, but a tool refusing three people
 * in a row is not a burst, it is the product dark for each of them. NULL map
 * pushes nothing (whole-map hold); a tool absent from the map pushes nothing
 * (it did not run today, which is neither failure nor recovery).
 *
 * Resolves ONLY on a proven success. A run that ages out of the seat's window
 * without a success leaves the alert open with nothing to evaluate it; that is
 * a stale hold, and stale-holds.ts surfaces it for the manual path.
 */

import { TOOL_FAILING_PREFIX } from './conditions'
import type { ConditionState, FleetCondition, FleetStatusRow } from './index'

/** Consecutive failures before the page. Three people refused in a row. */
export const TOOL_FAILING_MIN_FAILURES = 3

export interface ToolFailureEntry {
  consecutive_failures: number
  first_error_ts?: string
  last_error_ts?: string
  last_ok_ts?: string
  last_error?: string
}

function nonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** One entry, parsed-not-cast; null drops it (absence = hold for that tool). */
function parseEntry(value: unknown): ToolFailureEntry | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const count = nonNegInt(raw.consecutive_failures)
  if (count === null) return null
  const entry: ToolFailureEntry = { consecutive_failures: count }
  for (const field of ['first_error_ts', 'last_error_ts', 'last_ok_ts'] as const) {
    if (typeof raw[field] === 'string') entry[field] = raw[field]
  }
  if (typeof raw.last_error === 'string') entry.last_error = raw.last_error.slice(0, 200)
  return entry
}

/**
 * Parse a row's tool_failures_json defensively (fleet-view discipline: one
 * corrupt row degrades to null, a hold, and never aborts the fleet loop). The
 * ingest already validated entries; this is the Worker's own trust boundary.
 */
export function parseToolFailuresMap(
  json: string | null | undefined
): Record<string, ToolFailureEntry> | null {
  if (json === null || json === undefined) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const out: Record<string, ToolFailureEntry> = {}
  for (const [tool, value] of Object.entries(raw as Record<string, unknown>)) {
    const entry = parseEntry(value)
    if (entry !== null) out[tool] = entry
  }
  return out
}

/** The tool_failing:<tool> states for one row. */
export function toolFailingConditions(row: FleetStatusRow): ConditionState[] {
  const out: ConditionState[] = []
  const tools = parseToolFailuresMap(row.tool_failures_json)
  if (tools === null) return out
  for (const [tool, entry] of Object.entries(tools)) {
    const condition: FleetCondition = `${TOOL_FAILING_PREFIX}${tool}`
    if (entry.consecutive_failures === 0) {
      out.push({
        customer_slug: row.customer_slug,
        condition,
        active: false,
        detail: `last ${tool} call succeeded${entry.last_ok_ts ? ` at ${entry.last_ok_ts}` : ''}`,
      })
      continue
    }
    if (entry.consecutive_failures < TOOL_FAILING_MIN_FAILURES) continue // hold
    const since = entry.first_error_ts ? ` since ${entry.first_error_ts}` : ''
    out.push({
      customer_slug: row.customer_slug,
      condition,
      active: true,
      detail:
        `${entry.consecutive_failures} consecutive ${tool} failures${since}; ` +
        `last error: ${entry.last_error ?? '(no message captured)'}. ` +
        'Every person who used this tool in that run got a refusal instead of the result. ' +
        'Auto-resolves on the next successful call.',
    })
  }
  return out
}
