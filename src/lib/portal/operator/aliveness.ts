/**
 * Operator — dashboard aliveness signal (issue #875).
 *
 * Customers asked "where is the agent right now?" The aliveness signal
 * is the persistent header chip that answers that question without
 * making them open the audit viewer. Four states cover every observable
 * posture of a customer's Hermes Machine:
 *
 *   idle        — the Machine is up; the last audit event is recent and
 *                 nothing is currently running. Healthy default.
 *   running     — there is an in-flight skill invocation. The signal
 *                 carries the skill name so the header reads "Running
 *                 inbox-triage" instead of a generic "Working".
 *   sticky_stop — the safety substrate has pinned the agent: HARD_STOP in
 *                 `sticky_stop_state`. Captain escalation is required to
 *                 clear (recovery contract in
 *                 `operator/safety-substrate/sticky_stop.py`). WARN and
 *                 SOFT_STOP used to reach this signal too, which showed a
 *                 CLIENT a constrained agent on a seat nothing was
 *                 constraining; both were removed 2026-09-02.
 *   offline     — no audit_log activity within OFFLINE_THRESHOLD_MINUTES.
 *                 This is a derived posture: the audit writer is
 *                 synchronous on every action (issue #891), so absence
 *                 of audit rows past the freshness window means either
 *                 the Machine is down, the bridge is broken, or the
 *                 customer's agent is genuinely doing nothing.
 *
 * Data source (#1678, superseding the #821 bridge stub): this customer's
 * `fleet_status` row — the console-side store each Machine's heartbeat
 * emitter pushes to (ADR 0023 Wave 1). It carries `last_heartbeat_ts`,
 * `last_audit_ts`, and the Machine-reported `sticky_stop_level`, which is
 * everything the four-state signal needs:
 *
 *   - heartbeat + audit timestamps drive the idle/offline split (freshest
 *     wins — a quiet-but-healthy Machine heartbeats without acting);
 *   - `sticky_stop_level` drives the sticky_stop posture. The reason text IS
 *     pushed on the heartbeat as of migration 0112 / overlay#341 and IS
 *     stored, but it stays deliberately OFF this surface: the seat writes
 *     operational jargon naming internal skills and MCP tools
 *     ("consecutive_tool_failures=8 (window=600s, skill=mcp_x)"), which is
 *     admin diagnostics, not client-facing copy. The chip shows the posture
 *     without it BY CHOICE, not for want of data — do not read the null
 *     below as a gap to close;
 *   - no in-flight marker is pushed today, so 'running' never renders
 *     from this source — we do not infer it from timestamps.
 *
 * A customer with no `fleet_status` row resolves to null and the
 * AlivenessHeader renders the empty-state branch (no fabricated
 * activity, per `docs/style/empty-state-pattern.md`).
 *
 * The derivation helper `deriveAlivenessFromBridge` is exported pure
 * and tested directly. The component does not derive from raw inputs;
 * it consumes the resolved signal.
 *
 * Per-customer: the resolver takes a `SubscriptionRow` and the bridge
 * routes to the matching customer Machine. Aggregate is explicitly out
 * of scope — the issue AC is "per-customer status, not aggregate."
 */

import type { D1Database } from '@cloudflare/workers-types'
import type { SubscriptionRow } from '../product-access'

/**
 * Closed vocabulary for the dashboard aliveness signal. The page must
 * render exactly one of these states; unknown / pending data degrades
 * to null (empty-state) rather than fabricating a posture.
 */
export type AlivenessLevel = 'idle' | 'running' | 'sticky_stop' | 'offline'

/**
 * @public Closed vocabulary. tests/portal-operator-aliveness.test.ts imports it and pins the set.
 * No runtime caller, by design.
 */
export const ALIVENESS_LEVELS: readonly AlivenessLevel[] = [
  'idle',
  'running',
  'sticky_stop',
  'offline',
] as const

