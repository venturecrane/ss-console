/**
 * The heartbeat body, parsed and never cast.
 *
 * Every alert-driving field the seat can emit has a parser here, and the
 * parsers share one contract the fleet_status upsert depends on (stated on
 * `parseObservability`). Lived inside `src/pages/api/internal/heartbeat.ts`
 * until 2026-09-10, when that route was the domain layer (code review
 * 2026-09-10, Architecture 3); the route now gates, parses through this
 * module, and delegates the write to `src/lib/db/fleet-status.ts`.
 *
 * The authoritative list of fields the pinned overlay can emit is
 * `operator/observability/heartbeat-fields.json`, enforced by
 * `tests/heartbeat-field-parity.test.ts`.
 */

export interface HeartbeatBody {
  heartbeat_ts: string
  last_audit_ts?: string
  last_skill_ts?: string
  process_uptime_seconds?: number
  version?: string
  sticky_stop_level?: string
  sticky_stop_reason?: unknown
  sticky_stop_condition?: unknown
  scheduler_ok?: unknown
  scheduler_job_count?: unknown
  scheduler_max_overdue_seconds?: unknown
  connector_check_ok?: unknown
  connectors?: unknown
  connector_token_age?: unknown
  spec_control_ok?: unknown
  spec_control?: unknown
  webhook_surface_ok?: unknown
  webhook_surface?: unknown
  cron_containment?: unknown
  audit_write_failures?: unknown
  audit_head?: unknown
  audit_rows?: unknown
  gateway_loop_ok?: unknown
  gateway_loop_age_seconds?: unknown
  gateway_supervisor_state?: unknown
  gateway_restarts_last_hour?: unknown
  send_refusals?: unknown
  send_refusals_last_ts?: unknown
  send_refusals_json?: unknown
}

// The breaker ladder vocabulary (overlay shared/cost_breaker.read_stop_state).
// Anything else is stored as NULL — never guess a level from junk input.
const STICKY_STOP_LEVELS = new Set(['OK', 'WARN', 'SOFT_STOP', 'HARD_STOP', 'unknown'])

// The four meters that drive the ladder, plus the Captain's manual clear
// (overlay shared/sticky_stop.StickyStopCondition). Closed for the same reason
// as the level: a condition we do not know is a writer we do not understand,
// and it must not become a word the alerter routes on. NULL, never guessed.
const STICKY_STOP_CONDITIONS = new Set([
  'consecutive_tool_failures',
  'refusal_cascade',
  'time_budget_exceeded',
  'cost_threshold',
  'captain_clear',
])

// The seat caps its reason at 300 chars; this is the receiver's own bound, so
// a seat running unexpected code cannot write an unbounded column.
const STICKY_STOP_REASON_MAX = 300

// The part-1 supervisor's state machine (operator/templates/entrypoint.sh,
// gateway_liveness_state). Closed vocabulary for the same reason as the breaker
// ladder: a word we do not know is a writer we do not understand, and it must
// not become a state the alerter acts on. Stored as NULL (hold), never guessed.
//
// THIS SET IS THE THIRD LINK IN A FOUR-LINK CHAIN, and it is the one that was
// missed. entrypoint.sh writes the word, the overlay's
// shared/gateway_loop_check.py SUPERVISOR_STATES forwards it, THIS parser
// stores it, and workers/fleet-alerts/src/gateway-loop.ts grades it. Every one
// of those four drops an unrecognised word silently, so the chain is only as
// wide as its narrowest link and a gap anywhere reads as a healthy NULL rather
// than a failure. ss#2677 widened links 1 and 4 and overlay#339 widened link 2;
// this link kept the original five words, which would have nulled both new
// states in transit and reproduced the exact silence those changes exist to
// end. When SUPERVISOR_STATES moves in the overlay, it moves here in the same
// change — tests/heartbeat-field-parity.test.ts asserts the two agree word for
// word wherever the overlay is checked out.
const GATEWAY_SUPERVISOR_STATES = new Set([
  'armed',
  'not-armed',
  // Bootstrap has not yet exec'd the gateway: a normal, minutes-long window on
  // every boot. Previously reported as `inert`, which pages.
  'starting',
  'inert',
  'not-watching',
  // No fresh loop beat in the whole startup grace — wedged DURING startup.
  // Previously reported as `not-armed`, which never pages and must not, since
  // `not-armed` is also every healthy seat's first thirty seconds.
  'never-healthy',
  'refusing',
])

