/**
 * Fleet-roster reader for the admin Operator console landing
 * (`/admin/operator`) — design doc docs/design/operator/01-admin-portal.md §4.1.
 *
 * The roster is the "is anything on fire across all my operators" view: one
 * row per client, scannable as the fleet grows. It composes three console-side
 * projections, never a cross-Machine runtime join (ADR 0009):
 *
 *   1. `customer_configs`            — identity, persona roster, authority posture
 *   2. `operator_runtime_summary`    — health/alerts/last-activity mirror (ADR 0043 B)
 *   3. `fleet_status`                — per-customer heartbeat liveness (ADR 0023)
 *
 * This module owns only #1's read + the pure derivations the page renders
 * (posture label, combined health). The summary/heartbeat readers are the
 * frozen seam (src/lib/admin/runtime-summary.ts, fleet-status.ts); the page
 * joins them by entity_id (slug fallback), exactly as the costs page does.
 *
 * Fleet-view discipline (foundations §7, ADR 0043): one corrupt config row must
 * never 500 the whole fleet view. `listFleetRoster` parses each row defensively
 * — a malformed personas/authority column degrades that single row to a flagged
 * `config_error` state with an empty persona list and the launch-default
 * posture, and the rest of the fleet still renders.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  DEFAULT_AUTHORITY_POSTURE,
  parseAuthorityPosture,
  resolveAllDomains,
  type AuthorityPosture,
} from '../operator/authority'
import type { SummaryStatus } from './runtime-summary'
import {
  CONNECTOR_BACKSTOP_MIN_FAILURES,
  CONNECTOR_BACKSTOP_RUN_AGE_SECONDS,
  CONNECTOR_DOWN_MIN_FAILURES,
  CONNECTOR_DOWN_RUN_AGE_SECONDS,
  WORK_OVERDUE_RED_SECONDS,
} from './fleet-status'

export interface RosterPersona {
  slug: string
  name: string
  status: string
}

export interface RosterCustomer {
  entity_id: string
  customer_slug: string
  /** Display name from `entities.name`; null when no entity row joins. */
  entity_name: string | null
  /** Projected `customer_configs.vertical`; null before CI sync backfills it. */
  vertical: string | null
  personas: RosterPersona[]
  authority: AuthorityPosture
  /**
   * Set when this row's projected JSON was malformed. The row still renders
   * (degraded) so one corrupt config never blanks the fleet view — the flag
   * lets the page surface "this operator's config needs attention" honestly
   * instead of silently showing zero personas.
   */
  config_error: string | null
}

interface RosterDbRow {
  entity_id: string
  customer_slug: string
  personas_json: string
  authority_json: string | null
  vertical: string | null
}

interface EntityNameRow {
  id: string
  name: string
}

/**
 * Enumerate every Operator customer for the fleet roster, ordered by slug.
 * One batched read of `customer_configs` + one batched name lookup — no
 * per-row query and no per-customer Machine round-trip.
 */
export async function listFleetRoster(db: D1Database): Promise<RosterCustomer[]> {
  const configResult = await db
    .prepare(
      `SELECT entity_id, customer_slug, personas_json, authority_json, vertical
         FROM customer_configs
        ORDER BY customer_slug ASC`
    )
    .all<RosterDbRow>()
  const rows = configResult.results ?? []
  if (rows.length === 0) return []

  const namesByEntity = await loadEntityNames(
    db,
    rows.map((r) => r.entity_id)
  )

  return rows.map((row) => projectRosterRow(row, namesByEntity.get(row.entity_id) ?? null))
}

async function loadEntityNames(db: D1Database, entityIds: string[]): Promise<Map<string, string>> {
  const placeholders = entityIds.map(() => '?').join(',')
  const result = await db
    .prepare(`SELECT id, name FROM entities WHERE id IN (${placeholders})`)
    .bind(...entityIds)
    .all<EntityNameRow>()
  const map = new Map<string, string>()
  for (const row of result.results ?? []) map.set(row.id, row.name)
  return map
}