/**
 * Tone for the aliveness chip, drawn from the portal `Tone` vocabulary
 * in `src/lib/portal/status.ts`. Returned as a string here to avoid
 * importing the full tone module at the resolver layer — the component
 * is the only consumer that needs the typed value, and it imports both
 * sides.
 *
 * Assignment rationale:
 *   idle        → success — the Machine is healthy and reachable
 *   running     → info    — actively working; reviewer-noticeable but
 *                           not actionable
 *   sticky_stop → danger  — Captain escalation required
 *   offline     → warning — degraded; may or may not need attention
 *                           depending on hours-of-operation; reviewers
 *                           should look at the last-action timestamp
 *
 * @public Consumed by src/components/portal/operator/AlivenessHeader.astro, a component knip
 * reports unused. Retiring that component is a product decision, not this gate's.
 */
export function alivenessTone(level: AlivenessLevel): 'success' | 'info' | 'danger' | 'warning' {
  switch (level) {
    case 'idle':
      return 'success'
    case 'running':
      return 'info'
    case 'sticky_stop':
      return 'danger'
    case 'offline':
      return 'warning'
  }
}

/**
 * Number of minutes of audit-log silence after which a Machine is
 * considered "offline." The audit writer is synchronous on every
 * safety-relevant action; gaps in the log past this window indicate
 * either a downed Machine, a broken bridge, or genuine inactivity.
 *
 * 30 minutes is the default because the Operator is an
 * assistant-class agent expected to be active during the firm's
 * business day; a half-hour gap during work hours is the right amount
 * of "something is probably off, take a look." Reviewers can still
 * widen the audit page's date range to see longer-tail history.
 *
 * Tracked as an exported constant so the PR body, tests, and any future
 * customer-yaml override (out of scope today) share one source of
 * truth.
 */
export const OFFLINE_THRESHOLD_MINUTES = 30

/**
 * One resolved aliveness signal for the dashboard header. The component
 * renders exactly the fields present here — no field-presence inference,
 * no derived friendly labels beyond what the formatters below produce.
 *
 *   level              — the four-state enum above.
 *   lastActionAt       — ISO 8601 UTC of the most recent audit_log row.
 *                        null only when the customer has no audit
 *                        history at all (first-day case).
 *   currentSkill       — when `level === 'running'`, the slug of the
 *                        in-flight skill. null in every other state.
 *   stickyStopReason   — when `level === 'sticky_stop'`, the substrate's
 *                        recorded reason string. The Captain-escalation
 *                        affordance surfaces this verbatim. null in
 *                        every other state.
 */
export interface AlivenessSignal {
  level: AlivenessLevel
  lastActionAt: string | null
  currentSkill: string | null
  stickyStopReason: string | null
}

/**
 * Minimal shape of the data the Hermes bridge will return per request.
 * Captured here so `deriveAlivenessFromBridge` has a stable, named
 * contract independent of the audit_log row schema (which carries many
 * fields the dashboard does not use). Keeps test fixtures readable.
 */
export interface AlivenessBridgeReading {
  /** ISO 8601 UTC of the most recent audit_log row, null if none. */
  lastAuditTs: string | null
  /**
   * ISO 8601 UTC of the most recent Machine heartbeat (ADR 0023 Wave 1
   * emitter → console `fleet_status`), null if the Machine has never
   * reported one. A quiet-but-healthy Machine writes heartbeats without
   * writing audit rows, so liveness (idle vs offline) is decided by the
   * FRESHEST of heartbeat/audit — while `lastActionAt` display stays the
   * audit timestamp (the last thing the operator *did*, not the last time
   * it phoned home).
   */
  lastHeartbeatTs: string | null
  /**
   * Slug of an in-flight skill at the moment the bridge was polled,
   * null if nothing is running. The bridge is responsible for the
   * "in-flight" determination (e.g., a started-but-not-completed
   * marker in the per-customer runtime); this module does not infer
   * it from the audit row.
   */
  inFlightSkill: string | null
  /**
   * The forward-only sticky-stop level. 'OK' means not pinned;
   * anything else means the agent is constrained. The level vocabulary
   * mirrors `operator/safety-substrate/sticky_stop.py::StickyStopLevel`.
   * Unknown values surface as 'OK' rather than collapsing to
   * sticky_stop — under-reporting is preferable to false-positive
   * "agent is stopped" copy.
   */
  stickyStopLevel: 'OK' | 'HARD_STOP'
  /**
   * Human-readable reason text the substrate stored when it pinned the
   * stop. null when `stickyStopLevel === 'OK'`.
   */
  stickyStopReason: string | null
}