function parseGatewaySupervisorState(value: unknown): string | null {
  return typeof value === 'string' && GATEWAY_SUPERVISOR_STATES.has(value) ? value : null
}

// Coerce the overlay's scheduler_ok signal to 1/0/NULL. Accepts a boolean or a
// literal 0/1 (JSON booleans and small-int flags are both idiomatic in the
// emitter). Anything else — including a truthy non-1 number — is junk and
// stored NULL: never manufacture a health verdict from an unrecognized value.
function parseSchedulerOk(value: unknown): 0 | 1 | null {
  if (value === true || value === 1) return 1
  if (value === false || value === 0) return 0
  return null
}

// Non-negative integer counters (job count, max-overdue seconds). Junk — a
// float, a negative, a string, a missing field — is stored NULL.
function parseNonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

// The audit chain head (#2500): a sha-256 hexdigest from the overlay's
// `compute_row_hash`. Parsed, never cast — this value is stored as the pinned
// head an integrity check later compares against, and junk pinned as a head
// would make every subsequent verification fail against a value that never was
// a hash. Anything not exactly 64 lowercase hex characters is stored NULL,
// which reads as "nothing to pin", never as "the chain broke".
const AUDIT_HEAD_RE = /^[0-9a-f]{64}$/
function parseAuditHead(value: unknown): string | null {
  return typeof value === 'string' && AUDIT_HEAD_RE.test(value) ? value : null
}

// Per-connector map guardrails (ADR 0080). The overlay writer caps at 32
// servers and 200-char messages; these ingest-side caps are the backstop
// against a compromised or drifted emitter, not the primary limit.
const CONNECTORS_MAX_SERVERS = 64
const CONNECTORS_MAX_MESSAGE_CHARS = 200
const CONNECTOR_SERVER_NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/

// One connector entry, parsed-not-cast (ADR 0080 three-tier rule): a valid
// entry's meaning doesn't depend on its neighbors, so an invalid ENTRY is
// dropped (absence = the alerter holds for that server) while valid siblings
// are kept. Fields inside a kept entry are never individually nulled — a
// half-trusted entry could open or resolve an alert wrongly; entries are
// atomic. consecutive_failures is the one required field; a failure run
// (count > 0) additionally requires its writer-side run_age_seconds, because
// every open condition is age-gated and an ageless run can satisfy none.
function parseConnectorEntry(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const count = parseNonNegInt(raw.consecutive_failures)
  if (count === null) return null
  const entry: Record<string, unknown> = { consecutive_failures: count }
  if (count > 0) {
    const runAge = parseNonNegInt(raw.run_age_seconds)
    if (runAge === null) return null
    entry.run_age_seconds = runAge
    entry.conn_evidence = raw.conn_evidence === true
  }
  const lastOkAge = parseNonNegInt(raw.last_ok_age_seconds)
  if (lastOkAge !== null) entry.last_ok_age_seconds = lastOkAge
  const lastErrorAge = parseNonNegInt(raw.last_error_age_seconds)
  if (lastErrorAge !== null) entry.last_error_age_seconds = lastErrorAge
  if (typeof raw.last_error_message === 'string' && raw.last_error_message.length > 0) {
    entry.last_error_message = raw.last_error_message.slice(0, CONNECTORS_MAX_MESSAGE_CHARS)
  }
  return entry
}

// The whole map: structurally-invalid (not a plain object, or absurdly large)
// → NULL, meaning "trust nothing this beat"; under NULL-hold semantics every
// open connector alert simply holds, which makes whole-map NULL cheap and
// honest. Returns the serialized JSON to store, or null.
function parseConnectorsJson(value: unknown): string | null {
  if (value === undefined) return null
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length > CONNECTORS_MAX_SERVERS) return null
  const parsed: Record<string, unknown> = {}
  for (const [server, raw] of entries) {
    if (!CONNECTOR_SERVER_NAME_RE.test(server)) continue
    const entry = parseConnectorEntry(raw)
    if (entry !== null) parsed[server] = entry
  }
  return JSON.stringify(parsed)
}

