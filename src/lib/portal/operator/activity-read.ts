/**
 * Activity & Audit read path (client-portal §5.5). Bridges the frozen ADR 0043
 * live runtime read (readMachineRuntime) into the existing audit display
 * machinery (filters / sort / pagination / formatters in audit.ts).
 *
 * Why route through readMachineRuntime rather than a per-page resolver in
 * audit.ts: the foundation converges every deep per-customer
 * read on the single audited, fail-closed, one-customer-per-call path
 * (foundations §6, ADR 0043). Activity is one of those drill-ins.
 *
 * In a deployment without OPERATOR_RUNTIME_READ_URL / _SECRET (a fresh
 * environment), the read path is not configured. We short-circuit to an empty
 * page BEFORE calling readMachineRuntime, so an unwired deployment does not
 * write an `unreachable` read-audit row on every page view. In production the
 * path is configured; every read is attempted and audited per ADR 0043.
 *
 * The Machine read endpoint (overlay `shared/runtime_read.py`) serves
 * `audit_log` shaped to this module's `parseAuditEntries` contract — the
 * overlay cites this file by name. Parsing stays defensive (parse, never
 * cast): a malformed row is dropped, never rendered as a misleading line.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { isRecord } from '../../api/helpers'
import { readMachineRuntime, type RuntimeReadActor } from '../../operator/runtime-read'
import { isClientVisibleAction } from './activity-language'
import { listPauseEvents } from './pause-control'
import { listEntitlementChanges } from './entitlement-change'
import { listPortalActionEvents, type PortalActionEventRow } from './action-events'
import {
  createMachineRuntimeTransport,
  createRuntimeReadAudit,
  isRuntimeReadConfigured,
  type RuntimeReadEnv,
} from '../../operator/runtime-read-transport'
import {
  buildAuditListPage,
  AUDIT_ACTOR_ROLES,
  AUDIT_DECISIONS,
  type AuditEntry,
  type AuditActorRole,
  type AuditDecision,
  type AuditListParams,
  type AuditListPage,
} from './audit'

export interface ActivityReadDeps {
  db: D1Database
  env: RuntimeReadEnv
  /** Console-side actor id for the read-audit row (distinct from the operator's log). */
  actorUserId: string
  /** Entity scope for the console-plane unions (logins, team/config actions). */
  entityId: string
}

/**
 * Load one filtered/paginated page of activity for the signed-in client's own
 * operator. Fails closed to an empty page on any transport failure or when the
 * read path is not configured — never throws into the page render.
 */
export async function loadActivityPage(
  deps: ActivityReadDeps,
  customerSlug: string,
  actor: RuntimeReadActor,
  params: AuditListParams
): Promise<AuditListPage> {
  // Console-plane unions (#2003 Q6 and the portal-accountability slice): the
  // Machine ledger cannot carry console-side governance events (the broker
  // PID-gates appends to the gateway process), so the client-readable record
  // is Machine ledger ∪ operator_pause_events ∪ portal_login_events ∪
  // portal_action_events. Each loader is defensive: a missing table (fresh
  // environment) contributes nothing rather than blanking the page. These
  // rows do not depend on the Machine read path, so they render even when
  // runtime read is not configured.
  const pauseRows = await loadPauseEventEntries(deps.db, customerSlug)
  const entitlementRows = await loadEntitlementChangeEntries(deps.db, customerSlug)
  const loginRows = await loadLoginEventEntries(deps.db, deps.entityId)
  const actionRows = await loadActionEventEntries(deps.db, deps.entityId, customerSlug)
  const consoleRows = [...pauseRows, ...entitlementRows, ...loginRows, ...actionRows]

  if (!isRuntimeReadConfigured(deps.env)) {
    return buildAuditListPage(consoleRows, params)
  }
  // Walk the Machine ledger across the requested window rather than taking one
  // slice of it (#2179). The single unpaginated fetch starved the client feed:
  // the newest page of a working seat is dominated by suppressed telemetry
  // (tool calls, turns), so the slice could contain zero client-visible events
  // while dozens sat just behind it — live-proven 2026-08-02 (56 mapped events
  // in-window, page rendered none, seam ok; vfy_01KZ204YY88HD2VJ2ZXNW85JDV).
  const machine = await collectMachineWindow(deps, customerSlug, actor, params.from ?? null)
  // Curated client language only (Captain decision 7): entries without
  // authored client copy never reach the page, regardless of filters.
  const clientRows = machine.rows.filter((r) => isClientVisibleAction(r.action))
  const page = buildAuditListPage([...clientRows, ...consoleRows], params)
  return { ...page, machineCoverageFloor: machine.coverageFloor }
}