function projectRosterRow(row: RosterDbRow, entityName: string | null): RosterCustomer {
  const base = {
    entity_id: row.entity_id,
    customer_slug: row.customer_slug,
    entity_name: entityName,
    vertical: row.vertical,
  }
  try {
    return {
      ...base,
      personas: parsePersonas(row.personas_json),
      // parseAuthorityPosture is total (never throws); a malformed personas
      // column is what flips a row into the error state.
      authority: parseAuthorityPosture(safeJsonParse(row.authority_json)),
      config_error: null,
    }
  } catch (err) {
    return {
      ...base,
      personas: [],
      authority: { ...DEFAULT_AUTHORITY_POSTURE },
      config_error: err instanceof Error ? err.message : String(err),
    }
  }
}

function parsePersonas(raw: string): RosterPersona[] {
  const parsed: unknown = JSON.parse(raw)
  if (!Array.isArray(parsed)) throw new Error('personas_json is not an array')
  return parsed.map((p) => {
    if (typeof p !== 'object' || p === null) throw new Error('persona entry is not an object')
    const rec = p as Record<string, unknown>
    return {
      slug: typeof rec.slug === 'string' ? rec.slug : '',
      name: typeof rec.name === 'string' ? rec.name : '',
      status: typeof rec.status === 'string' ? rec.status : 'unknown',
    }
  })
}

/** Null-tolerant JSON parse; null/undefined → null, malformed → throws. */
function safeJsonParse(value: string | null | undefined): unknown {
  if (value === null || value === undefined) return null
  return JSON.parse(value)
}

// ===========================================================================
// Pure derivations the roster page renders
// ===========================================================================

export type PostureLabel = 'Managed' | 'Co-Managed' | 'Self-Managed'

export interface PosturePill {
  label: PostureLabel
  classes: string
}

const POSTURE_BADGE_STRUCTURE =
  'inline-flex items-center px-2 py-0.5 rounded-[var(--ss-radius-badge)] ' +
  'text-[10px] font-medium uppercase tracking-wide whitespace-nowrap'

/**
 * Derive the posture chip from the resolved authority posture (foundations
 * §4.1): the label is a description of the *switch pattern*, not a SKU —
 * Managed (every client switch off), Self-Managed (every switch client),
 * Co-Managed (a mix). SMD always retains full control regardless; this chip
 * describes only the client org's operability.
 */
export function derivePostureLabel(posture: AuthorityPosture | null): PostureLabel {
  const holders = Object.values(resolveAllDomains(posture))
  const clientCount = holders.filter((h) => h === 'client').length
  if (clientCount === 0) return 'Managed'
  if (clientCount === holders.length) return 'Self-Managed'
  return 'Co-Managed'
}

export function posturePill(posture: AuthorityPosture | null): PosturePill {
  const label = derivePostureLabel(posture)
  const tone =
    label === 'Managed'
      ? 'bg-[color:var(--ss-color-primary)] text-white'
      : label === 'Self-Managed'
        ? 'bg-[color:var(--ss-color-complete)] text-white'
        : 'bg-[color:var(--ss-color-attention)] text-white'
  return { label, classes: `${POSTURE_BADGE_STRUCTURE} ${tone}` }
}

export type RosterHealthColor = 'green' | 'yellow' | 'red' | 'gray'

export interface RosterHealth {
  color: RosterHealthColor
  /** Relative liveness label from the heartbeat ("47s ago" / "stale 12m"). */
  label: string
  /** Secondary line when the summary mirror reports a non-green rollup. */
  note: string | null
}

// gray ("no signal") ranks ABOVE green: an unknown-liveness Machine must never
// read calmer than a live one. Ordering: green < gray < yellow < red.
const HEALTH_RANK: Record<RosterHealthColor, number> = { green: 0, gray: 1, yellow: 2, red: 3 }

/**
 * Combine the heartbeat liveness ("is the process alive", fleet_status) with
 * the runtime-summary rollup ("is the operator OK" — folds in sticky-stop,
 * escalation pressure, connector health per migration 0052) into one fleet
 * dot. The heartbeat is the source of truth for LIVENESS; the summary may only
 * ESCALATE it (operator reports a warning/problem), never paint a Machine green
 * or downgrade a live-green Machine to "unknown". So only a yellow/red summary
 * participates — a green or absent summary leaves the heartbeat verdict standing.
 *
 * Previously the summary's own gray/green could override the heartbeat because
 * gray was ranked below green: a Machine that had never sent a heartbeat (gray)
 * but carried a stale "green" summary painted GREEN — the column reading calmer
 * than reality, the one thing a fleet dot must never do. `summaryStatus` is null
 * when no Machine has pushed a summary yet.
 */
