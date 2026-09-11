/**
 * Customer config — portal read path for the projection of `customer.yaml`.
 *
 * Per ADR 0012, `customer.yaml` lives in a canonical git repository and is
 * projected on every merge into two read replicas:
 *
 *   1. portal D1 `customer_configs` table  (this module reads from here)
 *   2. per-customer R2 prefix              (Hermes reads from there)
 *
 * Neither replica is hand-edited. Drift detection (a daily cron, lands in a
 * follow-on PR) reconciles them against git. This module is read-only on
 * principle: any write path that bypasses git breaks the source-of-truth
 * commitment in ADR 0012 §2.
 *
 * Per ADR 0011, `personas` is an array (length ≥1 at v1, with the v1
 * customer's sole persona at index 0). `getActivePersona` returns the first
 * persona whose `status === 'active'`. Phase 2 extends the selector when a
 * second persona ships — the function signature stays additive (Phase 2 adds
 * an optional selector parameter; v1 callers continue to work).
 */

import { parseMcpConnector } from '../operator/mcp/connector-projection'
import { parseAuthorityPosture, type AuthorityPosture } from '../operator/authority'
import { validateRoutineGrid, type RoutineGrid } from '../operator/routine-grid'
import {
  DEFAULT_CREDENTIAL_CUSTODY,
  parseCredentialCustody,
  type CredentialCustody,
} from '../operator/credential-custody'
import type {
  McpConnector,
  PersonaEntitlements,
  SkillInitiation,
} from '../operator/customer-yaml/types'

export type PersonaStatus = 'active' | 'archived'

export interface PersonaSendAs {
  /**
   * Provider-neutral send-as identity (ADR 0078 §4). Populated on every row
   * projected after 2026-07-24; OPTIONAL here because pre-migration projected
   * rows carry only `agentmail_identity` (the read side casts JSON without
   * re-validating the sub-shape). Readers fall back to `agentmail_identity`.
   */
  send_identity?: { provider: 'agentmail' | 'msgraph'; address: string }
  /** @deprecated Legacy AgentMail-only field. Read `send_identity`. */
  agentmail_identity?: string
}

/**
 * Resolve a persona's send-as email address from the provider-neutral
 * `send_identity` (ADR 0078 §4), falling back to the deprecated
 * `agentmail_identity` so pre-migration projected rows still resolve. The one
 * place readers should ask "what does this persona send from?".
 */
export function personaSendAsAddress(sendAs: PersonaSendAs | null | undefined): string | null {
  return sendAs?.send_identity?.address ?? sendAs?.agentmail_identity ?? null
}

export interface PersonaSkill {
  name: string
  initiation: SkillInitiation
  /**
   * Authored scalar knobs (ADR 0075) the runtime reads verbatim — projected
   * so the portal (config source of truth to the client, correspondence 08)
   * can render the firm's confirmed numbers, e.g. the #2005 pair. Rows
   * projected before this field existed parse as absent (defensive read side).
   */
  settings?: Record<string, string | number | boolean>
}

export interface PersonaChannelBinding {
  integration: string
  channels: string[]
}

/** One projected cron entry: WHEN a skill runs (schedule detail only). */
export interface PersonaCronEntry {
  skill: string
  schedule: string
}

export interface PersonaConfig {
  slug: string
  status: PersonaStatus
  name: string
  title: string | null
  signature_html: string | null
  tone: string[]
  send_as: PersonaSendAs | null
  entitlements: PersonaEntitlements
  skills: PersonaSkill[]
  /**
   * Projected cron schedules (console blueprint §4 schedule coverage). Rows
   * projected before this field existed parse as [] (defensive read side).
   */
  cron?: PersonaCronEntry[]
  channel_bindings: PersonaChannelBinding[]
}

