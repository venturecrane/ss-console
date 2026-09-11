/**
 * Operator audit log viewer — typed contract + read resolver.
 *
 * Per PRD §13 and #873, every safety-relevant action the Operator
 * takes (draft created, sent approved, trust ceiling promoted,
 * connector bound, invariant violation, etc.) writes an `audit_log` row
 * on the per-customer Hermes Machine D1. The audit_log writer landed
 * in PR #942 (`operator/adapter/audit_log.py`); the row schema and
 * accepted action_type vocabulary are the source of truth there.
 *
 * This module owns the read side: typed `AuditEntry` shape, URL
 * search-params parsing (filters / pagination), in-memory filter +
 * sort + paginate helpers, friendly formatters for action class and
 * actor role, and the resolver the page invokes.
 *
 * Data source: per-customer Hermes Machine D1 (ADR 0007 + 0009). The
 * portal Worker cannot bind directly to a per-customer D1; the live read
 * goes through the frozen ADR 0043 runtime-read seam in
 * `activity-read.ts`, which feeds the parsed rows into the filter / sort
 * / paginate helpers here. (An earlier per-page resolver with a stub
 * bridge fetch lived in this file until 2026-09-09; nothing called it
 * once activity-read.ts took the seam, and it is gone.) No mock data. No
 * placeholder copy. See docs/style/empty-state-pattern.md.
 *
 * Mirrors the shape of `src/lib/portal/operator/drafts.ts` deliberately
 * (sibling list view, same pagination contract). Differences:
 *
 *   - Audit rows are immutable history, not a triage queue: default sort
 *     is `ts_desc` (newest first), no priority bucket, no "max age"
 *     window. Instead the date range filter (`from` / `to`) is the
 *     primary temporal filter, defaulting to the last 7 days per AC.
 *   - The free-text `q` filter does substring match over
 *     `reason` + `actor` + `target` — these are the three fields a
 *     compliance reviewer scans for "what happened around X".
 *   - Action class is a multi-select over the closed
 *     `ACCEPTED_ACTION_TYPES` vocabulary mirrored from the writer.
 */

import { type Page, paginate } from './pagination'

/**
 * Accepted `action_type` values for audit rows, mirrored from
 * `operator/adapter/audit_log.py::ACCEPTED_ACTION_TYPES`. The
 * vocabulary is closed — additions require updating both this list and
 * the Python constant in lockstep (see writer-side doc reference for
 * the spec at docs/specs/operator/d1-schema.md §1).
 *
 * Listed here as a TypeScript const so the filter bar can render
 * options from a single source and the resolver can validate incoming
 * params against the vocabulary.
 */