/**
 * Machine self-check signals (WP-2 scheduler + ADR 0080 connectors), bundled
 * so rosterHealth stays within the parameter ceiling. `ok`: scheduler verdict
 * 1 healthy / 0 broken / NULL unreported; `maxOverdueSeconds`: seconds the
 * most-overdue job is past its next_run_at. The two connector fields are
 * optional so pre-0080 call sites and tests stay valid.
 */
export interface SchedulerSignal {
  ok: number | null
  maxOverdueSeconds: number | null
  /** Connector self-check verdict: 1 healthy / 0 broken / NULL unreported. */
  connectorCheckOk?: number | null
  /** fleet_status.connectors_json verbatim; parsed defensively here. */
  connectorsJson?: string | null
  /** ss#2276: 1 = crons deliberately contained, 0 normal, NULL unreported. */
  cronContainment?: number | null
  /** ss#2488 part 2: seconds since the gateway loop last beat; NULL = hold. */
  gatewayLoopAgeSeconds?: number | null
  /** ss#2488 part 2: 1 could look / 0 could not / NULL unreported. */
  gatewayLoopOk?: number | null
  /** ss#2488 part 2: the part-1 supervisor's state word, or NULL. */
  gatewaySupervisorState?: string | null
  /**
   * Migration 0112: which of the ladder's four meters tripped the stop. Lives
   * on the signal bag rather than beside `stickyStopLevel` because a sixth
   * positional parameter trips the arity ceiling -- and this IS a signal read
   * off the same fleet_status row as everything else here.
   */
  stickyStopCondition?: string | null
}

/**
 * Same number as the fleet-alerts Worker's GATEWAY_LOOP_RED_SECONDS
 * (workers/fleet-alerts/wrangler.toml) -- a documented contract across two
 * packages, like WORK_OVERDUE_RED_SECONDS. It must stay BELOW the seat
 * supervisor's kill point (~270s at defaults) so the dot goes red before the
 * seat restarts itself.
 */
export const GATEWAY_LOOP_RED_SECONDS = 120

/**
 * Sanitized server names whose failure run crosses an ADR 0080 open path
 * (conn-class or backstop — same predicates the fleet-alerts Worker pages
 * on, via the shared threshold constants). Defensive parse: junk JSON or a
 * junk entry contributes nothing (NULL participates in nothing).
 */
function connectorEntryFailing(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) return false
  const entry = value as Record<string, unknown>
  const count = typeof entry.consecutive_failures === 'number' ? entry.consecutive_failures : 0
  if (count <= 0) return false
  const runAge = typeof entry.run_age_seconds === 'number' ? entry.run_age_seconds : 0
  const connPath =
    count >= CONNECTOR_DOWN_MIN_FAILURES &&
    entry.conn_evidence === true &&
    runAge >= CONNECTOR_DOWN_RUN_AGE_SECONDS
  const backstop =
    count >= CONNECTOR_BACKSTOP_MIN_FAILURES && runAge >= CONNECTOR_BACKSTOP_RUN_AGE_SECONDS
  return connPath || backstop
}

/**
 * Build the rosterHealth signal bundle from a fleet_status row (or its
 * absence). Centralized so the .astro call sites stay under the complexity
 * ceiling and the two pages can never drift on which columns feed the dot.
 */