export interface CustomerConfigRow {
  entity_id: string
  org_id: string
  customer_slug: string
  schema_version: string
  personas: PersonaConfig[]
  voice_library: unknown
  /**
   * What the seat IS — `{kind, product}` — or null when unauthored (ADR 0083).
   * Carries no lifecycle state by construction: whether a seat is connected or
   * serving is answered by probing the running system, never by this row.
   */
  seat: unknown
  /**
   * Per-output-class declaration of whether an authored spec is EXPECTED.
   * `null` means the customer declared nothing here — NOT that no spec is
   * expected. A class declaring `expected` whose spec is missing fails closed;
   * collapsing the two would let a broken sync read as a deliberate choice.
   */
  output_classes: unknown
  escalation: unknown
  business_hours: unknown
  connectors: unknown
  scope: unknown
  /**
   * Projected from `customer.yaml.compliance_enabled` per issue #895.
   * When `false`, the dedicated Compliance dashboard view does not
   * render even for users who hold the `compliance` product_role —
   * the firm has not opted in to the separation-of-duties posture this
   * view represents. RBAC on the existing audit surface is independent
   * of this flag.
   */
  compliance_enabled: boolean
  /**
   * Projected from `customer.yaml.vertical` per issue #895. Drives the
   * per-vertical audit retention default surfaced on the Compliance
   * dashboard. Distinct from the prospect-side `entities.vertical`
   * field (different taxonomy, different purpose). Nullable so the row
   * can backfill before CI sync writes the column.
   */
  vertical: string | null
  /**
   * Resolved authority posture (ADR 0041) — per-domain client-self-serve
   * switches over SMD's always-present full control. Always present: a null
   * `authority_json` column resolves to the launch default
   * (`{ default: 'managed', overrides: {} }`) via parseAuthorityPosture, so
   * portals never special-case absence. The resolver + domain contract live
   * in src/lib/operator/authority.ts.
   */
  authority: AuthorityPosture
  /**
   * Resolved client-level default credential custody (ADR 0042). Always
   * present: a null `credential_custody_default` column resolves to
   * `delegated`. Per-connector overrides live inside `connectors`. The
   * resolver `resolveCredentialCustody` lives in
   * src/lib/operator/credential-custody.ts.
   */
  credential_custody_default: CredentialCustody
  /**
   * Resolved `mcp_connector` block (Operator ⇄ Claude connector, Phase 1) —
   * projected from `customer.yaml.mcp_connector`. Always present: a null
   * `mcp_connector_json` column (a row predating the column, or a customer.yaml
   * with no block) resolves to the fail-closed default (disabled, empty access)
   * via {@link parseMcpConnector}. The MCP endpoint reads this for the per-user
   * `access[]` mapping; the Clerk binding lives separately in mcp_clerk_bindings.
   */
  mcp_connector: McpConnector
  /**
   * Resolved routine grid (ADR 0075) — the compiled per-routine autonomy
   * traceability the console "the work" chapter renders. Projected from the
   * seat's routine-grid.yaml (when one exists next to customer.yaml).
   *
   * NULLABLE BY DESIGN, and resolved with a DELIBERATELY DIFFERENT posture
   * from personas (which throws): a seat with no grid, a null column, or a
   * malformed projected value all resolve to null via {@link resolveRoutineGrid}.
   * A bad grid must degrade to the gridless console fallback, never 500 the
   * live portal — see the resolver's contract.
   */
  routine_grid: RoutineGrid | null
  git_sha: string
  synced_at: string
}

export interface CustomerConfigDbRow {
  entity_id: string
  org_id: string
  customer_slug: string
  schema_version: string
  personas_json: string
  voice_library_json: string | null
  seat_json: string | null
  output_classes_json: string | null
  escalation_json: string | null
  business_hours_json: string | null
  connectors_json: string | null
  scope_json: string | null
  compliance_enabled: number
  vertical: string | null
  authority_json: string | null
  credential_custody_default: string | null
  mcp_connector_json: string | null
  routine_grid_json: string | null
  git_sha: string
  synced_at: string
}

/**
 * Parse a JSON column that may be null. Returns `null` when the column is
 * null. Throws on malformed JSON — drift detection / CI sync are responsible
 * for ensuring the projection is well-formed; a malformed JSON column is a
 * corruption signal, not a routine condition to recover from.
 */
