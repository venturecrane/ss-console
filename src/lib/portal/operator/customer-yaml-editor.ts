/**
 * customer.yaml editor — resolver, locked-field policy, diff, and validation.
 *
 * Per ADR 0012, `customer.yaml` lives in a canonical git repository and is
 * the authoritative source of truth. The portal D1 `customer_configs` table
 * is a read replica (see `src/lib/portal/customer-config.ts`). This module
 * sits above that read replica and provides the editor-side surface for
 * the principal-only advanced settings page (#877):
 *
 *   1. A resolver (`resolveEditableConfigFromRow`) that returns the
 *      current configuration projected to the form's shape, with the
 *      customer-side mutable fields broken out from the Captain-only
 *      locked fields. The locked fields are always returned for display
 *      (with a "Captain-managed" badge) but never accepted as input.
 *
 *   2. A locked-field policy that is structural, not a list: the editable
 *      surface is the closed `EditableCustomerConfig` shape, and every
 *      Captain-only field (identity, vertical pinning, machine, memory,
 *      token refs, custody, auth mode, authority) is pulled from the
 *      CURRENT yaml by `lockedFromCurrent`, never from the input. (A
 *      `LOCKED_FIELD_PATHS` list once sat beside this; nothing enforced
 *      through it, and it was removed 2026-09-09.)
 *
 *   3. A merger (`applyEditableChanges`) that takes a current full YAML
 *      and an `EditableCustomerConfig` and returns the next full YAML.
 *      Because of (2), the merger cannot write a locked path even if one
 *      appears in the input — defense in depth against a malformed POST
 *      body smuggling a field name the route forgot to strip.
 *
 *   4. A diff helper (`computeChangedFields`) that produces the list of
 *      JSONPath strings whose values changed between two snapshots. Used
 *      by the audit emission pathway (`customer_yaml_updated`) to record
 *      what shifted without echoing values.
 *
 *   5. A validation pass (`validateEditableChanges`) that surfaces both
 *      lock-violation errors (the input carried a Captain-only field)
 *      and structural errors (after merging, the YAML fails the shared
 *      `src/lib/operator/customer-yaml/` validator).
 *
 * Write-back to git (ADR 0012 §2) is OUT OF SCOPE at v1; the audit log +
 * validation are the customer-side guarantees while the configs-repo
 * write path lands in a follow-on.
 */

import {
  validate,
  type CustomerYaml,
  type Persona,
  type PersonaSkill,
  type ValidationError,
  type ValidationResult,
} from '../../operator/customer-yaml'
import type { CustomerConfigRow } from '../customer-config'
import {
  projectLockedFromRow,
  reconstructFromProjection,
  type ProjectedLockedFields,
} from './customer-config-reconstruct'

// ============================================================================
// Public types — editable + locked surfaces
// ============================================================================

export interface EditablePersonaSkill {
  name: string
  initiation: { manual: boolean; scheduled: boolean; webhook: boolean }
  enabled: boolean
}

export interface EditablePersona {
  slug: string
  status: 'active' | 'archived'
  name: string
  title: string | null
  tone: string[]
  pronouns: 'they/them' | 'he/him' | 'she/her' | null
  send_as: { agentmail_identity: string } | null
  skills: EditablePersonaSkill[]
  channel_bindings: { integration: string; channels: string[] }[]
}

export interface EditableConnector {
  adapter: string
  backend: string
  enabled: boolean
  scopes: string[]
  // token_ref intentionally omitted — locked Captain-only field.
}

export interface EditableEscalation {
  red_flag_recipients: string[]
  failure_recipients: string[]
  acknowledgement_window_minutes: number | null
}

export interface EditableBusinessHours {
  timezone: string
  days: string[]
  start: string
  end: string
}

export interface EditableScope {
  email_folders_visible: string[]
  email_folders_blind: string[]
  email_keyword_blocks: string[]
  domain_blocks: string[]
  matter_blocks: string[]
  inbound_allow_from: string[]
}

