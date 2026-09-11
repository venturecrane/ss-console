/**
 * Fleet Alerts Worker — heartbeat-red / HARD_STOP / scheduler pager (#1709).
 *
 * Closes the gap ADR 0064's honesty banner named: detection was
 * dashboard-only. Every 2 minutes (cron) this Worker reads the central
 * `fleet_status` table each Machine's heartbeat emitter pushes to (ADR 0023)
 * and emails team@smd.services on condition TRANSITIONS:
 *
 *   heartbeat_red   — the seat HAS reported before, and its last heartbeat is
 *                     older than HEARTBEAT_RED_SECONDS (default 300s = the
 *                     period+grace envelope). Seats with a NULL heartbeat or
 *                     no row at all are provisioning-gray, never alerted —
 *                     a false page trains people to ignore the pager.
 *   hard_stop       — the Machine-reported cost-breaker ladder (ADR 0062) is
 *                     at HARD_STOP.
 *   scheduler_error — the gate's per-beat scheduler self-check reports the
 *                     cron store is unreadable or a job is in an error state
 *                     (scheduler_ok=0). This is the 8-day-silent-death class:
 *                     the gate lives (heartbeats green) but scheduled work
 *                     cannot fire.
 *   work_overdue    — the cron store is readable but a job is past its
 *                     next_run_at by more than WORK_OVERDUE_RED_SECONDS
 *                     (default 900s).
 *   connector_down:<server> — per-MCP-server outage (ADR 0080 / ss#1990): the
 *                     seat's connector ledger reports a sustained consecutive-
 *                     failure run for one server. Two open paths: the fast
 *                     conn-class path (>=3 consecutive with connection-class
 *                     evidence, run age >= CONNECTOR_DOWN_RUN_AGE_SECONDS)
 *                     and the signature-free backstop (>=10 consecutive, run
 *                     age >= 900s — a connector failing 10 straight with no
 *                     success is broken regardless of what its errors look
 *                     like, which keeps paging genuinely connector-generic).
 *                     Resolves ONLY on a proven success (count back to 0);
 *                     ambiguous counts (1-2) push no state at all. All ages
 *                     are stamped WRITER-side on the seat, so this Worker
 *                     only ever evaluates stored values — a frozen row from
 *                     a dead seat can never self-activate a connector page.
 *   connector_check_error — the seat's connector self-check ITSELF is broken
 *                     (ledger unreadable / tool→server mapping gone):
 *                     nothing is being counted, which must page rather than
 *                     silently disabling the whole connector alert class.
 *
 * Edge-triggered via `fleet_alert_state` (migrations 0086/0093): one open alert
 * per (customer, condition) until recovery, one recovery notice on the green
 * transition, silence otherwise. No alert storm by construction.
 *
 * It ALSO delivers the shared alert sink. `cost_anomaly_alerts` rows written by
 * the Sentry and healthchecks webhook receivers (source != 'cost') were
 * dashboard-only — visible to whoever opened the console, and to nobody
 * otherwise. `notifySinkAlerts` emails each undelivered row once and stamps
 * `notified_at` (migration 0095) only on a successful send. Those rows are
 * event-shaped, not condition-shaped: a Sentry issue has no green state to
 * transition back to, so they never enter `fleet_alert_state`.
 *
 * This Worker only OBSERVES and EMAILS. It never touches a Machine — the
 * response ladder is human doctrine (ADR 0064/0065).
 *
 * The fetch handler exposes a bearer-gated POST /run (same pattern as the
 * cost-anomaly Worker) so the evaluation can be driven on demand for live
 * verification, plus GET /health.
 */

import { CONNECTOR_DOWN_PREFIX } from './conditions'
import { escapeHtml } from './html'
import { notifySinkAlerts, type SinkNotification } from './sink-notify'
import { notifySendRefusals, type SendRefusedNotification } from './send-refused'
import { getOpenSpecControlKeys, specControlConditions } from './spec-control'
import { getStaleHolds } from './stale-holds'
import { tokenExpiryConditions } from './token-expiry'
import { getOpenWebhookSurfaceKeys, webhookSurfaceConditions } from './webhook-surface'
import { gatewayLoopConditions, gatewayLoopRedSeconds } from './gateway-loop'
import { conditionLabel, hardStopDetail } from './conditions'
import { listFleetStatus, type FleetStatusRow } from './fleet-status'
export { listFleetStatus, type FleetStatusRow } from './fleet-status'
export { conditionLabel, hardStopDetail } from './conditions'