function parseJsonNullable<T>(value: string | null | undefined): T | null {
  // null = column is SQL NULL; undefined = column absent from the row (e.g. a
  // freshly-added projection column a row predates, or a partial test row).
  // Both mean "no value" — only a present, malformed JSON string is corruption.
  if (value === null || value === undefined) return null
  return JSON.parse(value) as T
}

/**
 * Parse a required JSON column. Throws on null OR malformed JSON — both are
 * corruption signals at the projection layer.
 */
function parseJsonRequired<T>(value: string, column: string, entityId: string): T {
  try {
    return JSON.parse(value) as T
  } catch (err) {
    throw new Error(
      `customer_configs.${column} is malformed JSON for entity_id=${entityId}: ${
        err instanceof Error ? err.message : String(err)
      }`,
      { cause: err }
    )
  }
}

// The projected mcp_connector_json parser moved down to
// src/lib/operator/mcp/connector-projection.ts on 2026-09-10 so the MCP
// customer resolver no longer imports upward from this portal module.
// Re-exported because this is the name every caller and test already uses.
export { parseMcpConnector }

/**
 * Resolve the projected `routine_grid_json` column into a runtime `RoutineGrid`.
 *
 * DELIBERATELY FAIL-SOFT, unlike `parseJsonRequired` (used for personas_json,
 * which THROWS): a null column, malformed JSON, or a value that fails the
 * routine-grid validator all resolve to `null` rather than throwing. Two
 * reasons, the same shape as `parseMcpConnector`:
 *   1. This is read on the live client portal. A corrupt grid must not 500 the
 *      page — it degrades to the gridless console fallback (ADR 0075, the
 *      console "the work" chapter renders nothing rather than crashing).
 *   2. A seat that has never authored a routine-grid.yaml is the common case,
 *      not corruption: absence is a first-class "no grid" state.
 *
 * Note this catches malformed JSON, which `parseJsonNullable` does not — the
 * catch is what makes "never throw" hold for an arbitrary stored string.
 */
export function resolveRoutineGrid(json: string | null | undefined): RoutineGrid | null {
  if (json === null || json === undefined) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  const result = validateRoutineGrid(raw)
  return result.ok ? result.value : null
}

export function projectRow(row: CustomerConfigDbRow): CustomerConfigRow {
  return {
    entity_id: row.entity_id,
    org_id: row.org_id,
    customer_slug: row.customer_slug,
    schema_version: row.schema_version,
    personas: parseJsonRequired<PersonaConfig[]>(row.personas_json, 'personas_json', row.entity_id),
    voice_library: parseJsonNullable(row.voice_library_json),
    seat: parseJsonNullable(row.seat_json),
    output_classes: parseJsonNullable(row.output_classes_json),
    escalation: parseJsonNullable(row.escalation_json),
    business_hours: parseJsonNullable(row.business_hours_json),
    connectors: parseJsonNullable(row.connectors_json),
    scope: parseJsonNullable(row.scope_json),
    compliance_enabled: row.compliance_enabled === 1,
    vertical: row.vertical,
    // JSON-syntax corruption throws like any other projected column; semantic
    // shape (unknown override keys/values) is tolerated by parseAuthorityPosture,
    // and a null column resolves to the launch-default posture.
    authority: parseAuthorityPosture(parseJsonNullable(row.authority_json)),
    credential_custody_default:
      parseCredentialCustody(row.credential_custody_default) ?? DEFAULT_CREDENTIAL_CUSTODY,
    mcp_connector: parseMcpConnector(row.mcp_connector_json),
    // Fail-soft to null (NOT the throwing personas posture) — see resolveRoutineGrid.
    routine_grid: resolveRoutineGrid(row.routine_grid_json),
    git_sha: row.git_sha,
    synced_at: row.synced_at,
  }
}