export const AUDIT_ACTION_TYPES = [
  // Draft lifecycle
  'DRAFT_CREATED',
  'DRAFT_APPROVED',
  'DRAFT_REJECTED',
  'DRAFT_EXPIRED',
  // Memory rules
  'MEMORY_RULE_ADDED',
  'MEMORY_RULE_EDITED',
  'MEMORY_RULE_DELETED',
  // Trust ceiling
  'TRUST_PROMOTED',
  'TRUST_DEMOTED',
  // Skill activation
  'SKILL_ENABLED',
  'SKILL_DISABLED',
  // Routine scheduling (#2498). Deliberately distinct from the two above:
  // SKILL_ENABLED is whether the Operator is ALLOWED to do a thing, these are
  // whether it is SCHEDULED to. A seat can have every skill enabled and
  // initiate nothing, and conflating them would report it as fully armed.
  'ROUTINE_ENABLED',
  'ROUTINE_DISABLED',
  // Agent lifecycle
  'AGENT_STOPPED',
  'AGENT_RESUMED',
  // Connector lifecycle
  'CONNECTOR_BOUND',
  'CONNECTOR_UNBOUND',
  'CONNECTOR_AUTH_EXPIRED',
  'CONNECTOR_AUTH_RESTORED',
  'CONNECTOR_TOKEN_REFRESHED',
  'CONNECTOR_HEALTH_PROBE_FAILED',
  // Scope changes
  'SCOPE_CHANGED',
  // Safety substrate
  'INVARIANT_VIOLATION',
  'INVARIANT_BOOT_CHECK_FAILED',
  // RBAC and compliance
  'RBAC_EVENT',
  'COMPLIANCE_PACKET_EXPORTED',
  // Voice gate
  'VOICE_GATE_PASSED',
  'VOICE_GATE_NEAR_PASS',
  'VOICE_GATE_FAILED',
  // Fabrication and escalation
  'FABRICATION_FILTER_TRIGGERED',
  'IDENTIFIER_UNVERIFIED',
  'ESCALATION_FIRED',
  'ESCALATION_ACKNOWLEDGED',
  // Inbound quarantine (ADR 0027)
  'INBOUND_RECEIVED',
  // Honcho mirror (ADR 0016) + agent-authored skills (ADR 0017)
  'HONCHO_CONCLUSION_DISMISSED',
  'AGENT_SKILL_CREATED',
  'AGENT_SKILL_REMOVED',
  // customer-sync sidecar (ADR 0019)
  'CUSTOMER_YAML_SYNCED',
  'CUSTOMER_YAML_STRUCTURAL_CHANGE_DEFERRED',
  // Subagent + cron lifecycle (ADR 0021)
  'SUBAGENT_STOPPED',
  'SUBAGENT_INCOMPLETE',
  'SUPPRESSED_WAKE',
  // The wake half of the same gate (#2253): a gated cron logged why it did not
  // act and logged nothing when it did, so the tick that mattered was the tick
  // with no row. Written best-effort — a wake is never gated on its audit row.
  'EMITTED_WAKE',
  // Reply channel (ADR 0055) — overlay hermes-smd-reply emits these when the
  // Operator answers a rostered colleague (recipient-locked, roster-authorized).
  'REPLY_SENT',
  'REPLY_HELD',
  'REPLY_FAILED',
  // ss#2167 — the outbound matter-identity gate ran on a reply but could not
  // resolve the cited matter's party list, so membership was neither confirmed
  // nor denied. Recorded rather than held: get_matter fires on 8 of 86 reply
  // turns, so holding on unresolved would withhold correct client replies at a
  // rate nobody has measured. These rows are that measurement.
  'MATTER_UNRESOLVED',
  // Decommission lifecycle. Per-step BEGIN/COMPLETE/FAILED rows added
  // 2026-06-12 (code review) so the compliance trail distinguishes the
  // nine teardown steps; mirrors ACCEPTED_ACTION_TYPES in
  // operator/adapter/audit_log.py and d1-schema.md §1. The parity test in
  // tests/portal-operator-audit.test.ts asserts set equality with the
  // python constant — extend both sides together.
  'DECOMMISSION_INITIATED',
  'DECOMMISSION_DRAIN_COMPLETE',
  'DECOMMISSION_STEP_BEGIN',
  'DECOMMISSION_STEP_COMPLETE',
  'DECOMMISSION_STEP_FAILED',
  'DECOMMISSION_FINAL',
  // 2026-08-02 vocabulary reconciliation (#2122): live-producer types that
  // were being written to both seats' ledgers while absent here, so the
  // ?action= filter silently no-opped on them and the roll-ups could not
  // name the bulk of the ledger. All start SUPPRESSED in activity-language
  // (they were already invisible; surfacing any to clients is a product
  // call with authored copy, not a vocabulary side effect). Mirrors
  // operator/adapter/audit_log.py — extend both together (parity test).
  'TOOL_CALL_COMPLETED',
  'LLM_TURN_COMPLETED',
  'WEBHOOK_ROUTED',
  'WEBHOOK_SUPPRESSED',
  'BROKER_DECISION_ALLOWED',
  'BROKER_EXECUTED',
  'CONFIG_WRITE',
  'CONFIRM_SEND_DISPATCHED',
  'CONFIRM_SEND_FAILED',
  'SPEC_GATE_TRIGGERED',
  'VOICE_GATE_TRIGGERED',
  'CORRECTION_PROPOSED',
  'RULE_PROPOSED',
  'ESTABLISHMENT_SUBMITTED',
  'ESTABLISHMENT_RESULT',
  'RULE_REQUEST_NOTIFIED',
  'RULE_DECLINED',
  'RULE_LAPSED',
  'ACT_PROPOSED',
  'ACT_COMMITTED',
  'OPS_REQUEST_RECORDED',
  'OPS_REQUEST_RESOLVED',
  'OPS_REQUEST_LAPSED',
  // ss#2614 routine 11: one row per chronology-job transition, written by the
  // workspace broker under its own uid (counts and ids, never the envelope).
  'MEDCHRON_JOB_SUBMITTED',
  'MEDCHRON_JOB_RUNNING',
  'MEDCHRON_JOB_HELD',
  'MEDCHRON_JOB_DELIVERED',
  'MEDCHRON_JOB_FAILED',
] as const