export type { SinkNotification, SendRefusedNotification }

export interface Env {
  DB: D1Database
  RESEND_API_KEY?: string
  ALERT_FROM_EMAIL?: string
  ALERT_TO_EMAIL?: string
  HEARTBEAT_RED_SECONDS?: string
  /**
   * Seconds a job may be past its next_run_at before work_overdue fires.
   * Default 900. MUST match the admin roster's WORK_OVERDUE_RED_SECONDS
   * (src/lib/admin/fleet-status.ts) — the two live in separate wrangler/app
   * packages, so the shared literal is a documented contract, not an import.
   */
  WORK_OVERDUE_RED_SECONDS?: string
  /**
   * Seconds the gateway loop heartbeat may be stale before gateway_loop_wedged
   * fires (ss#2488 part 2). Default 120, floor 60. MUST stay BELOW the seat's
   * kill point -- SMD_GATEWAY_LIVENESS_STALE_SECONDS x 2 samples + the dump and
   * TERM graces, ~270s at defaults (operator/templates/entrypoint.sh) -- or the
   * page lands after the restart it was meant to precede. A documented
   * cross-repo contract, not an import, like WORK_OVERDUE_RED_SECONDS.
   */
  GATEWAY_LOOP_RED_SECONDS?: string
  /**
   * Minimum writer-side run age (seconds) before the conn-class connector_down
   * path fires. Default 300 — a failure burst that self-heals inside Hermes'
   * 60s breaker cooldown never reaches an inbox. MUST match the admin roster's
   * CONNECTOR_DOWN_RUN_AGE_SECONDS (src/lib/admin/fleet-status.ts) — separate
   * packages, documented contract, not an import.
   */
  CONNECTOR_DOWN_RUN_AGE_SECONDS?: string
  /**
   * The CURRENT vendor refresh-token lifetime for the Smokeball connector, in
   * days (ss#2148; Smokeball auth docs: 180 since their 2026-08-24 cutover,
   * 30 before it). This is the lifetime for a token issued TODAY — tokens
   * minted before the cutover still die at 30, and ./token-expiry.ts dates each
   * token from its reported age to pick the right one. Do not fold that back
   * into this single value; on 2026-09-02 both regimes were live at once.
   * The connector_token_expiring condition opens when the seat-reported
   * token-file age reaches (lifetime - TOKEN_EXPIRY_WARN_DAYS). Unset/invalid
   * disables the condition for smokeball rather than guessing a lifetime.
   */
  SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS?: string
  /** Days of warning before the recorded lifetime. Default 5. */
  TOKEN_EXPIRY_WARN_DAYS?: string
  FLEET_ALERTS_BEARER?: string
  ADMIN_BASE_URL?: string
  /**
   * healthchecks.io ping URL for the alerter itself (secret, optional). Pinged
   * at the end of every successful runOnce — if this Worker's cron dies or
   * runOnce throws, healthchecks pages independently. The one piece that makes
   * "unable to die silently" literally true for the watcher.
   */
  ALERTER_HEALTHCHECKS_PING_URL?: string
}

export type FleetCondition =
  | 'heartbeat_red'
  | 'hard_stop'
  | 'scheduler_error'
  | 'work_overdue'
  | 'connector_check_error'
  | 'spec_control_unprovable'
  | 'webhook_surface_unprovable'
  | 'gateway_loop_wedged'
  | 'gateway_loop_unprovable'
  | 'gateway_restarted'
  | 'gateway_supervisor_refusing'
  | 'gateway_supervisor_inert'
  // ss#2547. The one EVENT-shaped member of this union: it never goes `open`
  // and never resolves, it only carries a marker. See ./send-refused.
  | 'send_refused'
  | `connector_down:${string}`
  | `connector_token_expiring:${string}`
  | `spec_control_broken:${string}`
  | `webhook_surface_missing:${string}`