/**
 * Read the projected customer config for an entity. Returns null when no row
 * exists — a meaningful state during alpha when CI sync has not been wired
 * up yet (rows are hand-seeded only).
 *
 * LEGACY / single-instance only. Since migration 0090 an entity may own more
 * than one operator config (the multi-operator model), so this `.first()` read
 * returns an ARBITRARY row for a multi-config entity. New operator surfaces must
 * address the instance by slug via {@link getCustomerConfigBySlug}; this remains
 * only for the (still 1:1) callers that key on the entity alone.
 */
export async function getCustomerConfig(
  db: D1Database,
  entityId: string
): Promise<CustomerConfigRow | null> {
  const row = await db
    .prepare('SELECT * FROM customer_configs WHERE entity_id = ?')
    .bind(entityId)
    .first<CustomerConfigDbRow>()
  if (!row) return null
  return projectRow(row)
}

/**
 * Read the projected customer config for a specific operator instance, addressed
 * by its `customer_slug` (the instance identity since migration 0090). This is
 * the read every instance-addressed operator surface uses. Returns null when no
 * such config exists.
 *
 * The CALLER is responsible for the ownership check — verify the returned
 * `entity_id` matches the signed-in client's entity before rendering, so a user
 * cannot view another client's operator by guessing a slug. `resolveOperatorAccess`
 * enforces this centrally.
 */
export async function getCustomerConfigBySlug(
  db: D1Database,
  customerSlug: string
): Promise<CustomerConfigRow | null> {
  const row = await db
    .prepare('SELECT * FROM customer_configs WHERE customer_slug = ?')
    .bind(customerSlug)
    .first<CustomerConfigDbRow>()
  if (!row) return null
  return projectRow(row)
}

/**
 * List every projected operator config owned by an entity, oldest first (stable
 * order for nav/home listing). One row per operator instance the client owns
 * (multi-operator model, migration 0090). Empty array when the entity owns none.
 */
export async function listCustomerConfigsForEntity(
  db: D1Database,
  entityId: string
): Promise<CustomerConfigRow[]> {
  const { results } = await db
    .prepare('SELECT * FROM customer_configs WHERE entity_id = ? ORDER BY created_at ASC')
    .bind(entityId)
    .all<CustomerConfigDbRow>()
  return (results ?? []).map(projectRow)
}

// ===========================================================================
// customer_config_history — ADR 0022 Stream 3 (substrate gap B)
// ===========================================================================
//
// The history table records materialization events independently of git.
// Wherever the actual sync code path lives (CI workflow, drift-repair cron,
// manual portal action, bootstrap script), it should call
// `recordCustomerConfigSync` after writing customer_configs so the audit
// trail accumulates from day one.
//
// PR 3 ships the substrate (table + helpers + admin page). The sync code
// path itself lives where it already lives — this module does not own it.

/**
 * Source of a customer_config_history event. Mirrors the SyncSource enum
 * exported from src/lib/operator/customer-yaml (added in PR 1). Kept as
 * a literal-union here so the portal-side modules don't need to pull in
 * the validator just for the enum.
 */
export type SyncSource = 'manual' | 'ci' | 'drift-repair' | 'bootstrap'

export interface CustomerConfigHistoryRow {
  id: number
  customer_slug: string
  git_sha: string
  synced_at: string
  synced_by: SyncSource
  actor: string | null
  prev_git_sha: string | null
  r2_shadow_key: string | null
  created_at: string
}

/**
 * List the most-recent N history rows for a customer slug. Powers the
 * admin /admin/operator/config-history/<slug>.astro page.
 */
export async function listCustomerConfigHistory(
  db: D1Database,
  customerSlug: string,
  limit = 20
): Promise<CustomerConfigHistoryRow[]> {
  const { results } = await db
    .prepare(
      'SELECT id, customer_slug, git_sha, synced_at, synced_by, actor, ' +
        'prev_git_sha, r2_shadow_key, created_at ' +
        'FROM customer_config_history ' +
        'WHERE customer_slug = ? ORDER BY synced_at DESC LIMIT ?'
    )
    .bind(customerSlug, limit)
    .all<CustomerConfigHistoryRow>()
  return results ?? []
}