/**
 * Console-ledger synthetic actions (portal_login_events / portal_action_events
 * unions in activity-read.ts). Deliberately NOT part of AUDIT_ACTION_TYPES:
 * that constant mirrors the Machine writer vocabulary and is parity-tested
 * against operator/adapter/audit_log.py — these actions have no Machine
 * producer and never will. They are validated separately for the ?action=
 * filter below and carry authored client copy in activity-language.ts.
 */
export const CONSOLE_ACTION_TYPES = [
  'PORTAL_LOGIN',
  'TEAM_ROLE_GRANTED',
  'TEAM_ROLE_REVOKED',
  'TEAM_INVITE_SENT',
  'CONFIG_CHANGE_SUBMITTED',
  'CONFIG_CHANGE_REJECTED',
  'CONNECTOR_RECONSENT_REQUESTED',
  'OUTPUT_SPEC_AUTHORED',
  'OUTPUT_SPEC_REJECTED',
  // Synthesized by activity-read from operator_entitlement_changes (0097).
  // Was rendered and categorized but absent from BOTH validation lists, so
  // ?action=ENTITLEMENT_CHANGED silently no-opped to "show everything"
  // (#2122 read-side survey, defect 1). Console-plane: no Machine producer.
  'ENTITLEMENT_CHANGED',
  // Synthesized from portal_action_events when a Named Administrator pulls the
  // per-matter audit record (ss#2122). Console-plane: the Machine has no
  // producer for it, because the export happens in the console. Distinct from
  // the Machine's COMPLIANCE_PACKET_EXPORTED, which the packet builder writes.
  'COMPLIANCE_RECORD_EXPORTED',
] as const

const AUDIT_ACTION_TYPE_SET: ReadonlySet<string> = new Set([
  ...AUDIT_ACTION_TYPES,
  ...CONSOLE_ACTION_TYPES,
])

/**
 * The effective decision the writer recorded for an action. Mirrors the
 * trust-ceiling outcomes the agent emits per ADR 0005. The vocabulary is
 * closed — unknown values surface as the raw string rather than a
 * fabricated friendly label.
 *
 *   allow            — Action proceeded without reviewer gate.
 *   draft_for_review — Default. The Operator proposed; a reviewer must
 *                      approve before the effect lands.
 *   refuse           — The action was refused (trust ceiling, invariant,
 *                      voice gate, fabrication filter).
 */
export type AuditDecision = 'allow' | 'draft_for_review' | 'refuse'

export const AUDIT_DECISIONS: readonly AuditDecision[] = [
  'allow',
  'draft_for_review',
  'refuse',
] as const

/**
 * Closed vocabulary for the actor's role at the time of the audited
 * action. Mirrors `operator/adapter/audit_log.py::ActorRole`. The
 * column itself is nullable in the writer; null surfaces as `null` here.
 */
export type AuditActorRole = 'principal' | 'staff' | 'compliance' | 'agent' | 'captain'

export const AUDIT_ACTOR_ROLES: readonly AuditActorRole[] = [
  'principal',
  'staff',
  'compliance',
  'agent',
  'captain',
] as const

/**
 * Sort options exposed via `?sort=`. Default is `ts_desc` (newest
 * first) — audit history is a "what just happened" surface for
 * compliance reviewers. `ts_asc` is the chronological reading order
 * for incident reconstruction.
 */
export type AuditSort = 'ts_desc' | 'ts_asc'

export const AUDIT_SORTS: readonly AuditSort[] = ['ts_desc', 'ts_asc'] as const

