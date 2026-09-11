/**
 * The ONE reconstruction of a validator-shaped `customer.yaml` root from the
 * lossy `customer_configs` projection, plus the display view of the locked
 * fields that reconstruction cannot know.
 *
 * WHY THIS FILE EXISTS. Two copies of this reconstruction lived under two
 * names — `reconstructFromProjection` in customer-yaml-editor.ts and
 * `reconstructProjection` in the customer-yaml-update route — with no shared
 * source and no drift test. They had already drifted: one defaulted every
 * `scope` sub-field individually and carried `voice_cohorts`, the other passed
 * `row.scope` through whole and omitted `inbound_allow_from` from its fallback.
 * The next field added to the projection would have been added to one of them.
 *
 * WHAT A RECONSTRUCTION IS FOR, and the #1965 failure it caused. The
 * projection is a read replica of a subset: it carries personas, connectors,
 * scope, escalation, business hours and voice library, and it carries NONE of
 * the identity/runtime block (`hermes_ref`, `machine`, `model`, `fly_region`,
 * `users`) and none of cron's runtime mechanics. The validator, however, wants
 * a whole document. The previous reconstruction squared that circle by
 * inventing values — and, for `hermes_ref`, by deliberately inventing an
 * INVALID one (`v0.0.0@000…`) so it would fail `checkHermesRef` "and prompt
 * the operator to set a real pin". That reasoning belongs to a real config
 * being authored; applied to a reconstruction of a replica it guaranteed the
 * strict pass failed for every customer, always, which is exactly what it did:
 * every seat in `operator/customers/` resolved to CONFIGURATION ERROR.
 *
 * THE POSTURE HERE. Fields the projection does not carry are filled with
 * placeholders chosen to be structurally valid and semantically inert, so the
 * validator can do its job on the surface the editor actually writes. Those
 * placeholders are never persisted (the merged document is validated and
 * discarded — see the customer-yaml-update route) and never displayed: the
 * locked view a client reads is built by `projectLockedFromRow` from the ROW,
 * and a field the row does not carry comes back `null` for the page to render
 * as absent rather than as a plausible-looking value. Inventing a version pin
 * and showing it to a client would be Pattern B fabrication (CLAUDE.md); the
 * old code avoided that only by failing before it could render.
 *
 * `cron` is DROPPED rather than defaulted. The projection carries skill +
 * schedule and deliberately omits `wake_policy` (runtime mechanics stay
 * unprojected), which the validator requires. Dropping the block says "this
 * replica knows nothing about scheduling"; defaulting it would put a fabricated
 * runtime policy into a document that is then diffed and audited. The editor
 * never writes cron either way.
 */

import type { CustomerConfigRow, PersonaConfig } from '../customer-config'

/**
 * Structurally-valid stand-in for the upstream pin the projection does not
 * carry. Matches the ADR 0024 shape (`v{YYYY}.{M}.{D}@{40-hex}`) with an
 * all-zero date and sha so it cannot be confused with a real release, and
 * PASSES validation — unlike the `v0.0.0@…` sentinel it replaces, whose whole
 * design was to fail. See the header: this document is validated and thrown
 * away, so a failing pin bought nothing and cost the editor.
 */
export const UNPROJECTED_HERMES_REF = `v0000.0.0@${'0'.repeat(40)}`

/**
 * `users` is required and must be non-empty, and the projection carries no
 * users column (the roster is console-side access control, not Machine
 * config). One inert entry satisfies the shape. Deliberately NOT an
 * email-shaped string: nothing downstream should be able to mistake it for a
 * real person, and the validator checks only that the field is a non-empty
 * string.
 */
const UNPROJECTED_USER = {
  email: 'unprojected',
  role: 'principal',
  full_name: 'unprojected',
} as const

/**
 * Vertical stand-in for the RECONSTRUCTION only — the row's real `vertical` is
 * what the client is shown (`projectLockedFromRow`).
 *
 * `mixed` rather than the row's value because `vertical` is the one identity
 * field that imposes requirements on OTHER un-projected fields: `law-firm`
 * requires a non-empty `practice_areas`, which the projection does not carry.
 * Reconstructing `law-firm` would therefore mean inventing practice areas to
 * satisfy it — putting a fabricated claim about a firm's practice into a
 * document, which is worse than the inert placeholder. `mixed` imposes nothing
 * and asserts nothing.
 */
const UNPROJECTED_VERTICAL = 'mixed'