// server → token-file age seconds (ss#2148). A SEPARATE map from connectors:
// token age must never synthesize a health entry. Structurally-invalid → NULL
// ("trust nothing this beat" — the token-expiry condition holds on NULL).
function parseConnectorTokenAgeJson(value: unknown): string | null {
  if (value === undefined) return null
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length > CONNECTORS_MAX_SERVERS) return null
  const parsed: Record<string, number> = {}
  for (const [server, raw] of entries) {
    if (!CONNECTOR_SERVER_NAME_RE.test(server)) continue
    const age = parseNonNegInt(raw)
    if (age !== null) parsed[server] = age
  }
  return JSON.stringify(parsed)
}

// Authored-spec control map (ss#2234): "<output_class>.<property>" →
// { declared, installed }. Keyed per PROPERTY because a seat can have
// staff.voice installed and staff.format missing, and resolving one must not
// clear the alert on the other.
const SPEC_CONTROL_MAX_KEYS = 64
const SPEC_CONTROL_KEY_RE = /^[a-z_]{1,40}\.(voice|format)$/

// Same three-tier rule as the connector map. Both flags are REQUIRED in a kept
// entry and neither is defaulted: `installed` is what opens and closes the
// alert, so inferring it from a missing field would be manufacturing the
// verdict. An entry that cannot supply both is dropped (that key holds);
// a structurally-invalid MAP → NULL (nothing this beat is trusted, every open
// alert holds).
function parseSpecControlJson(value: unknown): string | null {
  if (value === undefined) return null
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length > SPEC_CONTROL_MAX_KEYS) return null
  const parsed: Record<string, { declared: boolean; installed: boolean }> = {}
  for (const [key, raw] of entries) {
    if (!SPEC_CONTROL_KEY_RE.test(key)) continue
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue
    const entry = raw as Record<string, unknown>
    if (typeof entry.declared !== 'boolean' || typeof entry.installed !== 'boolean') continue
    parsed[key] = { declared: entry.declared, installed: entry.installed }
  }
  return JSON.stringify(parsed)
}

// Webhook expected-tool surface (ss#2287, the ss#2222 warn tier): tool name →
// { expected, offered }. Tool names come from the overlay's
// WEBHOOK_EXPECTED_TOOLS tuple, so the key shape is a python identifier.
const WEBHOOK_SURFACE_MAX_TOOLS = 64
const WEBHOOK_SURFACE_TOOL_RE = /^[a-z][a-z0-9_]{0,63}$/

// Same three-tier rule as the connector and spec-control maps. Both flags are
// REQUIRED in a kept entry and neither is defaulted: `offered` is what opens and
// closes the alert, so inferring it from a missing field would be manufacturing
// the verdict. An entry that cannot supply both is dropped (that tool holds); a
// structurally-invalid MAP → NULL (nothing this beat is trusted).
//
// An EMPTY reported map is deliberately preserved as `{}` rather than collapsed
// to NULL: the overlay's docstring is explicit that "checked, every expected
// tool is offered" is the state that RESOLVES an open alert, and NULL holds.
function parseWebhookSurfaceJson(value: unknown): string | null {
  if (value === undefined) return null
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const entries = Object.entries(value as Record<string, unknown>)
  if (entries.length > WEBHOOK_SURFACE_MAX_TOOLS) return null
  const parsed: Record<string, { expected: boolean; offered: boolean }> = {}
  for (const [tool, raw] of entries) {
    if (!WEBHOOK_SURFACE_TOOL_RE.test(tool)) continue
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) continue
    const entry = raw as Record<string, unknown>
    if (typeof entry.expected !== 'boolean' || typeof entry.offered !== 'boolean') continue
    parsed[tool] = { expected: entry.expected, offered: entry.offered }
  }
  return JSON.stringify(parsed)
}