/** Overlay `shared/runtime_read.py` clamps `limit` to this (MAX_LIMIT). */
const MACHINE_PAGE_LIMIT = 200
/** Pages walked per view before declaring a coverage floor: 15 × 200 = 3,000
 * ledger rows, ~2.5 weeks of the pilot's current volume for a 7-day window.
 * A budget (not a loop-until-done) bounds page latency against a runaway
 * seat; exhausting it is surfaced honestly, never silently. */
const MACHINE_PAGE_BUDGET = 15

/**
 * Collect Machine-ledger entries newest-first until the window's `from` bound
 * is passed, the ledger is exhausted, or the page budget runs out.
 *
 * The seam (overlay `_read_audit_log`) keyset-paginates `ORDER BY id DESC`
 * with `WHERE id < cursor`, advertising a next cursor only on a full page —
 * ULIDs sort by time, so once a page's oldest `ts` precedes `from`, every
 * later page is older still and the walk stops.
 *
 * One LOGICAL read, one read-audit row: the first segment is audited per
 * ADR 0043 (attempt + outcome); continuation segments of the same walk reuse
 * a no-op sink rather than writing N rows per page view. A first-segment
 * failure is already recorded before the fail-closed empty return.
 *
 * `coverageFloor` is the oldest ts actually scanned when the budget ran out
 * with in-window ledger still unread — the page states the floor rather than
 * letting a truncated walk read as "nothing happened before this".
 */
async function collectMachineWindow(
  deps: ActivityReadDeps,
  customerSlug: string,
  actor: RuntimeReadActor,
  from: string | null
): Promise<MachineWindow> {
  return walkMachineWindow(
    {
      transport: createMachineRuntimeTransport(deps.env),
      audit: createRuntimeReadAudit(deps.db, { actorUserId: deps.actorUserId }),
    },
    customerSlug,
    actor,
    from
  )
}

export interface MachineWindow {
  rows: AuditEntry[]
  coverageFloor: string | null
}

/** The injectable core of the windowed walk. Exported for unit testing at the
 * same transport boundary the runtime-read tests use. */
export async function walkMachineWindow(
  deps: Parameters<typeof readMachineRuntime>[0],
  customerSlug: string,
  actor: RuntimeReadActor,
  from: string | null
): Promise<MachineWindow> {
  const noopAudit = { record: async () => {} }

  const rows: AuditEntry[] = []
  let cursor: string | null = null
  for (let segment = 0; segment < MACHINE_PAGE_BUDGET; segment++) {
    const result = await readMachineRuntime(
      { transport: deps.transport, audit: segment === 0 ? deps.audit : noopAudit },
      customerSlug,
      { kind: 'audit_log', limit: MACHINE_PAGE_LIMIT, ...(cursor !== null ? { cursor } : {}) },
      actor
    )
    if (!result.ok) return { rows, coverageFloor: null }
    const parsed = parseAuditEntries(result.data)
    if (parsed.length === 0) return { rows, coverageFloor: null }
    rows.push(...parsed)

    const oldest = parsed[parsed.length - 1].ts
    // ISO timestamps compare lexicographically; a date-only `from` sorts
    // before any same-day timestamp, so entries ON the from-date survive.
    if (from !== null && oldest < from) return { rows, coverageFloor: null }

    cursor = nextCursorOf(result.data)
    if (cursor === null) return { rows, coverageFloor: null }
  }
  // Budget exhausted with ledger (and possibly window) still unread.
  return { rows, coverageFloor: rows.length > 0 ? rows[rows.length - 1].ts : null }
}