interface ProjectedScope {
  email_folders_visible?: string[]
  email_folders_blind?: string[]
  email_keyword_blocks?: string[]
  domain_blocks?: string[]
  matter_blocks?: string[]
  inbound_allow_from?: string[]
  admins?: string[]
  // ss#2546. Carried beside admins rather than left to the validator's default,
  // because it is validated AS A SUBSET of admins: reconstructing one without
  // the other would validate a document the authored file is not.
  rule_requests_to?: string[]
  // ss#2546 (the operations half). Carried for the same reason: the validator
  // reads it against a closed set of SMD domains, so reconstructing a document
  // without it would validate something the authored file is not.
  ops_reply_from?: string[]
}

interface ProjectedEscalation {
  red_flag_recipients?: string[]
  failure_recipients?: string[]
  acknowledgement_window_minutes?: number | null
}

/**
 * Reassemble the projection columns into the validator's expected root shape.
 *
 * Returns `unknown` on purpose: the caller feeds it to `validate()`, which is
 * the thing that produces a typed `CustomerYaml`. Handing back a pre-typed
 * object here would be a cast over data assembled from JSON columns.
 */
export function reconstructFromProjection(row: CustomerConfigRow): unknown {
  return {
    schema_version: Number(row.schema_version),
    customer_id: row.customer_slug,
    customer_name: row.customer_slug,
    vertical: UNPROJECTED_VERTICAL,
    fly_region: 'unprojected',
    model: 'unprojected',
    hermes_ref: UNPROJECTED_HERMES_REF,
    machine: { size: 'unprojected', memory_mb: 256 },
    users: [{ ...UNPROJECTED_USER }],
    personas: row.personas.map(withoutCron),
    connectors: row.connectors ?? {},
    scope: reconstructScope(row.scope),
    escalation: reconstructEscalation(row.escalation),
    voice_library: row.voice_library ?? null,
    voice_cohorts: null,
    business_hours: row.business_hours ?? null,
    memory: {
      d1_namespace: row.customer_slug,
      r2_vault_path: `vaults/${row.customer_slug}/`,
      vectorize_index: `hermes-${row.customer_slug}-vault`,
      retention: null,
    },
  }
}

/** Every scope list defaulted to empty — an absent column means no entries. */
function reconstructScope(raw: unknown): Required<ProjectedScope> {
  const scope = (raw as ProjectedScope | null) ?? {}
  return {
    email_folders_visible: scope.email_folders_visible ?? [],
    email_folders_blind: scope.email_folders_blind ?? [],
    email_keyword_blocks: scope.email_keyword_blocks ?? [],
    domain_blocks: scope.domain_blocks ?? [],
    matter_blocks: scope.matter_blocks ?? [],
    inbound_allow_from: scope.inbound_allow_from ?? [],
    admins: scope.admins ?? [],
    rule_requests_to: scope.rule_requests_to ?? [],
    ops_reply_from: scope.ops_reply_from ?? [],
  }
}

function reconstructEscalation(raw: unknown): Required<ProjectedEscalation> {
  const escalation = (raw as ProjectedEscalation | null) ?? {}
  return {
    red_flag_recipients: escalation.red_flag_recipients ?? [],
    failure_recipients: escalation.failure_recipients ?? [],
    acknowledgement_window_minutes: escalation.acknowledgement_window_minutes ?? null,
  }
}

/**
 * Strip `cron` from a projected persona. See the header: the projection omits
 * `wake_policy`, which the validator requires, and a fabricated policy is worse
 * than an absent block on a document nobody persists.
 */
function withoutCron(persona: PersonaConfig): Omit<PersonaConfig, 'cron'> {
  const { cron: _cron, ...rest } = persona
  void _cron
  return rest
}

/**
 * The Captain-managed fields as the PROJECTION actually knows them.
 *
 * Every field the row does not carry is `null`, and the page renders those as
 * absent. This is the display counterpart to the placeholders above: the
 * validator gets a whole document, the client gets only what is true.
 */
export interface ProjectedLockedFields {
  schema_version: number
  customer_id: string
  vertical: string | null
  fly_region: null
  model: null
  hermes_ref: null
  machine: null
  connector_token_refs: Record<string, string | null>
}

export function projectLockedFromRow(
  row: CustomerConfigRow,
  connectorTokenRefs: Record<string, string | null>
): ProjectedLockedFields {
  return {
    schema_version: Number(row.schema_version),
    customer_id: row.customer_slug,
    vertical: row.vertical,
    fly_region: null,
    model: null,
    hermes_ref: null,
    machine: null,
    connector_token_refs: connectorTokenRefs,
  }
}
