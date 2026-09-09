/**
 * Provisioning authoring + validation for the admin Operator console (§4.5).
 *
 * Stands up a new operator. The console's job is steps 1-3 of the §4.5 flow:
 * pick a vertical pack, author the customer.yaml essentials, and validate them
 * with the existing validator + secret-exclusion scan. Step 4+ (commit to the
 * configs repo, Machine stand-up, connector consent, subscription activation)
 * is the existing provisioning tooling (`operator/bin/provision-customer.sh`,
 * being made agent-runnable on a separate track, #1262) — NOT a Worker
 * capability. A Cloudflare Worker cannot write the git source of truth or call
 * Fly; git write-back is explicitly OUT OF SCOPE at v1 (ADR 0012 §2, mirrored by
 * the customer-side customer-yaml-update endpoint).
 *
 * So this module is honest about the seam: it ASSEMBLES a candidate customer.yaml
 * from the authored essentials, VALIDATES the parsed object (and scans the
 * rendered text for leaked secrets) via the real `validate()`, and emits the
 * validated YAML text as the deliverable the operator drops into
 * `operator/customers/<slug>/customer.yaml` before running the stand-up tooling.
 * The attempt is recorded as provisioning INTENT (its own ledger) — at this
 * point no entity_id exists yet (the customer is being created), so the ledger
 * keys on the proposed customer_id and carries no entities() FK.
 *
 * Pure assembly/validation/serialization helpers + the append-only intent
 * writer live here; the admin page is the only caller and is admin-gated.
 *
 * What the vertical pick seeds vs. what the admin authors: a Worker cannot read
 * the on-disk pack manifests (`operator/verticals/<v>/vertical.yaml`), so the
 * pack's personas/skills/connectors are materialized from the pack by the
 * stand-up tooling, not re-typed here. The pick drives the vertical + add-on
 * selection (real constants); the admin authors identity, the primary persona,
 * the primary user, and the connector capability set per §4.5 step 2. Per-domain
 * authority flips happen on the §5.9 authority surface post-provision; the
 * candidate omits an `authority:` block, which the validator resolves to the
 * fail-closed all-managed default (DEFAULT_AUTHORITY_POSTURE).
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  validate,
  ACCEPTED_VERTICALS,
  ACCEPTED_EXPOSURE_CEILINGS,
  ACCEPTED_USER_ROLES,
  type ValidationResult,
} from '../operator/customer-yaml'
import { ACCEPTED_CAPABILITY_NAMES } from '../operator/customer-yaml/types'
import type { CapabilityName } from '../operator/capabilities/types'

// ---------------------------------------------------------------------------
// Authored input
// ---------------------------------------------------------------------------

export interface ConnectorInput {
  capability: string
  adapter: string
  backend: string
  enabled: boolean
}

export interface ProvisioningInput {
  customer_id: string
  customer_name: string
  vertical: string
  addons: string[]
  practice_areas: string[]
  fly_region: string
  model: string
  hermes_ref: string
  machine_size: string
  machine_memory_mb: number
  user_email: string
  user_role: string
  user_full_name: string
  persona_slug: string
  persona_name: string
  persona_title: string
  persona_tone: string[]
  skill_name: string
  exposure_level: string
  connectors: ConnectorInput[]
}

/** Split a textarea/CSV field into a trimmed, non-empty string list. */
export function splitList(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

function str(form: FormData, key: string): string {
  const v = form.get(key)
  return typeof v === 'string' ? v.trim() : ''
}

/**
 * Parse the provisioning form into a typed input. Never casts untrusted values:
 * unparseable numbers become NaN so the validator reports them rather than a
 * coerced default silently passing. Connector rows are read positionally from
 * the parallel `connector_*[]` field arrays.
 */
export function parseProvisioningForm(form: FormData): ProvisioningInput {
  const memRaw = str(form, 'machine_memory_mb')
  const memNum = Number.parseInt(memRaw, 10)
  return {
    customer_id: str(form, 'customer_id'),
    customer_name: str(form, 'customer_name'),
    vertical: str(form, 'vertical'),
    addons: form.getAll('addons').filter((a): a is string => typeof a === 'string' && a.length > 0),
    practice_areas: splitList(str(form, 'practice_areas')),
    fly_region: str(form, 'fly_region'),
    model: str(form, 'model'),
    hermes_ref: str(form, 'hermes_ref'),
    machine_size: str(form, 'machine_size'),
    machine_memory_mb: Number.isFinite(memNum) ? memNum : Number.NaN,
    user_email: str(form, 'user_email'),
    user_role: str(form, 'user_role'),
    user_full_name: str(form, 'user_full_name'),
    persona_slug: str(form, 'persona_slug'),
    persona_name: str(form, 'persona_name'),
    persona_title: str(form, 'persona_title'),
    persona_tone: splitList(str(form, 'persona_tone')),
    skill_name: str(form, 'skill_name'),
    exposure_level: str(form, 'exposure_level'),
    connectors: parseConnectorRows(form),
  }
}

function parseConnectorRows(form: FormData): ConnectorInput[] {
  const caps = form.getAll('connector_capability')
  const adapters = form.getAll('connector_adapter')
  const backends = form.getAll('connector_backend')
  const out: ConnectorInput[] = []
  for (let i = 0; i < caps.length; i++) {
    const capability = typeof caps[i] === 'string' ? (caps[i] as string).trim() : ''
    const adapter = typeof adapters[i] === 'string' ? (adapters[i] as string).trim() : ''
    const backend = typeof backends[i] === 'string' ? (backends[i] as string).trim() : ''
    // A wholly blank row is an unfilled form slot, not a connector — skip it.
    if (!capability && !adapter && !backend) continue
    out.push({ capability, adapter, backend, enabled: true })
  }
  return out
}

// ---------------------------------------------------------------------------
// Candidate assembly
// ---------------------------------------------------------------------------

/**
 * Assemble the authored essentials into a customer.yaml-shaped object. Derived
 * isolation invariants (memory namespaces) come from the slug so the validator's
 * customer_id-equality checks pass; scope/escalation get safe defaults the
 * config-authoring surface (§5.2) refines later. The object is the validation
 * input; `serializeCustomerYaml` renders it to the deliverable text.
 */
export function buildCandidateDoc(input: ProvisioningInput): Record<string, unknown> {
  const slug = input.customer_id
  const persona: Record<string, unknown> = {
    slug: input.persona_slug,
    status: 'active',
    name: input.persona_name,
    tone: input.persona_tone,
    entitlements: {
      exposure: {
        internal_write: input.exposure_level,
        external_send: input.exposure_level,
      },
    },
    skills: [
      {
        name: input.skill_name,
        initiation: { manual: true, scheduled: false, webhook: false },
        enabled: true,
      },
    ],
  }
  if (input.persona_title) persona['title'] = input.persona_title

  return {
    schema_version: 1,
    customer_id: slug,
    customer_name: input.customer_name,
    vertical: input.vertical,
    addons: input.addons,
    practice_areas: input.practice_areas,
    fly_region: input.fly_region,
    model: input.model,
    hermes_ref: input.hermes_ref,
    machine: { size: input.machine_size, memory_mb: input.machine_memory_mb },
    users: [{ email: input.user_email, role: input.user_role, full_name: input.user_full_name }],
    personas: [persona],
    connectors: buildConnectorMap(input.connectors),
    scope: {
      email_folders_visible: ['Inbox', 'Sent'],
      email_folders_blind: [],
      email_keyword_blocks: [],
      domain_blocks: [],
    },
    escalation: {
      red_flag_recipients: [input.user_email],
      failure_recipients: [input.user_email],
    },
    memory: {
      d1_namespace: slug,
      r2_vault_path: `vaults/${slug}/`,
      vectorize_index: `hermes-${slug}-vault`,
    },
  }
}

function buildConnectorMap(rows: ConnectorInput[]): Record<string, unknown> {
  const map: Record<string, unknown> = {}
  for (const c of rows) {
    if (!c.capability) continue
    map[c.capability] = { adapter: c.adapter, backend: c.backend, enabled: c.enabled }
  }
  return map
}

// ---------------------------------------------------------------------------
// YAML serialization (scoped emitter — no YAML lib in the Worker bundle)
// ---------------------------------------------------------------------------

function isPlainObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** A single scalar rendered for YAML. Strings are always single-quoted (with
 * internal quotes doubled) so no string ever needs the "does this need
 * quoting?" decision tree — single-quoting is always valid YAML. */
function emitScalar(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') return `'${value.replace(/'/g, "''")}'`
  // Defensive: the emitter routes arrays/objects elsewhere, so this branch is
  // unreachable for a well-formed doc; render through JSON so a stray non-scalar
  // never emits '[object Object]'.
  return `'${JSON.stringify(value).replace(/'/g, "''")}'`
}

function emitNode(key: string, value: unknown, indent: number): string[] {
  const pad = '  '.repeat(indent)
  if (Array.isArray(value)) {
    if (value.length === 0) return [`${pad}${key}: []`]
    return [`${pad}${key}:`, ...emitListItems(value, indent + 1)]
  }
  if (isPlainObj(value)) {
    const inner = emitMapEntries(value, indent + 1)
    return inner.length === 0 ? [`${pad}${key}: {}`] : [`${pad}${key}:`, ...inner]
  }
  return [`${pad}${key}: ${emitScalar(value)}`]
}

function emitMapEntries(obj: Record<string, unknown>, indent: number): string[] {
  const out: string[] = []
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined) continue
    out.push(...emitNode(k, v, indent))
  }
  return out
}