export function seatSignals(
  fleet: {
    scheduler_ok: number | null
    scheduler_max_overdue_seconds: number | null
    connector_check_ok: number | null
    connectors_json: string | null
    cron_containment?: number | null
    gateway_loop_ok?: number | null
    gateway_loop_age_seconds?: number | null
    gateway_supervisor_state?: string | null
    sticky_stop_condition?: string | null
  } | null
): SchedulerSignal {
  // One null-row branch up front, then plain reads: every `?? null` below
  // counted as its own branch against the complexity ceiling.
  if (fleet === null) {
    return { ok: null, maxOverdueSeconds: null }
  }
  return {
    ok: fleet.scheduler_ok,
    maxOverdueSeconds: fleet.scheduler_max_overdue_seconds,
    connectorCheckOk: fleet.connector_check_ok,
    connectorsJson: fleet.connectors_json,
    cronContainment: fleet.cron_containment ?? null,
    gatewayLoopOk: fleet.gateway_loop_ok ?? null,
    gatewayLoopAgeSeconds: fleet.gateway_loop_age_seconds ?? null,
    gatewaySupervisorState: fleet.gateway_supervisor_state ?? null,
    stickyStopCondition: fleet.sticky_stop_condition ?? null,
  }
}

export function failingConnectorNames(connectorsJson: string | null | undefined): string[] {
  if (!connectorsJson) return []
  let raw: unknown
  try {
    raw = JSON.parse(connectorsJson)
  } catch {
    return []
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>)
    .filter(([, value]) => connectorEntryFailing(value))
    .map(([server]) => server)
    .sort()
}

interface RosterNoteInputs {
  stickyStopLevel: string | null
  /**
   * Which meter tripped the ladder (migration 0112). Four drive it, so the
   * note must not assert one: "cost breaker hard stop" is what the roster said
   * on 2026-09-01 while ashton-price was stopped by a bad credential. Null on
   * a seat still running a pre-cause overlay, where the note says the level
   * and stops rather than guessing.
   */
  stickyStopCondition: string | null
  schedulerOk: number | null
  overdue: boolean
  summaryStatus: SummaryStatus | null
  connectorCheckOk: number | null
  failingConnectors: string[]
  cronContainment: number | null
  /** ss#2488 part 2: the loop is beating-stale past GATEWAY_LOOP_RED_SECONDS (ok=1 AND age present). */
  loopWedged: boolean
  /** ss#2488 part 2: the seat could not read its own loop heartbeat. */
  loopUnprovable: boolean
  /** ss#2488 part 2: the supervisor's word, or null. */
  supervisorState: string | null
  /**
   * ss#2295: true when this seat is reporting at all — a live heartbeat AND a
   * fleet_status row to have carried it. That is what makes an ABSENT
   * cron_containment meaningful rather than a by-product of whole-seat
   * silence. See `containmentUnreported`.
   */
  seatReporting: boolean
}

/**
 * ss#2295 — containment is three states, and the third one is not silence.
 *
 * `cron_containment` is 1 contained / 0 not contained / NULL unreported, and
 * the `=== 1` tests above are deliberately strict: NULL must never be resolved
 * into either verdict (the ss#2291 rule). But NULL was then rendered as
 * nothing at all, so a seat whose containment could not be read looked exactly
 * like a healthy uncontained one.
 *
 * What NULL means changed under the console's feet. Before the overlay fix for
 * ss#2291 (hermes-smd-overlay#252), a seat that could not read `/opt/data`
 * still reported a verdict, so NULL only ever meant "overlay build predating
 * ss#2276". After #252 the seat sends the field only when it actually read the
 * volume — and `/opt/data` is also where profile homes, cron stores, and
 * tokens live, so an absent field is rarely benign.
 *
 * Gated on the seat reporting at all, in two ways. When the heartbeat is gray
 * every field is NULL by construction, and naming containment there would
 * blame one field for a whole-seat silence while escalating gray to yellow — a
 * narrower fault than the one actually present. And with no SchedulerSignal
 * bundle there is no fleet_status row, so there is no evidence a beat was ever
 * recorded to have omitted the field. "Absent from a beat we received" is the
 * claim; without a beat, the claim has no subject.
 */
function containmentUnreported(inputs: RosterNoteInputs): boolean {
  return inputs.seatReporting && inputs.cronContainment === null
}