/**
 * One row in the audit log viewer. Shape mirrors what the Hermes
 * bridge will return when #821 lands — fields map to `audit_log`
 * columns one-for-one. Field semantics:
 *
 *   id        — ULID written by the audit writer. Stable identifier
 *               used as the row key and for the optional expand
 *               affordance.
 *   ts        — ISO 8601 UTC with millisecond precision. The writer
 *               always emits this; the renderer formats it for display.
 *   actor     — Caller identity (`'agent'`, `'captain'`, or a
 *               `person_mappings.id`). Renders verbatim in the actor
 *               cell; the table never fabricates a friendly name.
 *   actorRole — Optional role at action time (see AuditActorRole).
 *   action    — Action class. Validated against AUDIT_ACTION_TYPES;
 *               unknown values render verbatim so we never silently
 *               drop a row whose class drifted ahead of this constant.
 *   target    — Opaque object reference (a draft id, connector slug, or
 *               other source-system handle — never interpreted, per ADR
 *               0050 §6). `null` when the action has no specific target
 *               (e.g., `INVARIANT_BOOT_CHECK_FAILED`).
 *   decision  — Effective decision (see AuditDecision). `null` when
 *               the writer did not attach one (the schema field is
 *               nullable in some action classes).
 *   reason    — Short human-readable explanation of the decision /
 *               action. Renders truncated by default with an expand
 *               affordance on the row; full text lives in the row's
 *               `<details>` block. May be null if the writer did not
 *               attach a reason.
 *   skill     — Optional skill slug that originated the action.
 *               Powers the skill multi-select; mirrors
 *               `audit_log.skill_name`.
 *
 * Per ADR 0052 §6, an action's object is referenced only by the generic
 * opaque `target` handle — there is no per-vertical reference field.
 */
export interface AuditEntry {
  id: string
  ts: string
  actor: string
  actorRole: AuditActorRole | null
  action: string
  target: string | null
  decision: AuditDecision | null
  reason: string | null
  skill: string | null
}

/**
 * Parameters parsed from the page's URLSearchParams. All optional — the
 * page renders an unfiltered list when nothing is passed, except for the
 * date-range default below.
 *
 *   skills      — Multi-select skill filter. Empty array = all skills.
 *   actions     — Multi-select action-class filter. Empty array = all
 *                 actions. Values outside AUDIT_ACTION_TYPES are
 *                 dropped (defensive against bookmark drift).
 *   from        — Inclusive lower bound on `ts` as an ISO date or
 *                 ISO datetime. null = no lower bound. Empty / invalid
 *                 input falls back to null so a typo doesn't blank the
 *                 list silently.
 *   to          — Inclusive upper bound on `ts`. Same semantics as
 *                 `from`. When both are null and `defaultDateRange` is
 *                 true (the page contract), the resolver lets the page
 *                 supply a "last 7 days" window.
 *   q           — Free-text search applied over `reason` + `actor` +
 *                 `target` (case-insensitive substring). null = no
 *                 filter.
 *   sort        — One of AUDIT_SORTS. Defaults to 'ts_desc'.
 *   page        — 1-indexed page number. Defaults to 1. Out-of-range
 *                 values clamp to 1.
 *   pageSize    — Defaults to 100 per AC. Capped at MAX_AUDIT_PAGE_SIZE.
 */
export interface AuditListParams {
  skills: readonly string[]
  actions: readonly string[]
  from: string | null
  to: string | null
  q: string | null
  sort: AuditSort
  page: number
  pageSize: number
}

export const DEFAULT_AUDIT_PAGE_SIZE = 100
export const MAX_AUDIT_PAGE_SIZE = 500

/**
 * Default date-range window for the audit viewer. Compliance reviewers
 * scanning "what happened today / this week" want a tight default so
 * the page is responsive even when the customer has months of history.
 * Seven days is the AC default; reviewers widen via the filter bar.
 */
export const DEFAULT_AUDIT_RANGE_DAYS = 7

/**
 * Validate that a string is a parsable ISO date or datetime. Returns
 * the input verbatim when valid (preserves the user's representation
 * for the form's re-hydration) and null when not. Defensive: rejects
 * NaN, empty strings, and obvious typos so the resolver never silently
 * filters out everything because a single character is wrong.
 */
function normalizeIsoTimestamp(raw: string | null): string | null {
  if (raw === null) return null
  const trimmed = raw.trim()
  if (trimmed.length === 0) return null
  const parsed = Date.parse(trimmed)
  if (!Number.isFinite(parsed)) return null
  return trimmed
}

/**
 * Parse params from URLSearchParams. Defensive — every field is
 * validated against the closed vocabularies above; unknown values fall
 * back to safe defaults instead of throwing. This keeps the surface
 * stable under user-typed URLs and bookmark drift.
 *
 * Multi-select fields accept both repeated params
 * (`?skill=intake&skill=deadline`) and comma-separated values
 * (`?skill=intake,deadline`).
 */