/** The seam envelope's next-page cursor, defensively. Absent/malformed → null
 * (treated as exhausted — fail toward a shorter honest feed, never a loop). */
function nextCursorOf(data: unknown): string | null {
  if (!isRecord(data)) return null
  const cursor = data['cursor']
  return typeof cursor === 'string' && cursor.length > 0 ? cursor : null
}

/**
 * Map the operator_pause_events governance rows into AuditEntry shape so the
 * activity page renders one unified record. AGENT_STOPPED / AGENT_RESUMED
 * carry authored client copy in activity-language.ts.
 */
async function loadPauseEventEntries(db: D1Database, customerSlug: string): Promise<AuditEntry[]> {
  try {
    const events = await listPauseEvents(db, customerSlug)
    return events.map((e) => ({
      id: `pause:${e.id}`,
      ts: e.created_at,
      actor: e.actor_email,
      actorRole: asActorRole(e.actor_role),
      action: e.action === 'pause' ? 'AGENT_STOPPED' : 'AGENT_RESUMED',
      target: null,
      decision: null,
      reason: e.reason,
      skill: null,
    }))
  } catch {
    return []
  }
}

/**
 * Map operator_entitlement_changes into AuditEntry shape (#2003 Q7 — "every
 * change is logged with who made it and when"). ENTITLEMENT_CHANGED carries
 * authored client copy in activity-language.ts; the routine and tier movement
 * ride the target field.
 */
async function loadEntitlementChangeEntries(
  db: D1Database,
  customerSlug: string
): Promise<AuditEntry[]> {
  try {
    const events = await listEntitlementChanges(db, customerSlug)
    return events.map((e) => ({
      id: `entitlement:${e.id}`,
      ts: e.created_at,
      actor: e.actor_email,
      actorRole: asActorRole(e.actor_role),
      action: 'ENTITLEMENT_CHANGED',
      target: `${e.routine}: ${e.from_tier} → ${e.to_tier}`,
      decision: null,
      reason: e.reason,
      skill: null,
    }))
  } catch {
    return []
  }
}

/**
 * Map portal_login_events into AuditEntry shape. PORTAL_LOGIN carries
 * authored client copy in activity-language.ts. Entity-scoped: an entity's
 * sign-ins show on all of its operator instances (portal access spans the
 * entity, not one instance).
 */
async function loadLoginEventEntries(db: D1Database, entityId: string): Promise<AuditEntry[]> {
  try {
    const res = await db
      .prepare(
        'SELECT id, email, created_at FROM portal_login_events ' +
          'WHERE entity_id = ? ORDER BY created_at DESC LIMIT 50'
      )
      .bind(entityId)
      .all<{ id: string; email: string; created_at: string }>()
    return (res.results ?? []).map((e) => ({
      id: `login:${e.id}`,
      ts: e.created_at,
      actor: e.email,
      actorRole: null,
      action: 'PORTAL_LOGIN',
      target: null,
      decision: null,
      reason: null,
      skill: null,
    }))
  } catch {
    return []
  }
}

/** Console action → synthetic feed action. Kept out of AUDIT_ACTION_TYPES
 * (Machine writer vocabulary, Python-parity-tested); see CONSOLE_ACTION_TYPES
 * in audit.ts. */
const ACTION_EVENT_FEED_MAP: Record<PortalActionEventRow['action_type'], string> = {
  role_granted: 'TEAM_ROLE_GRANTED',
  role_revoked: 'TEAM_ROLE_REVOKED',
  invite_sent: 'TEAM_INVITE_SENT',
  customer_yaml_update_submitted: 'CONFIG_CHANGE_SUBMITTED',
  connector_reconsent_requested: 'CONNECTOR_RECONSENT_REQUESTED',
  output_class_spec_authored: 'OUTPUT_SPEC_AUTHORED',
  compliance_record_exported: 'COMPLIANCE_RECORD_EXPORTED',
}