// Note precedence, most-actionable first: a hard breaker stop and a broken
// scheduler are both red and both need immediate hands; the breaker note wins
// when both fire (recovery is a Captain clear, not investigation). A failing
// connector ranks just below the scheduler (client-facing work failing on a
// live seat); a broken connector CHECK ranks with it (outages not being
// counted is itself an outage of the monitoring).
// ss#2488 part 2, the two tiers of gateway note. `urgent` is the pair that
// outranks everything else in rosterHealthNote, including the breaker: a wedged
// event loop means the Operator is not answering on ANY channel, and a refusing
// supervisor is that same outage plus the knowledge it will not self-heal. The
// `attention` pair ranks below the connector check: a seat whose self-recovery
// is silently absent, or that cannot read its own pulse, is not yet down.
function gatewayNote(inputs: RosterNoteInputs, tier: 'urgent' | 'attention'): string | null {
  if (tier === 'urgent') {
    if (inputs.loopWedged) return 'gateway loop wedged (Operator not answering)'
    if (inputs.supervisorState === 'refusing') {
      return 'seat supervisor stopped restarting (needs a human)'
    }
    return null
  }
  if (inputs.loopUnprovable) return 'gateway loop heartbeat unreadable'
  if (inputs.supervisorState === 'inert' || inputs.supervisorState === 'not-watching') {
    return 'seat supervisor cannot act (a wedge would not self-recover)'
  }
  return null
}

/**
 * The roster's stop note. Names the METER when the seat reported one and the
 * level alone when it did not -- never a meter the ladder did not report.
 */
function stopNote(level: string, condition: string | null): string {
  return condition ? `${level}: ${condition.replace(/_/g, ' ')}` : level
}

function rosterHealthNote(inputs: RosterNoteInputs): string | null {
  const urgent = gatewayNote(inputs, 'urgent')
  if (urgent) return urgent
  if (inputs.stickyStopLevel === 'HARD_STOP')
    return stopNote('hard stop', inputs.stickyStopCondition)
  if (inputs.schedulerOk === 0) return 'cron scheduler broken'
  if (inputs.failingConnectors.length > 0) {
    return `connector failing: ${inputs.failingConnectors.join(', ')}`
  }
  if (inputs.connectorCheckOk === 0) return 'connector health check broken'
  const attention = gatewayNote(inputs, 'attention')
  if (attention) return attention
  if (inputs.stickyStopLevel === 'SOFT_STOP')
    return stopNote('soft stop', inputs.stickyStopCondition)
  // ss#2276: a deliberate state, not a fault - but it must be SAID, because it
  // also explains a zero job count and suppressed routines. Sits above
  // 'overdue' so containment is named instead of read as lateness.
  if (inputs.cronContainment === 1) return 'crons contained (deliberate)'
  if (inputs.overdue) return 'scheduled work overdue'
  if (inputs.summaryStatus === 'red') return 'operator reports a problem'
  if (inputs.summaryStatus === 'yellow') return 'operator reports a warning'
  // ss#2295: last among the notes because it is an information GAP, not a
  // fault — anything else this roster can name is more actionable, and the
  // yellow dot fires either way. The wording states only what the console
  // observed (the field was absent from the beat); it does not promote that
  // into "unreadable volume" or "broken seat", neither of which is known here.
  if (containmentUnreported(inputs)) return 'containment state not reported'
  return null
}

// The full escalation set for one seat, from the shared note-input bundle.
function signalEscalations(inputs: RosterNoteInputs): (RosterHealthColor | null)[] {
  return [
    inputs.summaryStatus === 'red' ? 'red' : inputs.summaryStatus === 'yellow' ? 'yellow' : null,
    breakerColor(inputs.stickyStopLevel),
    inputs.schedulerOk === 0 ? 'red' : null,
    inputs.overdue ? 'yellow' : null,
    inputs.failingConnectors.length > 0 ? 'red' : null,
    inputs.connectorCheckOk === 0 ? 'red' : null,
    // ss#2488 part 2: a wedged loop or a supervisor that has given up is red
    // (the Operator is down and staying down); a check that cannot look, or a
    // supervisor that could never act, is attention-yellow -- a seat whose
    // self-recovery is silently absent must not render as calm.
    inputs.loopWedged ? 'red' : null,
    inputs.supervisorState === 'refusing' ? 'red' : null,
    inputs.loopUnprovable ? 'yellow' : null,
    inputs.supervisorState === 'inert' || inputs.supervisorState === 'not-watching'
      ? 'yellow'
      : null,
    // Containment paints attention-yellow: deliberate, but never invisible.
    inputs.cronContainment === 1 ? 'yellow' : null,
    // ss#2295: and UNKNOWN containment paints attention-yellow too, for the
    // same reason — a state the console cannot read is not a state it may
    // render as calm. Still escalate-only: it can never calm a red seat.
    containmentUnreported(inputs) ? 'yellow' : null,
  ]
}