export function parseAuditListParams(searchParams: URLSearchParams): AuditListParams {
  // Multi-select skill: union of repeated params and comma-separated values.
  const rawSkillParams = searchParams.getAll('skill')
  const skills = Array.from(
    new Set(
      rawSkillParams
        .flatMap((value) => value.split(','))
        .map((value) => value.trim())
        .filter((value) => value.length > 0)
    )
  )

  // Multi-select action: same shape as skill, validated against the
  // closed vocabulary. Unknown values are dropped (we never want to
  // silently filter the whole list because a stale bookmark named an
  // action class that no longer exists).
  const rawActionParams = searchParams.getAll('action')
  const actions = Array.from(
    new Set(
      rawActionParams
        .flatMap((value) => value.split(','))
        .map((value) => value.trim())
        .filter((value) => AUDIT_ACTION_TYPE_SET.has(value))
    )
  )

  const from = normalizeIsoTimestamp(searchParams.get('from'))
  const to = normalizeIsoTimestamp(searchParams.get('to'))

  const rawQ = searchParams.get('q')?.trim() ?? ''
  const q = rawQ.length > 0 ? rawQ.toLowerCase() : null

  const rawSort = searchParams.get('sort')
  const sort: AuditSort = AUDIT_SORTS.includes(rawSort as AuditSort)
    ? (rawSort as AuditSort)
    : 'ts_desc'

  const rawPage = Number(searchParams.get('page'))
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1

  const rawPageSize = Number(searchParams.get('pageSize'))
  const pageSize =
    Number.isFinite(rawPageSize) && rawPageSize >= 1
      ? Math.min(Math.floor(rawPageSize), MAX_AUDIT_PAGE_SIZE)
      : DEFAULT_AUDIT_PAGE_SIZE

  return { skills, actions, from, to, q, sort, page, pageSize }
}

/**
 * Compute the default date-range window the page applies when neither
 * `from` nor `to` is in the URL. Lower bound is `nowMs - days * 86400e3`;
 * upper bound is `nowMs`. Returned as ISO strings to match what
 * normalizeIsoTimestamp would have produced from URL input — keeps the
 * downstream filter path uniform.
 *
 * `nowMs` is injectable so tests can pin the window deterministically.
 */
export function defaultAuditDateRange(
  days: number = DEFAULT_AUDIT_RANGE_DAYS,
  nowMs: number = Date.now()
): { from: string; to: string } {
  const upper = new Date(nowMs).toISOString()
  const lower = new Date(nowMs - days * 86400_000).toISOString()
  return { from: lower, to: upper }
}

/**
 * Apply filters to an AuditEntry list in-memory. Exposed for the
 * resolver below and for unit tests. The Hermes bridge will eventually
 * push filtering server-side and this function will only run for
 * in-page post-filtering (or be replaced entirely). For now it is the
 * source of truth so the empty-list contract has tested filter
 * semantics.
 *
 * `q` matches the lowercase substring against `reason || ''`,
 * `actor || ''`, and `target || ''`. Empty strings never match, which
 * is the desired behavior — searching for the empty string is treated
 * as "no search" upstream, so a row whose `reason` is null is included
 * unless filtered out by some other clause.
 */
export function applyAuditFilters(
  rows: readonly AuditEntry[],
  params: AuditListParams
): AuditEntry[] {
  let result = rows.slice()

  if (params.skills.length > 0) {
    const wanted = new Set(params.skills)
    result = result.filter((row) => row.skill !== null && wanted.has(row.skill))
  }

  if (params.actions.length > 0) {
    const wanted = new Set(params.actions)
    result = result.filter((row) => wanted.has(row.action))
  }

  if (params.from !== null) {
    const lowerMs = Date.parse(params.from)
    if (Number.isFinite(lowerMs)) {
      result = result.filter((row) => {
        const rowMs = Date.parse(row.ts)
        return Number.isFinite(rowMs) && rowMs >= lowerMs
      })
    }
  }

  if (params.to !== null) {
    const upperMs = Date.parse(params.to)
    if (Number.isFinite(upperMs)) {
      result = result.filter((row) => {
        const rowMs = Date.parse(row.ts)
        return Number.isFinite(rowMs) && rowMs <= upperMs
      })
    }
  }

  if (params.q !== null) {
    const needle = params.q
    result = result.filter((row) => {
      const reason = (row.reason ?? '').toLowerCase()
      const actor = row.actor.toLowerCase()
      const target = (row.target ?? '').toLowerCase()
      return reason.includes(needle) || actor.includes(needle) || target.includes(needle)
    })
  }

  return result
}

