/**
 * Config governance — the portal-side security boundary for autonomy config
 * (ADR 0026 / ADR 0030 §4).
 *
 * A change to exposure or initiation is a privileged, principal-
 * authenticated, immutably-audited, floor-checked act. This module holds the
 * pure decision logic plus the append-only audit writer. It is imported ONLY
 * by principal-gated portal POST handlers — never by agent/skill/tool code
 * (the agent runs on the Machine in the overlay repo, physically unable to
 * reach this module per the ADR 0009 isolation boundary; this is the portal-
 * side statement of ADR 0026 §1 "the agent can never raise its own ceiling").
 *
 * This module does NOT mutate the live `customer_configs` replica: that table
 * is read-only on principle (ADR 0012 §2 — only git -> CI writes it). The
 * value change reaches the runtime via the deferred git write-back path
 * (ADR 0025 step 7). What we persist here is the governance ACTION and its
 * floor decision, in the `config_change_audit` ledger — that is ADR 0026's
 * "immutably audited," honestly scoped `portal_intent`.
 */

import type { ActionClass } from '../../operator/customer-yaml/types'
import type { D1Database } from '@cloudflare/workers-types'
import {
  changeDirection,
  checkFloor,
  getVerticalFloor,
  type Ceiling,
  type ChangeDirection,
} from '../../operator/vertical-floors'

// The pure vocabulary (ceilings, their ordering, the vertical floors) moved
// down to src/lib/operator/vertical-floors.ts on 2026-09-10 so the entitlement
// compiler no longer imports upward from this portal module. The names
// production callers import from here are re-exported so those callers are
// unchanged; VERTICAL_FLOORS, checkFloor, changeDirection,
// verticalFloorActionClasses and FloorCheck have no production importer via
// this path and are imported from
// src/lib/operator/vertical-floors directly (tests included).
export {
  getVerticalFloor,
  isCeiling,
  restrictiveness,
  type Ceiling,
  type ChangeDirection,
} from '../../operator/vertical-floors'

export type ConfigChangeType = 'entitlement_exposure' | 'entitlement_initiation' | 'skill_enabled'
export type ConfigChangeOutcome = 'accepted' | 'rejected_floor' | 'rejected_invalid'

export interface ConfigChangeAuditEvent {
  customer_slug: string
  entity_id: string
  actor_user_id: string
  actor_email: string
  actor_role: string
  change_type: ConfigChangeType
  persona_slug: string | null
  skill_name: string | null
  action_class: string | null
  old_value: string | null
  new_value: string | null
  outcome: ConfigChangeOutcome
  outcome_reason: string | null
  direction: ChangeDirection
}

/**
 * Append a governance action to the immutable control-plane ledger. Always
 * writes `source='portal_intent'`. Records accepted AND rejected outcomes —
 * a floor-rejected raise is itself a compliance-relevant event (ADR 0026 §4).
 */
export async function recordConfigChangeAudit(
  db: D1Database,
  event: ConfigChangeAuditEvent
): Promise<void> {
  await db
    .prepare(
      'INSERT INTO config_change_audit ' +
        '(customer_slug, entity_id, source, actor_user_id, actor_email, actor_role, ' +
        'change_type, persona_slug, skill_name, action_class, old_value, new_value, ' +
        'outcome, outcome_reason, direction) ' +
        "VALUES (?, ?, 'portal_intent', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    .bind(
      event.customer_slug,
      event.entity_id,
      event.actor_user_id,
      event.actor_email,
      event.actor_role,
      event.change_type,
      event.persona_slug,
      event.skill_name,
      event.action_class,
      event.old_value,
      event.new_value,
      event.outcome,
      event.outcome_reason,
      event.direction
    )
    .run()
}

export interface Actor {
  user_id: string
  email: string
  role: string
}

export interface ApplyExposureChangeInput {
  customer_slug: string
  entity_id: string
  actor: Actor
  persona_slug: string | null
  skill_name: string
  action_class: ActionClass
  /** The customer's vertical, for the floor lookup. Null when unknown. */
  vertical: string | null
  old_value: Ceiling
  new_value: Ceiling
}

export interface ApplyResult {
  outcome: ConfigChangeOutcome
  reason: string | null
}

/**
 * Orchestrate a trust-ceiling / action-ceiling change: compute direction,
 * floor-check a raise on an action class, and record the outcome (accepted or
 * rejected) to the ledger. Does not write the live config (deferred git
 * write-back). The caller is responsible for being principal-gated.
 */
export async function applyExposureChange(
  db: D1Database,
  input: ApplyExposureChangeInput
): Promise<ApplyResult> {
  const direction = changeDirection(input.old_value, input.new_value)
  const floor = getVerticalFloor(input.vertical, input.action_class)
  const floorCheck = checkFloor(floor, input.new_value)
  const outcome: ConfigChangeOutcome = floorCheck.allowed ? 'accepted' : 'rejected_floor'

  await recordConfigChangeAudit(db, {
    customer_slug: input.customer_slug,
    entity_id: input.entity_id,
    actor_user_id: input.actor.user_id,
    actor_email: input.actor.email,
    actor_role: input.actor.role,
    change_type: 'entitlement_exposure',
    persona_slug: input.persona_slug,
    skill_name: input.skill_name,
    action_class: input.action_class,
    old_value: input.old_value,
    new_value: input.new_value,
    outcome,
    outcome_reason: floorCheck.reason,
    direction,
  })

  return { outcome, reason: floorCheck.reason }
}

export interface ApplySkillToggleInput {
  customer_slug: string
  entity_id: string
  actor: Actor
  persona_slug: string | null
  skill_name: string
  next_enabled: boolean
}

/**
 * Record a skill enable/disable. Always `accepted`; audited.
 */
export async function applySkillToggle(
  db: D1Database,
  input: ApplySkillToggleInput
): Promise<ApplyResult> {
  await recordConfigChangeAudit(db, {
    customer_slug: input.customer_slug,
    entity_id: input.entity_id,
    actor_user_id: input.actor.user_id,
    actor_email: input.actor.email,
    actor_role: input.actor.role,
    change_type: 'skill_enabled',
    persona_slug: input.persona_slug,
    skill_name: input.skill_name,
    action_class: null,
    old_value: input.next_enabled ? 'false' : 'true',
    new_value: input.next_enabled ? 'true' : 'false',
    outcome: 'accepted',
    outcome_reason: null,
    direction: 'lateral',
  })

  return { outcome: 'accepted', reason: null }
}