// Escalate-only combine: return the most alarming of the base color and any
// escalations. NULL escalations are ignored; nothing here can CALM the base.
function escalatedColor(
  base: RosterHealthColor,
  escalations: (RosterHealthColor | null)[]
): RosterHealthColor {
  let color = base
  for (const e of escalations) {
    if (e && HEALTH_RANK[e] > HEALTH_RANK[color]) color = e
  }
  return color
}

// Map a two-level threshold string (breaker) to its escalation color.
function breakerColor(stickyStopLevel: string | null): RosterHealthColor | null {
  if (stickyStopLevel === 'HARD_STOP') return 'red'
  if (stickyStopLevel === 'SOFT_STOP') return 'yellow'
  return null
}

// ss#2488 part 2. Both fields are required for a wedge verdict: ok=1 with a
// NULL age is the seat's arming latch or boot suppression, not a beat, and
// `null > N` is false in JS, which would otherwise read as "not wedged".
// `!= null` (loose) so an absent column holds exactly like NULL.
function gatewayInputs(
  scheduler: SchedulerSignal | null
): Pick<RosterNoteInputs, 'loopWedged' | 'loopUnprovable' | 'supervisorState'> {
  const ok = scheduler?.gatewayLoopOk ?? null
  const age = scheduler?.gatewayLoopAgeSeconds ?? null
  return {
    loopWedged: ok === 1 && age != null && age > GATEWAY_LOOP_RED_SECONDS,
    loopUnprovable: ok === 0,
    supervisorState: scheduler?.gatewaySupervisorState ?? null,
  }
}

export function rosterHealth(
  heartbeatColor: RosterHealthColor,
  heartbeatLabel: string,
  summaryStatus: SummaryStatus | null,
  stickyStopLevel: string | null = null,
  scheduler: SchedulerSignal | null = null
): RosterHealth {
  // Every escalation input is escalate-only: the cost breaker (ADR 0062), the
  // runtime summary rollup, the scheduler self-check (WP-2), and the
  // connector health signals (ADR 0080) can each raise the dot but never calm
  // it. NULL participates in nothing — an unreported signal never paints a
  // dot and never overrides an existing worse color.
  const inputs: RosterNoteInputs = {
    stickyStopLevel,
    stickyStopCondition: scheduler?.stickyStopCondition ?? null,
    schedulerOk: scheduler?.ok ?? null,
    overdue:
      scheduler?.maxOverdueSeconds != null &&
      scheduler.maxOverdueSeconds > WORK_OVERDUE_RED_SECONDS,
    summaryStatus,
    connectorCheckOk: scheduler?.connectorCheckOk ?? null,
    failingConnectors: failingConnectorNames(scheduler?.connectorsJson),
    cronContainment: scheduler?.cronContainment ?? null,
    ...gatewayInputs(scheduler),
    seatReporting: heartbeatColor !== 'gray' && scheduler !== null,
  }
  const color = escalatedColor(heartbeatColor, signalEscalations(inputs))
  return { color, label: heartbeatLabel, note: rosterHealthNote(inputs) }
}

export function rosterHealthDotClass(color: RosterHealthColor): string {
  switch (color) {
    case 'green':
      return 'bg-[color:var(--ss-color-complete)]'
    case 'yellow':
      return 'bg-[color:var(--ss-color-attention)]'
    case 'red':
      return 'bg-[color:var(--ss-color-error)]'
    case 'gray':
      return 'bg-[color:var(--ss-color-border)]'
  }
}

/** "3 personas" / "1 persona" / "no personas". Pure, for the roster cell. */
export function personaSummary(personas: RosterPersona[]): string {
  const active = personas.filter((p) => p.status === 'active')
  const count = personas.length
  if (count === 0) return 'no personas'
  const noun = count === 1 ? 'persona' : 'personas'
  const archived = count - active.length
  return archived > 0 ? `${count} ${noun} (${archived} archived)` : `${count} ${noun}`
}