export interface EditableCustomerConfig {
  personas: EditablePersona[]
  voiceLibrary: { samples_path: string | null }
  escalation: EditableEscalation
  businessHours: EditableBusinessHours | null
  connectors: Record<string, EditableConnector>
  scope: EditableScope
  logging: { level: string; ship_to: string[] } | null
  pause: { active: boolean; reason: string | null } | null
}

export interface LockedFieldsView {
  schema_version: number
  customer_id: string
  customer_name: string
  vertical: string
  practice_areas: string[]
  fly_region: string
  model: string
  hermes_ref: string
  machine: { size: string; memory_mb: number }
  memory: { d1_namespace: string; r2_vault_path: string; vectorize_index: string }
  connector_token_refs: Record<string, string | null>
}

export interface ResolvedEditableConfig {
  editable: EditableCustomerConfig
  locked: LockedFieldsView
}

// ============================================================================
// Projection — CustomerYaml → editor surface
// ============================================================================

export function projectEditableConfig(yaml: CustomerYaml): ResolvedEditableConfig {
  const connectorTokenRefs: Record<string, string | null> = {}
  const editableConnectors: Record<string, EditableConnector> = {}
  for (const [capability, connector] of Object.entries(yaml.connectors)) {
    if (!connector) continue
    connectorTokenRefs[capability] = connector.token_ref
    editableConnectors[capability] = {
      adapter: connector.adapter,
      backend: connector.backend,
      enabled: connector.enabled,
      scopes: connector.scopes,
    }
  }
  return {
    editable: projectEditable(yaml, editableConnectors),
    locked: projectLocked(yaml, connectorTokenRefs),
  }
}

function projectEditable(
  yaml: CustomerYaml,
  editableConnectors: Record<string, EditableConnector>
): EditableCustomerConfig {
  return {
    personas: yaml.personas.map(projectPersona),
    voiceLibrary: { samples_path: yaml.voice_library?.samples_path ?? null },
    escalation: { ...yaml.escalation },
    businessHours: yaml.business_hours ? { ...yaml.business_hours } : null,
    connectors: editableConnectors,
    scope: { ...yaml.scope },
    logging: yaml.logging ? { level: yaml.logging.level, ship_to: yaml.logging.ship_to } : null,
    pause: yaml.pause ? { active: yaml.pause.active, reason: yaml.pause.reason } : null,
  }
}

function projectLocked(
  yaml: CustomerYaml,
  connectorTokenRefs: Record<string, string | null>
): LockedFieldsView {
  return {
    schema_version: yaml.schema_version,
    customer_id: yaml.customer_id,
    customer_name: yaml.customer_name,
    vertical: yaml.vertical,
    practice_areas: yaml.practice_areas,
    fly_region: yaml.fly_region,
    model: yaml.model,
    hermes_ref: yaml.hermes_ref,
    machine: yaml.machine,
    memory: yaml.memory,
    connector_token_refs: connectorTokenRefs,
  }
}

function projectPersona(p: Persona): EditablePersona {
  return {
    slug: p.slug,
    status: p.status,
    name: p.name,
    title: p.title,
    tone: p.tone,
    pronouns: p.pronouns,
    // The Advanced editor edits an AgentMail identity string only; read it from
    // the normalized send_identity. A non-agentmail (msgraph) identity has no
    // agentmail identity to surface here → null.
    send_as:
      p.send_as && p.send_as.send_identity.provider === 'agentmail'
        ? { agentmail_identity: p.send_as.send_identity.address }
        : null,
    skills: p.skills.map((s: PersonaSkill) => ({
      name: s.name,
      initiation: s.initiation,
      enabled: s.enabled,
    })),
    channel_bindings: p.channel_bindings,
  }
}

// ============================================================================
// Diff — compute changed field paths between two snapshots
// ============================================================================

/**
 * Compute the JSONPath-style paths whose value differs between two
 * `EditableCustomerConfig` snapshots. Section-level granularity:
 * auditors see "this section moved" — values live in git history per
 * ADR 0012.
 */