/** One per-server entry from the seat's connectors map (writer-side ages). */
export interface ConnectorEntry {
  consecutive_failures: number
  run_age_seconds?: number
  conn_evidence?: boolean
  last_ok_age_seconds?: number
  last_error_age_seconds?: number
  last_error_message?: string
}

export interface ConditionState {
  customer_slug: string
  condition: FleetCondition
  active: boolean
  /** Human detail for the email body (age, level). */
  detail: string
}

export interface Transition {
  customer_slug: string
  condition: FleetCondition
  kind: 'opened' | 'resolved'
  detail: string
  emailed: boolean
  resendId?: string
}

/**
 * An open alert row whose seat currently reports NULL for that condition's
 * source field (or has no fleet_status row at all — the orphaned pilot-smokeball
 * case from the 16-day alerter wedge). The NULL-hold means these will never
 * auto-resolve; they are surfaced for a documented one-line manual UPDATE.
 */
export interface StaleHold {
  customer_slug: string
  condition: FleetCondition
}

export interface RunSummary {
  at: string
  seats: number
  conditions: ConditionState[]
  transitions: Transition[]
  stale_holds: StaleHold[]
  sink_notifications: SinkNotification[]
  send_refusals: SendRefusedNotification[]
}

const DEFAULT_RED_SECONDS = 300
const DEFAULT_WORK_OVERDUE_SECONDS = 900
// ADR 0080 connector_down thresholds. The conn-class path aligns with Hermes'
// own circuit breaker (opens at 3 consecutive) so agent-visible and
// ops-visible "failing" coincide; the age gate keeps self-healing bursts out
// of the inbox. The signature-free backstop pages ANY sustained run — a
// connector failing 10 straight with zero successes for 15 minutes is broken
// no matter what its error text looks like.
const DEFAULT_CONNECTOR_RUN_AGE_SECONDS = 300
const CONNECTOR_DOWN_MIN_FAILURES = 3
const CONNECTOR_BACKSTOP_MIN_FAILURES = 10
const CONNECTOR_BACKSTOP_RUN_AGE_SECONDS = 900
// ss#2148 pre-expiry warning window (days before the recorded lifetime).
const DEFAULT_TOKEN_EXPIRY_WARN_DAYS = 5

function redSeconds(env: Env): number {
  const n = Number(env.HEARTBEAT_RED_SECONDS)
  return Number.isFinite(n) && n >= 60 ? Math.floor(n) : DEFAULT_RED_SECONDS
}

function workOverdueSeconds(env: Env): number {
  const n = Number(env.WORK_OVERDUE_RED_SECONDS)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : DEFAULT_WORK_OVERDUE_SECONDS
}

function connectorRunAgeSeconds(env: Env): number {
  const n = Number(env.CONNECTOR_DOWN_RUN_AGE_SECONDS)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : DEFAULT_CONNECTOR_RUN_AGE_SECONDS
}

/**
 * Recorded credential lifetimes per server (ss#2148). Only servers with a
 * valid recorded lifetime are evaluated — no entry, no guess, no page.
 */
function tokenLifetimesDays(env: Env): Record<string, number> {
  const out: Record<string, number> = {}
  const sb = Number(env.SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS)
  if (Number.isFinite(sb) && sb > 0) out.smokeball = Math.floor(sb)
  return out
}

function tokenWarnDays(env: Env): number {
  const n = Number(env.TOKEN_EXPIRY_WARN_DAYS)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : DEFAULT_TOKEN_EXPIRY_WARN_DAYS
}

/**
 * Parse a row's connectors_json defensively (fleet-view discipline: one
 * corrupt row degrades to null — a hold — and never aborts the fleet loop).
 * The ingest already validated entries; this re-validation is the Worker's
 * own trust boundary, not redundancy theater.
 */
function nonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

/** One entry, parsed-not-cast; null drops it (absence = hold for that server). */
function parseConnectorEntry(value: unknown): ConnectorEntry | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entry = value as Record<string, unknown>
  const count = nonNegInt(entry.consecutive_failures)
  if (count === null) return null
  const parsed: ConnectorEntry = { consecutive_failures: count }
  if (count > 0) {
    const runAge = nonNegInt(entry.run_age_seconds)
    if (runAge === null) return null
    parsed.run_age_seconds = runAge
    parsed.conn_evidence = entry.conn_evidence === true
  }
  const lastOk = nonNegInt(entry.last_ok_age_seconds)
  if (lastOk !== null) parsed.last_ok_age_seconds = lastOk
  const lastError = nonNegInt(entry.last_error_age_seconds)
  if (lastError !== null) parsed.last_error_age_seconds = lastError
  if (typeof entry.last_error_message === 'string') {
    parsed.last_error_message = entry.last_error_message.slice(0, 200)
  }
  return parsed
}