// ss#2547: the send-refusal event list.
//
// Every field here is bounded because the whole point of the list is to carry a
// refusal REASON verbatim from a customer Machine into an ops inbox, and
// verbatim text from a seat is the one input this handler must never trust by
// size. Whole-array invalid (not an array, more entries than the writer is
// allowed to send, or serializing past the cap) stores NULL, which the pager
// reads as "no detail this beat" -- the count and the marker still land, so a
// junk list can never suppress the page itself.
const SEND_REFUSAL_MAX_ENTRIES = 5
const SEND_REFUSAL_MAX_CHARS = 4096
const SEND_REFUSAL_FIELD_CHARS = 200
// 'degraded' (2026-08-24): the routine's own pre-run withheld an unfit digest
// (SUPPRESSED_WAKE with a digest_degraded basis, counted by the overlay's
// count_send_refusals) — the deliberate nothing that must page like a refusal.
const SEND_REFUSAL_KINDS = new Set(['refused', 'unsent', 'degraded'])

// ISO-8601 with an explicit zone. A bare local-looking timestamp is refused
// rather than assumed UTC: this value is the pager's ordering marker, and a
// marker off by a zone offset either re-pages a handled event or swallows a new
// one.
const ISO_8601_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/

/**
 * A timestamp, normalized to canonical UTC (`...Z`).
 *
 * Normalized rather than stored verbatim because the alerter orders events by
 * this string. Two seats reporting the same instant with different offsets
 * would sort wrongly as raw text; as canonical UTC, string order and instant
 * order are the same thing.
 */
function parseIsoInstant(value: unknown): string | null {
  if (typeof value !== 'string' || !ISO_8601_RE.test(value)) return null
  const ms = Date.parse(value)
  return Number.isNaN(ms) ? null : new Date(ms).toISOString()
}

/** One event. Atomic like a connector entry: half of one would page wrongly. */
function parseSendRefusalEntry(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const raw = value as Record<string, unknown>
  const ts = parseIsoInstant(raw.ts)
  if (ts === null) return null
  if (typeof raw.kind !== 'string' || !SEND_REFUSAL_KINDS.has(raw.kind)) return null
  const entry: Record<string, unknown> = { ts, kind: raw.kind }
  for (const field of ['routine', 'tool', 'reason'] as const) {
    const text = raw[field]
    if (typeof text === 'string' && text.length > 0) {
      entry[field] = text.slice(0, SEND_REFUSAL_FIELD_CHARS)
    }
  }
  const needsYou = parseNonNegInt(raw.needs_you)
  if (needsYou !== null) entry.needs_you = needsYou
  return entry
}

function parseSendRefusalsJson(value: unknown): string | null {
  if (!Array.isArray(value) || value.length > SEND_REFUSAL_MAX_ENTRIES) return null
  const parsed: Array<Record<string, unknown>> = []
  for (const raw of value) {
    const entry = parseSendRefusalEntry(raw)
    if (entry !== null) parsed.push(entry)
  }
  const json = JSON.stringify(parsed)
  return json.length > SEND_REFUSAL_MAX_CHARS ? null : json
}

/**
 * Every alert-driving field, parsed-not-cast.
 *
 * All of these share ONE contract that the surrounding upsert depends on: they
 * overwrite every beat INCLUDING back to NULL. A stale pinned verdict must not
 * outlive the signal that produced it — `COALESCE` here would keep a
 * `scheduler_ok=0` (or a broken-control map) forever after an overlay rollback
 * dropped the field. Grouped into one function so that contract is stated once
 * and a new field cannot quietly acquire different semantics.
 *
 * The three `*_ok` booleans share `parseSchedulerOk`'s 1/0/NULL coercion, and 0
 * always means the SEAT's own check is broken — the alerter pages that
 * separately (`connector_check_error`, `spec_control_unprovable`) rather than
 * letting the whole class go dark or, worse, reporting our blindness as the
 * customer's missing config.
 */