/**
 * Pure derivation: bridge reading → AlivenessSignal. The four-state
 * enum collapses from three inputs in priority order:
 *
 *   1. sticky_stop wins over everything else — when the substrate has
 *      pinned the agent, no other posture is interesting until Captain
 *      clears it.
 *   2. an in-flight skill → 'running'.
 *   3. the FRESHEST parseable timestamp among lastHeartbeatTs /
 *      lastAuditTs, compared against OFFLINE_THRESHOLD_MINUTES, decides
 *      idle vs offline. Heartbeats count because a quiet-but-healthy
 *      Machine phones home without writing audit rows; audit rows count
 *      because a pre-heartbeat Machine (or a row that predates the
 *      emitter) is still evidence of life. No parseable timestamp at all
 *      is treated as 'offline' — absence is the honest answer.
 *
 * `lastActionAt` always carries the audit timestamp (the last thing the
 * operator *did*), regardless of which timestamp decided liveness.
 *
 * `nowMs` is injectable for deterministic tests.
 */
export function deriveAlivenessFromBridge(
  reading: AlivenessBridgeReading,
  nowMs: number = Date.now()
): AlivenessSignal {
  // Positive match on HARD_STOP, not `!== 'OK'`. The negative form made ANY
  // unrecognised word mean "the agent is stopped" to a client -- the exact
  // opposite of this type's own promise a few lines up ("Unknown values
  // surface as 'OK' ... under-reporting is preferable to false-positive
  // 'agent is stopped' copy"). It was safe only because a boundary two
  // hundred lines away happened to filter first; after the 2026-09-02 two-
  // state collapse a legacy WARN / SOFT_STOP would have walked straight
  // through it and told a client a healthy seat was pinned.
  if (reading.stickyStopLevel === 'HARD_STOP') {
    return {
      level: 'sticky_stop',
      lastActionAt: reading.lastAuditTs,
      currentSkill: null,
      stickyStopReason: reading.stickyStopReason,
    }
  }

  if (reading.inFlightSkill !== null && reading.inFlightSkill.length > 0) {
    return {
      level: 'running',
      lastActionAt: reading.lastAuditTs,
      currentSkill: reading.inFlightSkill,
      stickyStopReason: null,
    }
  }

  const lastAliveMs = freshestMs(reading.lastHeartbeatTs, reading.lastAuditTs)
  if (lastAliveMs === null) {
    return {
      level: 'offline',
      lastActionAt: reading.lastAuditTs,
      currentSkill: null,
      stickyStopReason: null,
    }
  }

  const ageMs = nowMs - lastAliveMs
  const thresholdMs = OFFLINE_THRESHOLD_MINUTES * 60_000
  if (ageMs > thresholdMs) {
    return {
      level: 'offline',
      lastActionAt: reading.lastAuditTs,
      currentSkill: null,
      stickyStopReason: null,
    }
  }

  return {
    level: 'idle',
    lastActionAt: reading.lastAuditTs,
    currentSkill: null,
    stickyStopReason: null,
  }
}

/** The freshest parseable timestamp among the inputs, as epoch ms; null when
 * none parses. Unparseable values are skipped, not treated as epoch 0. */