export function parseConnectorsMap(json: string | null): Record<string, ConnectorEntry> | null {
  if (json === null) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const out: Record<string, ConnectorEntry> = {}
  for (const [server, value] of Object.entries(raw as Record<string, unknown>)) {
    const parsed = parseConnectorEntry(value)
    if (parsed !== null) out[server] = parsed
  }
  return out
}

// connector_token_expiring machinery lives in ./token-expiry (ss#2148) —
// extracted whole so this file stays under the line ceiling and the condition
// keeps a single home.

/**
 * Pure condition evaluation over fleet_status rows. Exported for tests.
 *
 * A NULL heartbeat is provisioning-gray (never red): alert only on a seat that
 * HAS been alive and went quiet. The two scheduler conditions apply a per-field
 * NULL-hold: when the source field is NULL the ConditionState is NOT pushed at
 * all (not pushed as active:false), so a NULL can neither open an alert nor
 * resolve an open one. A false RECOVERED email to an ops inbox cancels human
 * urgency; never emit one from an unreported signal.
 */
export interface EvaluateOptions {
  overdueThresholdSeconds?: number
  connectorRunAgeThresholdSeconds?: number
  tokenLifetimesDays?: Record<string, number>
  tokenWarnDays?: number
  /**
   * customer_slug → the `<class>.<prop>` keys that currently have an OPEN
   * spec_control_broken alert (ss#2234). Needed because a declaration can be
   * WITHDRAWN: the key then vanishes from the seat's map entirely, and without
   * this the alert would strand open forever with no signal to close it. Every
   * other condition's source field goes NULL rather than disappearing, which is
   * why this is the first condition that needs it.
   */
  openSpecControlKeys?: Record<string, string[]>
  /**
   * customer_slug → the tool names that currently have an OPEN
   * webhook_surface_missing alert (ss#2287). Same need as openSpecControlKeys:
   * a tool can leave WEBHOOK_EXPECTED_TOOLS, at which point its key vanishes
   * from the seat's map and nothing would ever close the alert.
   */
  openWebhookSurfaceKeys?: Record<string, string[]>
  /** gateway_loop_wedged threshold (ss#2488 part 2). See Env.GATEWAY_LOOP_RED_SECONDS. */
  gatewayLoopRedSeconds?: number
}