export function computeChangedFields(
  before: EditableCustomerConfig,
  after: EditableCustomerConfig
): string[] {
  const changed: string[] = []
  diffPersonas(before.personas, after.personas, changed)
  if (before.voiceLibrary.samples_path !== after.voiceLibrary.samples_path) {
    changed.push('voice_library.samples_path')
  }
  diffEscalation(before.escalation, after.escalation, changed)
  if (JSON.stringify(before.businessHours) !== JSON.stringify(after.businessHours)) {
    changed.push('business_hours')
  }
  diffConnectors(before.connectors, after.connectors, changed)
  diffScope(before.scope, after.scope, changed)
  if (JSON.stringify(before.logging) !== JSON.stringify(after.logging)) changed.push('logging')
  if (JSON.stringify(before.pause) !== JSON.stringify(after.pause)) changed.push('pause')
  return changed
}

function diffPersonas(
  before: EditablePersona[],
  after: EditablePersona[],
  changed: string[]
): void {
  if (before.length !== after.length) {
    changed.push('personas.length')
    return
  }
  for (let i = 0; i < after.length; i++) {
    if (JSON.stringify(before[i]) !== JSON.stringify(after[i])) {
      changed.push(`personas[${i}]`)
    }
  }
}

function diffEscalation(
  before: EditableEscalation,
  after: EditableEscalation,
  changed: string[]
): void {
  if (JSON.stringify(before.red_flag_recipients) !== JSON.stringify(after.red_flag_recipients)) {
    changed.push('escalation.red_flag_recipients')
  }
  if (JSON.stringify(before.failure_recipients) !== JSON.stringify(after.failure_recipients)) {
    changed.push('escalation.failure_recipients')
  }
  if (before.acknowledgement_window_minutes !== after.acknowledgement_window_minutes) {
    changed.push('escalation.acknowledgement_window_minutes')
  }
}

function diffConnectors(
  before: Record<string, EditableConnector>,
  after: Record<string, EditableConnector>,
  changed: string[]
): void {
  const keys = new Set([...Object.keys(before), ...Object.keys(after)])
  for (const key of keys) {
    if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) {
      changed.push(`connectors.${key}`)
    }
  }
}

function diffScope(before: EditableScope, after: EditableScope, changed: string[]): void {
  const fields: (keyof EditableScope)[] = [
    'email_folders_visible',
    'email_folders_blind',
    'email_keyword_blocks',
    'domain_blocks',
    'matter_blocks',
    'inbound_allow_from',
  ]
  for (const field of fields) {
    if (JSON.stringify(before[field]) !== JSON.stringify(after[field])) {
      changed.push(`scope.${field}`)
    }
  }
}

// ============================================================================
// Hash — deterministic short fingerprint for audit before/after
// ============================================================================

/**
 * 8-char hex fingerprint of an `EditableCustomerConfig` for the audit
 * payload. FNV-1a 32-bit hash over stable JSON serialization. NOT
 * cryptographic; suitable only for "did this snapshot change?" checks.
 */