export function parseObservability(body: HeartbeatBody) {
  return {
    schedulerOk: parseSchedulerOk(body.scheduler_ok),
    schedulerJobCount: parseNonNegInt(body.scheduler_job_count),
    schedulerMaxOverdueSeconds: parseNonNegInt(body.scheduler_max_overdue_seconds),
    connectorCheckOk: parseSchedulerOk(body.connector_check_ok),
    connectorsJson: parseConnectorsJson(body.connectors),
    connectorTokenAgeJson: parseConnectorTokenAgeJson(body.connector_token_age),
    specControlOk: parseSchedulerOk(body.spec_control_ok),
    specControlJson: parseSpecControlJson(body.spec_control),
    // ss#2287: the ss#2222 warn tier. ok=0 is the seat saying it could not
    // resolve its own webhook toolset — our blindness, paged separately from a
    // missing tool, for spec_control's reason.
    webhookSurfaceOk: parseSchedulerOk(body.webhook_surface_ok),
    webhookSurfaceJson: parseWebhookSurfaceJson(body.webhook_surface),
    // ss#2276: 1 = the CRON_CONTAINMENT volume sentinel is present (all managed
    // crons deliberately off, surviving boots), 0 = normal, NULL = unreported.
    cronContainment: parseSchedulerOk(body.cron_containment),
    // ss#2498 + ss#2500: what the ledger says about itself. `0` failures is a
    // REAL value and the load-bearing one — it is what distinguishes a quiet
    // ledger from a broken one. NULL means the seat cannot answer, and these
    // three are stored with COALESCE for that reason: overwriting a known
    // failure count with an absence would erase the record and silently reset
    // the delta baseline to zero.
    auditWriteFailures: parseNonNegInt(body.audit_write_failures),
    auditHead: parseAuditHead(body.audit_head),
    auditRows: parseNonNegInt(body.audit_rows),
    // ss#2488 part 2: the gateway's loop pulse and the part-1 supervisor's own
    // state, shipped by the webhook gate because it is the one process that
    // survives a wedge. ok=0 is the seat saying it could not LOOK (our
    // blindness, paged as gateway_loop_unprovable); age NULL is a hold -- the
    // seat's arming latch, a Hermes pin with no heartbeat, or boot suppression
    // -- never a verdict. restarts_last_hour is the field a restart cannot
    // race: the kill-ledger line is on the volume before the container dies.
    gatewayLoopOk: parseSchedulerOk(body.gateway_loop_ok),
    gatewayLoopAgeSeconds: parseNonNegInt(body.gateway_loop_age_seconds),
    gatewaySupervisorState: parseGatewaySupervisorState(body.gateway_supervisor_state),
    gatewayRestartsLastHour: parseNonNegInt(body.gateway_restarts_last_hour),
    // ss#2547: what the seat's own gates refused, and what it woke with and
    // never tried to send. Event-shaped, so these three are the exception to
    // the overwrite-including-NULL rule stated above and are stored with
    // COALESCE: the marker is the alerter's memory of what it has already
    // paged for, and erasing it on an unreported beat would re-page the whole
    // backlog the next time the seat spoke. See migration 0109.
    sendRefusals: parseNonNegInt(body.send_refusals),
    sendRefusalsLastTs: parseIsoInstant(body.send_refusals_last_ts),
    sendRefusalsJson: parseSendRefusalsJson(body.send_refusals_json),
  }
}

export interface ParsedStickyStop {
  level: string | null
  reason: string | null
  condition: string | null
}

/**
 * The breaker ladder as the seat reports it. Level, reason and condition all
 * overwrite every beat INCLUDING back to NULL, so a reason can never outlive
 * the level it explained. Pairing is enforced at the SENDER (the seat reads
 * level and cause from one row and emits a cause only with a level); the
 * receiver validates shape and stores each field on its own.
 */
export function parseStickyStop(body: HeartbeatBody): ParsedStickyStop {
  const level =
    typeof body.sticky_stop_level === 'string' && STICKY_STOP_LEVELS.has(body.sticky_stop_level)
      ? body.sticky_stop_level
      : null
  const reason =
    typeof body.sticky_stop_reason === 'string'
      ? body.sticky_stop_reason.slice(0, STICKY_STOP_REASON_MAX)
      : null
  const condition =
    typeof body.sticky_stop_condition === 'string' &&
    STICKY_STOP_CONDITIONS.has(body.sticky_stop_condition)
      ? body.sticky_stop_condition
      : null
  return { level, reason, condition }
}