function emitListItems(arr: unknown[], indent: number): string[] {
  const pad = '  '.repeat(indent)
  const out: string[] = []
  for (const item of arr) {
    if (isPlainObj(item)) {
      const lines = emitMapEntries(item, indent + 1)
      // The first entry carries the "- " bullet; the rest align under it.
      lines[0] = `${pad}- ${lines[0].slice(pad.length + 2)}`
      out.push(...lines)
    } else {
      out.push(`${pad}- ${emitScalar(item)}`)
    }
  }
  return out
}

/** Render a customer.yaml-shaped object to YAML text. */
export function serializeCustomerYaml(doc: Record<string, unknown>): string {
  return `${emitMapEntries(doc, 0).join('\n')}\n`
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

export interface CandidateValidation {
  result: ValidationResult
  yamlText: string
}

/**
 * Validate an assembled candidate. Renders the YAML once and passes it as
 * `rawText` so `validate()` runs the raw-text secret scan first (fails closed on
 * a leaked secret even if the structure is otherwise valid) and then the
 * structural + parsed-value checks — the "validator + secret-scan" pass §4.5
 * step 3 calls for, in a single documented call.
 */
export function validateCandidate(doc: Record<string, unknown>): CandidateValidation {
  const yamlText = serializeCustomerYaml(doc)
  const result = validate(doc, { rawText: yamlText })
  return { result, yamlText }
}

// ---------------------------------------------------------------------------
// Form option sources (all from real schema constants)
// ---------------------------------------------------------------------------

export function verticalOptions(): readonly string[] {
  return ACCEPTED_VERTICALS
}

export function trustCeilingOptions(): readonly string[] {
  return ACCEPTED_EXPOSURE_CEILINGS
}

export function userRoleOptions(): readonly string[] {
  return ACCEPTED_USER_ROLES
}

export function capabilityOptions(): CapabilityName[] {
  return [...ACCEPTED_CAPABILITY_NAMES]
}

// ---------------------------------------------------------------------------
// Intent ledger (no entities() FK — the customer does not exist yet)
// ---------------------------------------------------------------------------

export type ProvisioningOutcome = 'validated' | 'rejected'

export interface ProvisioningIntentEvent {
  customer_id: string
  customer_name: string
  vertical: string
  actor_user_id: string
  actor_email: string
  actor_role: string
  outcome: ProvisioningOutcome
  error_count: number
  candidate_yaml: string
}

/**
 * Append a provisioning attempt to the intent ledger. Always
 * `source='portal_intent'`: nothing here stands up a Machine or writes git — the
 * row records that SMD authored + validated a candidate config, and (on success)
 * the exact YAML handed to the stand-up tooling.
 */
export async function recordProvisioningIntent(
  db: D1Database,
  event: ProvisioningIntentEvent
): Promise<number> {
  const res = await db
    .prepare(
      'INSERT INTO operator_provisioning_intent ' +
        '(source, customer_id, customer_name, vertical, actor_user_id, actor_email, ' +
        'actor_role, outcome, error_count, candidate_yaml) ' +
        "VALUES ('portal_intent', ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id"
    )
    .bind(
      event.customer_id,
      event.customer_name,
      event.vertical,
      event.actor_user_id,
      event.actor_email,
      event.actor_role,
      event.outcome,
      event.error_count,
      event.candidate_yaml
    )
    .first<{ id: number }>()
  return res?.id ?? 0
}

export interface ProvisioningIntentRow {
  id: number
  customer_id: string
  customer_name: string
  vertical: string
  outcome: ProvisioningOutcome
  error_count: number
  actor_email: string
  created_at: string
}

/** Most-recent N provisioning attempts, newest first. Read-only. */
export async function listProvisioningIntent(
  db: D1Database,
  limit = 25
): Promise<ProvisioningIntentRow[]> {
  const result = await db
    .prepare(
      'SELECT id, customer_id, customer_name, vertical, outcome, error_count, ' +
        'actor_email, created_at FROM operator_provisioning_intent ' +
        'ORDER BY created_at DESC, id DESC LIMIT ?'
    )
    .bind(limit)
    .all<ProvisioningIntentRow>()
  return result.results ?? []
}