/** The two action types whose rejected attempts have their own client copy —
 *  a refused change reads differently from an accepted one, and both are
 *  recorded. Types absent here fall back to their accepted label. */
const REJECTED_FEED_MAP: Partial<Record<PortalActionEventRow['action_type'], string>> = {
  customer_yaml_update_submitted: 'CONFIG_CHANGE_REJECTED',
  output_class_spec_authored: 'OUTPUT_SPEC_REJECTED',
}

/**
 * Map portal_action_events into AuditEntry shape. A rejected customer.yaml
 * submission surfaces as CONFIG_CHANGE_REJECTED; role events carry the role
 * in the reason cell (parsed defensively from metadata).
 *
 * Seat-scoped as well as entity-scoped (#2281): these events are written with
 * the instance they were performed on, so a sibling seat must not show them.
 */
async function loadActionEventEntries(
  db: D1Database,
  entityId: string,
  customerSlug: string
): Promise<AuditEntry[]> {
  try {
    const events = await listPortalActionEvents(db, entityId, customerSlug)
    return events.map((e) => {
      const action =
        (e.status === 'rejected' ? REJECTED_FEED_MAP[e.action_type] : undefined) ??
        ACTION_EVENT_FEED_MAP[e.action_type]
      return {
        id: `action:${e.id}`,
        ts: e.created_at,
        actor: e.actor_email,
        actorRole: asActorRole(e.actor_role),
        action,
        target: e.target,
        decision: null,
        reason: roleFromMetadata(e),
        skill: null,
      }
    })
  } catch {
    return []
  }
}

/** For role events, surface the granted/revoked role. Malformed metadata → null. */
function roleFromMetadata(e: PortalActionEventRow): string | null {
  if (e.action_type !== 'role_granted' && e.action_type !== 'role_revoked') return null
  try {
    const parsed: unknown = JSON.parse(e.metadata_json)
    if (!isRecord(parsed)) return null
    const role = parsed['role']
    return typeof role === 'string' && role.length > 0 ? `Role: ${role}` : null
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// Defensive parsing of the runtime read payload into AuditEntry[].
// ---------------------------------------------------------------------------

/** A required non-empty string field; returns null when absent/empty so the
 * caller can drop a malformed row rather than render a blank cell. */
function reqString(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null
}

/** An optional string field: a present string or null (anything else → null). */
function optString(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null
}

function asActorRole(v: unknown): AuditActorRole | null {
  return typeof v === 'string' && (AUDIT_ACTOR_ROLES as readonly string[]).includes(v)
    ? (v as AuditActorRole)
    : null
}

function asDecision(v: unknown): AuditDecision | null {
  return typeof v === 'string' && (AUDIT_DECISIONS as readonly string[]).includes(v)
    ? (v as AuditDecision)
    : null
}

/**
 * Parse an unknown runtime payload into AuditEntry[]. Accepts either a bare
 * array of rows or an `{ entries: [...] }` envelope. Rows missing a required
 * `id`/`ts`/`actor`/`action` are dropped, not coerced — a malformed row never
 * becomes a misleading audit line. Exported for unit testing.
 */
export function parseAuditEntries(data: unknown): AuditEntry[] {
  const raw: unknown = isRecord(data) && Array.isArray(data['entries']) ? data['entries'] : data
  if (!Array.isArray(raw)) return []
  const out: AuditEntry[] = []
  for (const item of raw) {
    if (!isRecord(item)) continue
    const id = reqString(item['id'])
    const ts = reqString(item['ts'])
    const actor = reqString(item['actor'])
    const action = reqString(item['action'])
    if (id === null || ts === null || actor === null || action === null) continue
    out.push({
      id,
      ts,
      actor,
      action,
      actorRole: asActorRole(item['actorRole']),
      target: optString(item['target']),
      decision: asDecision(item['decision']),
      reason: optString(item['reason']),
      skill: optString(item['skill']),
    })
  }
  return out
}