function freshestMs(...timestamps: (string | null)[]): number | null {
  let freshest: number | null = null
  for (const ts of timestamps) {
    if (ts === null) continue
    const ms = Date.parse(ts)
    if (!Number.isFinite(ms)) continue
    if (freshest === null || ms > freshest) freshest = ms
  }
  return freshest
}

/**
 * Friendly label for an AlivenessLevel. The component pairs this with
 * the chip tone; the label is the headline text in the header band.
 *
 * Closed vocabulary; the switch is exhaustive so any new level surfaces
 * at compile time.
 *
 * @public Consumed by src/components/portal/operator/AlivenessHeader.astro, a component knip
 * reports unused. Retiring that component is a product decision, not this gate's.
 */
export function formatAlivenessLevel(level: AlivenessLevel): string {
  switch (level) {
    case 'idle':
      return 'Idle'
    case 'running':
      return 'Running'
    case 'sticky_stop':
      return 'Paused by safety check'
    case 'offline':
      return 'Offline'
  }
}

/**
 * Friendly relative-time label for the last-action timestamp.
 *
 * Returns "just now" for ages under a minute, "N minute(s) ago" up to
 * an hour, "N hour(s) ago" up to a day, "N day(s) ago" beyond that.
 * The component pairs this with the absolute ISO value in a tooltip /
 * `title` attribute so reviewers can hover for the exact instant.
 *
 * Returns null when the input is null or unparseable — the component
 * renders the absolute fallback (`ts` verbatim or omits the line)
 * rather than fabricating "unknown ago" copy.
 *
 * `nowMs` is injectable for deterministic tests.
 */
export function formatLastActionRelative(
  ts: string | null,
  nowMs: number = Date.now()
): string | null {
  if (ts === null) return null
  const tsMs = Date.parse(ts)
  if (!Number.isFinite(tsMs)) return null
  const deltaMs = nowMs - tsMs
  if (deltaMs < 0) return 'just now'
  const seconds = Math.floor(deltaMs / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}

/**
 * Format the absolute last-action timestamp for the chip's `title`
 * attribute and the visible secondary line. Same UTC + locale-stable
 * shape the audit page uses (`src/lib/portal/operator/audit.ts ::
 * formatAuditTimestamp`) so the two surfaces agree at a glance.
 *
 * Returns the input verbatim when unparseable.
 */
export function formatLastActionAbsolute(ts: string | null): string | null {
  if (ts === null) return null
  const parsedMs = Date.parse(ts)
  if (!Number.isFinite(parsedMs)) return ts
  const dt = new Date(parsedMs)
  const fmt = new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'UTC',
    timeZoneName: 'short',
  })
  return fmt.format(dt)
}

/**
 * Whether the current signal warrants the Captain-escalation
 * affordance. True for `sticky_stop` (the agent is pinned and only
 * Captain can clear) and `offline` (the Machine is not reporting
 * activity and may need investigation). The component renders the
 * affordance only when this is true.
 *
 * `idle` and `running` are healthy postures; surfacing an escalation
 * link there would train customers to ignore it.
 */
export function needsEscalationAffordance(level: AlivenessLevel): boolean {
  return level === 'sticky_stop' || level === 'offline'
}

/**
 * Server-side resolver invoked by the dashboard header. Reads this SEAT's
 * `fleet_status` row — the console-side heartbeat store each Machine pushes
 * to (ADR 0023 Wave 1) — and derives the signal from it (#1678 wired this;
 * the prior revision was the #821 stub). The row is addressed by the
 * subscription's `instance_slug`, not by entity: several seats share one
 * entity (#2281).
 *
 * Source semantics:
 *   - `sticky_stop_level` is the Machine-reported breaker ladder value;
 *     anything outside the known non-OK set reads as 'OK' (under-reporting
 *     beats a false "agent is stopped" chip). The reason text is not pushed
 *     on the heartbeat, so `stickyStopReason` is null here — the chip shows
 *     the posture; the reason lives with Captain.
 *   - No in-flight-skill marker is pushed today, so 'running' never renders
 *     from this source (honest: we do not infer it from timestamps).
 *   - `last_heartbeat_ts` + `last_audit_ts` drive the idle/offline split in
 *     the derivation above.
 *
 * IMPORTANT: no `fleet_status` row → null (chip renders nothing). Per
 * `docs/style/empty-state-pattern.md`, the component must render the
 * empty (silent) state until real data lands, never a fabricated
 * "agent is healthy" chip. A green chip the customer cannot trust is
 * worse than no chip at all. A read failure degrades the same way.
 */