/**
 * Sort a (pre-filtered) AuditEntry list by timestamp. ISO 8601 strings
 * sort lexicographically the same way as their underlying instants for
 * any well-formed value, but the writer is the only authority on
 * timestamp formatting and a malformed value would shift the sort.
 * `Date.parse` keeps the comparison numeric and stable across that
 * edge.
 */
export function applyAuditSort(rows: readonly AuditEntry[], sort: AuditSort): AuditEntry[] {
  const sorted = rows.slice()
  switch (sort) {
    case 'ts_desc':
      sorted.sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts))
      return sorted
    case 'ts_asc':
      sorted.sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts))
      return sorted
  }
}

/** Pagination return value. See {@link Page} (pagination.ts). */
export type AuditListPage = Page<AuditEntry> & {
  /** Oldest Machine-ledger ts actually scanned when the windowed walk hit its
   * page budget with in-window ledger still unread (#2179). Absent/null when
   * the window was fully covered. The page renders a coverage note from it —
   * a truncated walk must never read as "nothing happened before this". */
  machineCoverageFloor?: string | null
}

/**
 * Apply offset-based pagination to a sorted+filtered list. Thin wrapper over
 * the shared {@link paginate}; kept named for call-site + test stability.
 */
export function paginateAuditEntries(
  rows: readonly AuditEntry[],
  page: number,
  pageSize: number
): AuditListPage {
  return paginate(rows, page, pageSize)
}

/**
 * Compose filter → sort → paginate over an in-memory AuditEntry list.
 * Useful for unit tests; the page calls listAuditEntries below.
 */
export function buildAuditListPage(
  rows: readonly AuditEntry[],
  params: AuditListParams
): AuditListPage {
  const filtered = applyAuditFilters(rows, params)
  const sorted = applyAuditSort(filtered, params.sort)
  return paginateAuditEntries(sorted, params.page, params.pageSize)
}

/**
 * Format an ISO timestamp as a compact human-readable string for the
 * audit table's `ts` cell. Returns the input verbatim when it cannot be
 * parsed — never fabricates a "just now" or "unknown" label for a
 * malformed value (the writer is the authority on this field; surfacing
 * the raw value lets reviewers see exactly what the system recorded).
 *
 * Pure function: takes the timestamp string, not a Date, so rendered
 * HTML is deterministic for snapshot tests. Uses Intl.DateTimeFormat in
 * UTC so output does not depend on the rendering machine's locale.
 */
export function formatAuditTimestamp(ts: string): string {
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
 * Human label for an AuditDecision value. Closed vocabulary; unknown
 * values fall through to the raw value rather than fabricating a
 * friendly label.
 */
export function formatAuditDecision(decision: AuditDecision | null): string {
  if (decision === null) return ''
  switch (decision) {
    case 'allow':
      return 'Allowed'
    case 'draft_for_review':
      return 'Drafted for review'
    case 'refuse':
      return 'Refused'
  }
}

/**
 * Tone for the decision chip in the row. `refuse` is danger (the action
 * was blocked), `draft_for_review` is the neutral default (reviewer
 * gate is the standard posture), `allow` is the neutral success tone
 * because allowed actions are the unremarkable bulk of the log.
 *
 * The return values are the `Tone` vocabulary from
 * `src/lib/portal/status.ts` — kept as a string here to avoid a hard
 * import dependency at the resolver layer.
 */
export function decisionTone(decision: AuditDecision | null): 'danger' | 'neutral' | 'success' {
  switch (decision) {
    case 'refuse':
      return 'danger'
    case 'allow':
      return 'success'
    case 'draft_for_review':
    case null:
      return 'neutral'
  }
}

/**
 * Collect the distinct skills present in an audit list, sorted
 * alphabetically and excluding null. Used by the page to populate the
 * skill multi-select in the filter bar from whatever rows the current
 * page returned.
 */
export function distinctAuditSkills(rows: readonly AuditEntry[]): string[] {
  const seen = new Set<string>()
  for (const row of rows) {
    if (row.skill !== null && row.skill.length > 0) {
      seen.add(row.skill)
    }
  }
  return Array.from(seen).sort()
}