export function hashEditableConfig(config: EditableCustomerConfig): string {
  const serialized = JSON.stringify(config)
  let hash = 0x811c9dc5
  for (let i = 0; i < serialized.length; i++) {
    hash ^= serialized.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

// ============================================================================
// Merger — produce next CustomerYaml from current + editor input
// ============================================================================

/**
 * Apply an `EditableCustomerConfig` to a current `CustomerYaml` to
 * produce the next full YAML object. Locked fields are pulled from the
 * current document — the input is IGNORED at those paths even if it
 * carries values. ADR 0011: v1 personas length is exactly 1; the
 * merger truncates the input defensively.
 */
export function applyEditableChanges(
  current: CustomerYaml,
  changes: EditableCustomerConfig
): CustomerYaml {
  const editablePersonas = changes.personas.slice(0, 1)
  const mergedPersonas: Persona[] = current.personas.map((cur, idx) => {
    const next = editablePersonas[idx]
    return next ? mergePersona(cur, next) : cur
  })

  return {
    ...lockedFromCurrent(current),
    personas: mergedPersonas,
    connectors: mergeConnectors(current.connectors, changes.connectors),
    // outbound_roster (ADR 0075) and admins (ADR 0085 §2) are governance-sensitive
    // and NOT portal-editable; preserve the current values verbatim (same posture
    // as voice_cohorts below). admins in particular decides who may establish the
    // firm's voice, so a portal save must never be able to widen or clear it.
    // rule_requests_to (ss#2546) is preserved on the same footing: it is
    // validated as a subset of admins, so a portal save that could edit one
    // without the other could leave a request routed to somebody who is no
    // longer an administrator.
    // ops_reply_from (ss#2546, the operations half) is preserved for a blunter
    // reason: it names whose answer resolves an operations request, so a portal
    // save that could edit it would let the firm decide who at SMD speaks for
    // SMD. It changes through a PR or it does not change.
    scope: {
      ...changes.scope,
      outbound_roster: current.scope.outbound_roster,
      admins: current.scope.admins,
      rule_requests_to: current.scope.rule_requests_to,
      ops_reply_from: current.scope.ops_reply_from,
    },
    // case_alert_routing (#2004) is governance-sensitive (it decides who at
    // the firm receives case alerts) and NOT portal-editable; preserve the
    // current value verbatim (same posture as outbound_roster above).
    escalation: {
      ...changes.escalation,
      case_alert_routing: current.escalation.case_alert_routing,
    },
    voice_library:
      changes.voiceLibrary.samples_path === null
        ? null
        : { samples_path: changes.voiceLibrary.samples_path },
    // voice_cohorts is not user-editable in the portal yet (#857
    // schema lands the field; in-product editor is a follow-on).
    // Preserve the current value verbatim.
    voice_cohorts: current.voice_cohorts,
    // seat: describes what this Operator IS (kind, product). It is an SMD
    // determination about the engagement, never a customer setting — a client
    // must not be able to relabel their production seat as a proving seat and
    // shed the caution that label carries. Preserve verbatim.
    seat: current.seat,
    // output_classes declares, per class, whether an authored spec is EXPECTED
    // (ADR 0083). Raising or dropping that expectation is a commitment change,
    // so it moves through a PR like the rest of customer.yaml. The spec CONTENT
    // the customer authors lives in their own vault object and is edited
    // through the portal — a different key space, a different writer.
    // Preserve verbatim.
    output_classes: current.output_classes,
    business_hours: changes.businessHours,
    logging: changes.logging
      ? {
          level: changes.logging.level as CustomerYaml['logging'] extends infer L
            ? L extends { level: infer V }
              ? V
              : never
            : never,
          ship_to: changes.logging.ship_to as CustomerYaml['logging'] extends infer L
            ? L extends { ship_to: infer V }
              ? V
              : never
            : never,
        }
      : null,
    pause: changes.pause ? { active: changes.pause.active, reason: changes.pause.reason } : null,
    // observability is not user-editable in the portal yet (ADR 0023 Wave 1
    // lands the schema field; in-product editor is a follow-on if customer
    // demand surfaces). Preserve the current value verbatim — the validator
    // default-fills on read so `current.observability` is always non-null.
    observability: current.observability,
    // webhook_triggers is not user-editable in the portal yet (ADR 0021
    // Stream E lands the field; in-product editor is a follow-on).
    // Preserve the current value verbatim.
    webhook_triggers: current.webhook_triggers,
  }
}

function lockedFromCurrent(current: CustomerYaml): Pick<
  CustomerYaml,
  | 'schema_version'
  | 'customer_id'
  | 'customer_name'
  | 'vertical'
  | 'vertical_version'
  | 'addons'
  | 'practice_areas'
  | 'fly_region'
  | 'model'
  | 'escalation_model'
  | 'hermes_ref'
  | 'machine'
  | 'memory'
  | 'users'
  | 'compliance_enabled'
  | 'google_auth'
  | 'authority'
  | 'credential_custody_default'
  | 'mcp_connector'
  | 'relationship'
  | 'digest'
  // governance-sensitive custody acceptance (ADR 0044 D8 / #1841) — never portal-editable
  | 'custody_exceptions'
  // reply send-rate caps (#2070) bound SMD's own exposure on the reply channel;
  // tuning them is an SMD act, not a client one — never portal-editable
  | 'send_policy'
> {
  return {
    schema_version: current.schema_version,
    custody_exceptions: current.custody_exceptions,
    send_policy: current.send_policy,
    customer_id: current.customer_id,
    customer_name: current.customer_name,
    vertical: current.vertical,
    // ADR 0022 Stream 1: vertical pinning and add-ons are locked fields —
    // the portal editor never mutates them. They live in customer.yaml's
    // identity block alongside `vertical:` itself.
    vertical_version: current.vertical_version,
    addons: current.addons,
    practice_areas: current.practice_areas,
    fly_region: current.fly_region,
    model: current.model,
    escalation_model: current.escalation_model,
    hermes_ref: current.hermes_ref,
    machine: current.machine,
    memory: current.memory,
    users: current.users,
    compliance_enabled: current.compliance_enabled,
    // google_auth is not user-editable in the portal (the Google credential
    // mode is a provisioning/setup decision, not a self-serve toggle).
    // Preserve verbatim across portal edits. (#1213)
    google_auth: current.google_auth,
    // authority (ADR 0041) is SMD-set and NEVER client-editable — a client
    // editing their own authority block would be self-granting authority
    // (privilege escalation). The switches are flipped by SMD per domain;
    // the client portal preserves the block verbatim across any self-serve
    // config edit, even when the `configuration` domain is client-operable.
    authority: current.authority,
    // credential_custody_default (ADR 0042) is set at provisioning / via the
    // connectors authority domain, not the general config editor. Preserve.
    credential_custody_default: current.credential_custody_default,
    // mcp_connector (Operator <-> Claude) is provisioning/admin-set, not
    // client-editable in this portal flow yet. Preserve verbatim.
    mcp_connector: current.mcp_connector,
    digest: current.digest,
    // relationship (ADR 0048 authored behavioral lane) is SMD/provisioning-set —
    // per-person working preferences are not a client self-serve config. Preserve
    // verbatim across portal edits.
    relationship: current.relationship,
  }
}

function mergeConnectors(
  current: CustomerYaml['connectors'],
  changes: Record<string, EditableConnector>
): CustomerYaml['connectors'] {
  const merged: CustomerYaml['connectors'] = {}
  for (const [capability, existing] of Object.entries(current)) {
    if (!existing) continue
    const update = changes[capability]
    const key = capability as keyof CustomerYaml['connectors']
    if (!update) {
      merged[key] = existing
      continue
    }
    merged[key] = {
      adapter: update.adapter,
      backend: update.backend,
      enabled: update.enabled,
      scopes: update.scopes,
      // token_ref locked — pulled from current, never from input.
      token_ref: existing.token_ref,
      // webhook_url locked — ADR 0021 Stream E, embeds customer_id so
      // changing it via the editor would risk cross-customer routing.
      // Configured at provisioning time, never via portal.
      webhook_url: existing.webhook_url,
      // credential_custody locked — ADR 0042. Custody is a security decision
      // in the connectors authority domain, set via the dedicated custody flow,
      // never through the general config editor.
      credential_custody: existing.credential_custody,
      // auth_mode locked — set at provisioning with the OAuth flow itself;
      // the portal only READS it (connection care note).
      auth_mode: existing.auth_mode,
      // msgraph_auth / poll_seconds locked — Microsoft Graph mail credentials
      // and poll cadence (ADR 0078 D5) are set at provisioning, never via the
      // general config editor. Preserved verbatim from the current connector.
      msgraph_auth: existing.msgraph_auth,
      poll_seconds: existing.poll_seconds,
    }
  }
  return merged
}

function mergePersona(current: Persona, update: EditablePersona): Persona {
  // Skill list: keep current entries the input did not touch (preserves
  // cost_estimate, scope, version); override initiation + enabled from input.
  // Editor cannot add or remove skills.
  const updateByName = new Map(update.skills.map((s) => [s.name, s]))
  const mergedSkills: PersonaSkill[] = current.skills.map((cur) => {
    const u = updateByName.get(cur.name)
    return u ? { ...cur, initiation: u.initiation, enabled: u.enabled } : cur
  })
  return {
    slug: current.slug,
    status: update.status,
    name: update.name,
    title: update.title,
    tone: update.tone,
    pronouns: update.pronouns,
    // Normalize the editor's AgentMail-only field back into the provider-neutral
    // send_as shape (ADR 0078 §4). Emit only send_identity — the idempotent shape
    // the validator itself produces, so this merged config re-validates cleanly.
    send_as: update.send_as
      ? { send_identity: { provider: 'agentmail', address: update.send_as.agentmail_identity } }
      : null,
    entitlements: current.entitlements,
    channel_bindings: update.channel_bindings,
    skills: mergedSkills,
    signature_html: current.signature_html,
    // The chase-mail signature block is authored, not portal-editable;
    // preserved verbatim like the other authored-only fields here.
    signature: current.signature,
    avatar_url: current.avatar_url,
    voice_overrides: current.voice_overrides,
    escalation_overrides: current.escalation_overrides,
    // bundles + cron are not user-editable in the portal yet (ADR 0021
    // Streams D + B land the schema; in-product editor is a follow-on).
    // Preserve the current values verbatim.
    bundles: current.bundles,
    cron: current.cron,
  }
}

// ============================================================================
// Validation — lock-check + structural validation
// ============================================================================

/**
 * Validate a proposed change. Composes a lock-check (input must not
 * carry any locked field) and a structural pass (the merged YAML must
 * pass the shared `src/lib/operator/customer-yaml/` validator).
 */
export function validateEditableChanges(
  current: CustomerYaml,
  changes: EditableCustomerConfig
): ValidationResult {
  const errors: ValidationError[] = []

  if (changes.personas.length > 1) {
    errors.push({
      code: 'BannedFieldName',
      path: 'personas.length',
      message:
        'personas length cannot exceed 1 at v1 (ADR 0011). Contact Captain to provision a second persona.',
    })
  }

  for (const [capability, connector] of Object.entries(changes.connectors)) {
    const c = connector as unknown as Record<string, unknown>
    if (Object.prototype.hasOwnProperty.call(c, 'token_ref')) {
      errors.push({
        code: 'BannedFieldName',
        path: `connectors.${capability}.token_ref`,
        message: 'token_ref is a Captain-managed field; the customer-side editor cannot change it.',
      })
    }
  }

  if (errors.length > 0) return { ok: false, errors }
  return validate(applyEditableChanges(current, changes))
}

// ============================================================================
// Resolver — read projection from D1, produce editor surface
// ============================================================================

/**
 * The editor surface resolved from a projection row.
 *
 * `locked` is DELIBERATELY not `LockedFieldsView`: that view describes a real
 * `customer.yaml`, where every identity field has an authored value. A
 * projection row carries only some of them, so this one is honest about the
 * gap (see `ProjectedLockedFields`) and the page renders the absent fields as
 * absent rather than as invented ones.
 */
export interface ResolvedEditableConfigFromRow {
  editable: EditableCustomerConfig
  locked: ProjectedLockedFields
}

/**
 * Read the projection from `customer_configs`, validate the reconstructed
 * shape against the shared validator, and project to the editor surface.
 *
 * WHAT IS VALIDATED, and what #1965 got wrong. The reconstruction fills the
 * un-projected identity/runtime fields with structurally-valid, inert
 * placeholders (`customer-config-reconstruct.ts`), so the pass that runs here
 * is effectively a pass over the EDITABLE surface: personas, connectors,
 * scope, escalation, business hours, voice library. The previous
 * reconstruction seeded fields that could not validate — an invalid
 * `hermes_ref` by design, an empty `users`, cron entries missing the
 * `wake_policy` the projection strips — so this call failed for every
 * customer and the editor never rendered.
 *
 * Returns the editor surface on success, or a `{ error }` discriminator when
 * the projection payload is structurally invalid. Callers render the page's
 * error state per `docs/style/empty-state-pattern.md`.
 */
export function resolveEditableConfigFromRow(
  row: CustomerConfigRow
): ResolvedEditableConfigFromRow | { error: 'invalid_projection'; errors: ValidationError[] } {
  const result = validate(reconstructFromProjection(row))
  if (!result.ok) return { error: 'invalid_projection', errors: result.errors }
  const projected = projectEditableConfig(result.value)
  return {
    editable: projected.editable,
    locked: projectLockedFromRow(row, projected.locked.connector_token_refs),
  }
}