export async function resolveAlivenessSignal(
  db: D1Database,
  subscription: SubscriptionRow,
  nowMs: number = Date.now()
): Promise<AlivenessSignal | null> {
  const reading = await fetchAlivenessFromFleetStatus(db, subscription)
  if (reading === null) return null
  return deriveAlivenessFromBridge(reading, nowMs)
}

/**
 * Non-OK sticky-stop ladder values the Machine can report (mirrors
 * `operator/safety-substrate/sticky_stop.py::StickyStopLevel`).
 *
 * HARD_STOP only since the 2026-09-02 two-state collapse. WARN and SOFT_STOP
 * used to be in this set, which meant a CLIENT was shown a "constrained" chip
 * on a seat that was not constrained by anything -- those rungs restricted no
 * call and refused no send. A legacy seat still reporting them now reads OK,
 * which is what they always meant.
 */
const NON_OK_STICKY_LEVELS: ReadonlySet<string> = new Set(['HARD_STOP'])

interface FleetStatusAlivenessRow {
  last_heartbeat_ts: string | null
  last_audit_ts: string | null
  sticky_stop_level: string | null
}

/**
 * Read exactly this SEAT's heartbeat row (never a fleet-wide read from the
 * portal — ADR 0052 scopes this surface to the client's own operator).
 *
 * Keyed on `customer_slug`, which is `fleet_status`'s primary key since
 * migration 0093. It must not be keyed on `entity_id`: the multi-operator
 * model puts several seats on ONE entity, so `entity_id` is a plain
 * non-unique index and an entity-keyed `.first()` returns an arbitrary
 * sibling seat's heartbeat (#2281 — one live entity carries four rows).
 * The sibling module `pause-control.ts::readPausePosture` keys the same
 * table the same way.
 *
 * The seat identity is `subscription.instance_slug` (= the instance's
 * `customer_slug`, migration 0089). A subscription with no instance slug
 * carries no seat identity, so it resolves to the silent empty state rather
 * than falling back to the entity read — a borrowed heartbeat is exactly the
 * fabrication `docs/style/empty-state-pattern.md` forbids.
 */
async function fetchAlivenessFromFleetStatus(
  db: D1Database,
  subscription: SubscriptionRow
): Promise<AlivenessBridgeReading | null> {
  const customerSlug = subscription.instance_slug
  if (!customerSlug) return null

  let row: FleetStatusAlivenessRow | null
  try {
    row = await db
      .prepare(
        'SELECT last_heartbeat_ts, last_audit_ts, sticky_stop_level ' +
          'FROM fleet_status WHERE customer_slug = ?'
      )
      .bind(customerSlug)
      .first<FleetStatusAlivenessRow>()
  } catch {
    // A missing table (fresh environment) degrades to the silent empty state.
    return null
  }
  if (!row) return null

  const stickyStopLevel =
    typeof row.sticky_stop_level === 'string' && NON_OK_STICKY_LEVELS.has(row.sticky_stop_level)
      ? (row.sticky_stop_level as 'HARD_STOP')
      : 'OK'

  return {
    lastAuditTs: row.last_audit_ts,
    lastHeartbeatTs: row.last_heartbeat_ts,
    inFlightSkill: null,
    stickyStopLevel,
    // Deliberately null even though fleet_status now HAS the reason: it is
    // admin diagnostics naming internal skills and tools, and this object
    // renders to a client. See the note at the top of this file.
    stickyStopReason: null,
  }
}