export function evaluateConditions(
  rows: FleetStatusRow[],
  nowMs: number,
  redThresholdSeconds: number,
  options: EvaluateOptions = {}
): ConditionState[] {
  const {
    overdueThresholdSeconds = DEFAULT_WORK_OVERDUE_SECONDS,
    connectorRunAgeThresholdSeconds = DEFAULT_CONNECTOR_RUN_AGE_SECONDS,
    tokenLifetimesDays = {},
    tokenWarnDays = DEFAULT_TOKEN_EXPIRY_WARN_DAYS,
    openSpecControlKeys = {},
    openWebhookSurfaceKeys = {},
    gatewayLoopRedSeconds: loopRed = gatewayLoopRedSeconds(undefined),
  } = options
  const out: ConditionState[] = []
  for (const row of rows) {
    let hbActive = false
    let hbDetail = 'no heartbeat reported yet (provisioning-gray, not alertable)'
    if (row.last_heartbeat_ts !== null) {
      const ts = Date.parse(row.last_heartbeat_ts)
      if (Number.isFinite(ts)) {
        const ageSec = Math.floor((nowMs - ts) / 1000)
        hbActive = ageSec > redThresholdSeconds
        hbDetail = `last heartbeat ${ageSec}s ago (threshold ${redThresholdSeconds}s)`
      } else {
        // Unparseable timestamp from the store is a real fault, not gray.
        hbActive = true
        hbDetail = `unparseable last_heartbeat_ts: ${row.last_heartbeat_ts}`
      }
    }
    out.push({
      customer_slug: row.customer_slug,
      condition: 'heartbeat_red',
      active: hbActive,
      detail: hbDetail,
    })
    out.push({
      customer_slug: row.customer_slug,
      condition: 'hard_stop',
      active: row.sticky_stop_level === 'HARD_STOP',
      detail: hardStopDetail(row),
    })
    // scheduler_error — per-field NULL-hold: only evaluate when scheduler_ok
    // was actually reported this beat.
    if (row.scheduler_ok !== null) {
      out.push({
        customer_slug: row.customer_slug,
        condition: 'scheduler_error',
        active: row.scheduler_ok === 0,
        detail: `scheduler_ok=${row.scheduler_ok} (cron store unreadable or a job is in error state)`,
      })
    }
    // work_overdue — per-field NULL-hold, independent of scheduler_ok: only
    // evaluate when an overdue figure was reported (the overlay omits it inside
    // the post-restart boot-suppression window, which must NOT read as recovered).
    if (row.scheduler_max_overdue_seconds !== null) {
      out.push({
        customer_slug: row.customer_slug,
        condition: 'work_overdue',
        active: row.scheduler_max_overdue_seconds > overdueThresholdSeconds,
        detail: `max overdue ${row.scheduler_max_overdue_seconds}s (threshold ${overdueThresholdSeconds}s)`,
      })
    }
    out.push(...connectorConditions(row, connectorRunAgeThresholdSeconds))
    out.push(...tokenExpiryConditions(row, tokenLifetimesDays, tokenWarnDays, nowMs))
    // Indexed straight in, no `?? []`: both helpers default an absent list to
    // empty, and the extra branches pushed this function over its complexity
    // ceiling once the ss#2287 condition joined.
    out.push(...specControlConditions(row, openSpecControlKeys[row.customer_slug]))
    out.push(...webhookSurfaceConditions(row, openWebhookSurfaceKeys[row.customer_slug]))
    out.push(...gatewayLoopConditions(row, loopRed))
  }
  return out
}

/**
 * connector_check_error + connector_down:<server> states for one row.
 *
 * connector_down is a per-server tri-state:
 *   count === 0         → proven success: push inactive (resolves).
 *   open path satisfied → push active.
 *   anything else       → push NOTHING (hold): counts 1-2 are "failing again
 *     but not yet proven down" — pushing inactive would emit a false
 *     RECOVERED on the way INTO a new outage.
 * A NULL map pushes nothing for any server (whole-map hold).
 */
function connectorConditions(row: FleetStatusRow, runAgeThreshold: number): ConditionState[] {
  const out: ConditionState[] = []
  if (row.connector_check_ok !== null) {
    out.push({
      customer_slug: row.customer_slug,
      condition: 'connector_check_error',
      active: row.connector_check_ok === 0,
      detail:
        `connector_check_ok=${row.connector_check_ok} ` +
        '(seat cannot read its connector ledger or the tool→server mapping is gone — connector outages are NOT being counted)',
    })
  }
  const connectors = parseConnectorsMap(row.connectors_json)
  if (connectors === null) return out
  for (const [server, entry] of Object.entries(connectors)) {
    const condition: FleetCondition = `${CONNECTOR_DOWN_PREFIX}${server}`
    if (entry.consecutive_failures === 0) {
      out.push({
        customer_slug: row.customer_slug,
        condition,
        active: false,
        detail: `last ${server} call succeeded`,
      })
      continue
    }
    const runAge = entry.run_age_seconds ?? 0
    const connPath =
      entry.consecutive_failures >= CONNECTOR_DOWN_MIN_FAILURES &&
      entry.conn_evidence === true &&
      runAge >= runAgeThreshold
    const backstopPath =
      entry.consecutive_failures >= CONNECTOR_BACKSTOP_MIN_FAILURES &&
      runAge >= CONNECTOR_BACKSTOP_RUN_AGE_SECONDS
    if (!connPath && !backstopPath) continue // ambiguous run — hold
    const lastOk =
      entry.last_ok_age_seconds !== undefined
        ? `${entry.last_ok_age_seconds}s ago`
        : 'never observed'
    out.push({
      customer_slug: row.customer_slug,
      condition,
      active: true,
      detail:
        `${entry.consecutive_failures} consecutive failures over ${runAge}s ` +
        `(${connPath ? 'connection-class evidence' : 'signature-free backstop'}); ` +
        `last success ${lastOk}; last error: ${entry.last_error_message ?? '(no message captured)'}. ` +
        'Auto-resolves on the next successful call to this connector.',
    })
  }
  return out
}

async function getAlertState(
  db: D1Database,
  slug: string,
  condition: FleetCondition
): Promise<'open' | 'resolved' | null> {
  const row = await db
    .prepare('SELECT status FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?')
    .bind(slug, condition)
    .first<{ status: 'open' | 'resolved' }>()
  return row?.status ?? null
}

async function markOpen(db: D1Database, s: ConditionState, resendId: string | null): Promise<void> {
  await db
    .prepare(
      `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
       VALUES (?, ?, 'open', datetime('now'), NULL, ?, datetime('now'))
       ON CONFLICT (customer_slug, condition) DO UPDATE SET
         status = 'open', opened_at = datetime('now'), resolved_at = NULL,
         last_alert_id = excluded.last_alert_id, updated_at = datetime('now')`
    )
    .bind(s.customer_slug, s.condition, resendId)
    .run()
}

async function markResolved(db: D1Database, s: ConditionState): Promise<void> {
  await db
    .prepare(
      `UPDATE fleet_alert_state SET status = 'resolved', resolved_at = datetime('now'), updated_at = datetime('now')
       WHERE customer_slug = ? AND condition = ?`
    )
    .bind(s.customer_slug, s.condition)
    .run()
}

async function sendTransitionEmail(
  env: Env,
  s: ConditionState,
  kind: 'opened' | 'resolved'
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email ${kind} ${s.condition} for ${s.customer_slug}`)
    return { ok: false }
  }
  const label = conditionLabel(s.condition)
  const subject =
    kind === 'opened'
      ? `[SMD Ops] ALERT ${s.customer_slug}: ${label}`
      : `[SMD Ops] RECOVERED ${s.customer_slug}: ${label}`
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  // Escaped: connector_down details embed the seat's `last_error_message`,
  // which is arbitrary text from a customer Machine.
  const html =
    `<p><strong>${kind === 'opened' ? 'ALERT' : 'RECOVERED'}</strong>: ${escapeHtml(label)}</p>` +
    `<ul><li>Seat: ${escapeHtml(s.customer_slug)}</li><li>Detail: ${escapeHtml(s.detail)}</li>` +
    `<li>Severity: SEV1 per ADR 0064 - work begins on detection</li></ul>` +
    `<p><a href="${dashboard}">Fleet dashboard</a>. No automatic action was taken (ADR 0064/0065).</p>`
  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: env.ALERT_FROM_EMAIL ?? 'SMD Services Ops <team@smd.services>',
        to: env.ALERT_TO_EMAIL ?? 'team@smd.services',
        subject,
        html,
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] resend send failed:', err)
    return { ok: false }
  }
}

/**
 * Fail-soft self-ping to the alerter's own healthchecks.io check at the end of
 * a successful run. Skipped when unset; errors are swallowed. If runOnce throws
 * (which now can only happen on a fleet-wide read failure, since per-seat
 * evaluation is isolated), this is never reached and healthchecks pages.
 */
async function pingAlerterHealthcheck(env: Env): Promise<void> {
  const url = env.ALERTER_HEALTHCHECKS_PING_URL
  if (!url) return
  try {
    await fetch(url, { method: 'GET' })
  } catch (err) {
    console.error('[fleet-alerts] healthcheck self-ping failed:', err)
  }
}

/**
 * Fire the edge transition for one condition, if any. Marks alert state ONLY on
 * a successful send: a failed Resend POST leaves the row unmarked so the next
 * 2-minute cron retries — otherwise one transient failure silences the alert
 * forever (it would exist only in Worker logs). Returns the recorded transition
 * or null (silent / send-failed).
 */
async function processTransition(env: Env, s: ConditionState): Promise<Transition | null> {
  const prior = await getAlertState(env.DB, s.customer_slug, s.condition)
  if (s.active && prior !== 'open') {
    const sent = await sendTransitionEmail(env, s, 'opened')
    if (!sent.ok) return null
    await markOpen(env.DB, s, sent.resendId ?? null)
    return {
      customer_slug: s.customer_slug,
      condition: s.condition,
      kind: 'opened',
      detail: s.detail,
      emailed: true,
      resendId: sent.resendId,
    }
  }
  if (!s.active && prior === 'open') {
    const sent = await sendTransitionEmail(env, s, 'resolved')
    if (!sent.ok) return null
    await markResolved(env.DB, s)
    return {
      customer_slug: s.customer_slug,
      condition: s.condition,
      kind: 'resolved',
      detail: s.detail,
      emailed: true,
      resendId: sent.resendId,
    }
  }
  return null
}

/** One evaluation pass: read fleet, compute conditions, fire edge transitions. */
export async function runOnce(env: Env, nowMs: number = Date.now()): Promise<RunSummary> {
  const rows = await listFleetStatus(env.DB)
  const conditions = evaluateConditions(rows, nowMs, redSeconds(env), {
    overdueThresholdSeconds: workOverdueSeconds(env),
    connectorRunAgeThresholdSeconds: connectorRunAgeSeconds(env),
    tokenLifetimesDays: tokenLifetimesDays(env),
    tokenWarnDays: tokenWarnDays(env),
    openSpecControlKeys: await getOpenSpecControlKeys(env.DB),
    openWebhookSurfaceKeys: await getOpenWebhookSurfaceKeys(env.DB),
    gatewayLoopRedSeconds: gatewayLoopRedSeconds(env.GATEWAY_LOOP_RED_SECONDS),
  })
  const transitions: Transition[] = []

  // Group by seat so one bad row (a throwing DB op, a malformed value) can't
  // abort evaluation of every OTHER seat — the whole reason this loop exists is
  // to page on failure, so it must survive a single seat's failure.
  const bySeat = new Map<string, ConditionState[]>()
  for (const c of conditions) {
    const arr = bySeat.get(c.customer_slug)
    if (arr) arr.push(c)
    else bySeat.set(c.customer_slug, [c])
  }

  for (const [slug, states] of bySeat) {
    try {
      for (const s of states) {
        const t = await processTransition(env, s)
        if (t) transitions.push(t)
      }
    } catch (err) {
      console.error('[fleet-alerts] seat evaluation failed:', slug, err)
    }
  }

  const staleHolds = await getStaleHolds(env.DB)

  // Alert-sink delivery. Runs after condition evaluation and is independently
  // fail-soft, so a sink problem can never suppress the fleet_status pager.
  const sinkNotifications = await notifySinkAlerts(env)

  // ss#2547: the refused-or-unsent pager. Runs after condition evaluation for
  // the same reason the sink does, and is event-shaped rather than a condition
  // -- see ./send-refused for why a refusal has no green state to return to.
  const sendRefusals = await notifySendRefusals(env, rows)

  const summary: RunSummary = {
    at: new Date(nowMs).toISOString(),
    seats: rows.length,
    conditions,
    transitions,
    stale_holds: staleHolds,
    sink_notifications: sinkNotifications,
    send_refusals: sendRefusals,
  }
  if (transitions.length > 0) {
    console.log(`[fleet-alerts] transitions: ${JSON.stringify(transitions)}`)
  }
  if (sinkNotifications.length > 0) {
    console.log(`[fleet-alerts] sink notifications: ${JSON.stringify(sinkNotifications)}`)
  }
  if (sendRefusals.length > 0) {
    console.log(`[fleet-alerts] send refusals: ${JSON.stringify(sendRefusals)}`)
  }

  // Watch the watcher: only reached when the run completed without throwing.
  await pingAlerterHealthcheck(env)
  return summary
}

export default {
  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    await runOnce(env)
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    if (request.method === 'GET' && url.pathname === '/health') {
      return Response.json({ ok: true })
    }
    if (request.method === 'POST' && url.pathname === '/run') {
      const auth = request.headers.get('authorization') ?? ''
      if (!env.FLEET_ALERTS_BEARER || auth !== `Bearer ${env.FLEET_ALERTS_BEARER}`) {
        return Response.json({ error: 'unauthorized' }, { status: 401 })
      }
      return Response.json(await runOnce(env))
    }
    return Response.json({ error: 'not found' }, { status: 404 })
  },
}
