/**
 * Entitlement tier change — runtime transport + governance ledger (#2003 Q7).
 *
 * One governed path from a Named Administrator's click to the running
 * Operator, modeled exactly as the pause (Q6) is modeled:
 *
 *   compile (letter ceiling + vertical floor, both non-raisable)
 *     →  Machine gate (POST /entitlement/set — the runtime override store)
 *     →  governance row (status `applied`)
 *
 * Captain ruling 2026-07-28: the within-ceiling entitlement level is
 * CLIENT-OWNED RUNTIME POSTURE, not an edit to authored config. The authored
 * ceiling stays in git (`entitlements.exposure_ceiling`, gate (i) of the
 * commitments suite); the setting underneath it lives in the Machine's
 * volume-backed override store, takes effect on the next tool call, and
 * survives restart and reprovision. The Machine clamps every set to the
 * authored ceiling itself — the console is trusted for WHO, never for HOW FAR.
 *
 * Ordering is Machine-first, record-second — the same rule the pause control
 * follows: a change the Machine did not acknowledge is never recorded, and a
 * recorded change is one the runtime is already enforcing. The ledger row is
 * the client-readable audit record (who/when/why); the portal activity feed
 * unions it alongside pause events.
 *
 * The PR-based delivery leg this module previously carried (compile → yaml
 * edit → reviewable PR, #2020) is superseded and removed — one path only.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { machineBaseUrl } from '../../operator/machine-url'
import {
  compileTierChange,
  type LiveExposure,
  type Rejection,
  type TierChangeDelta,
} from '../../operator/entitlement-compiler'
import type { RoutineGrid } from '../../operator/routine-grid'
import { deriveRuntimeReadKey } from '../../operator/runtime-read-transport'
import { resolveCustomerFlyApp } from '../../operator/fly-app-registry'

export interface EntitlementGateEnv {
  OPERATOR_MCP_WEBHOOK_SECRET?: string
  OPERATOR_RUNTIME_READ_URL?: string
}

export interface TierChangeActor {
  userId: string
  email: string
  role: string
}

export interface TierChangeInput {
  entityId: string
  customerSlug: string
  routine: string
  targetTier: string
  reason: string
  vertical: string | null
  actor: TierChangeActor
  source: 'portal' | 'admin'
}

export type TierChangeOutcome =
  | { kind: 'applied'; delta: TierChangeDelta }
  | { kind: 'noop'; delta: TierChangeDelta }
  | { kind: 'rejected'; rejections: readonly Rejection[] }
  | { kind: 'failed'; error: string }

export interface GateEntitlementResult {
  applied: { action_class: string; ceiling: string }[]
  persona: string
  updated_at: string
}

/** True when the entitlement transport can reach a Machine (secret + URL present). */
function isEntitlementConfigured(env: EntitlementGateEnv): boolean {
  return (
    typeof env.OPERATOR_MCP_WEBHOOK_SECRET === 'string' &&
    env.OPERATOR_MCP_WEBHOOK_SECRET.length > 0 &&
    typeof env.OPERATOR_RUNTIME_READ_URL === 'string' &&
    env.OPERATOR_RUNTIME_READ_URL.length > 0
  )
}

/**
 * A compiled exposure change as the gate speaks it. `to: null` (deauthorize —
 * the flag-only target) maps to `refused`: the override store has no delete
 * verb, and a refused send class is enforcement-equivalent to the unauthored
 * key (ADR 0056 fail-closed) the authored-config model expressed by absence.
 */
export function gateChangesOf(delta: TierChangeDelta): { action_class: string; ceiling: string }[] {
  return delta.exposureChanges.map((c) => ({
    action_class: c.actionClass,
    ceiling: c.to ?? 'refused',
  }))
}

/**
 * Proxy the compiled change to the customer's Machine gate. Throws on any
 * failure so the caller records nothing and surfaces an honest error — a
 * change the Machine did not acknowledge must never be reported as applied.
 * A 409 is the Machine's own clamp refusing a raise above the authored
 * ceiling (defense in depth behind the compiler's identical check).
 */
async function setEntitlementOnMachine(
  env: EntitlementGateEnv,
  customerSlug: string,
  body: {
    persona: string
    changes: { action_class: string; ceiling: string }[]
    actor_id: string
    reason: string
  }
): Promise<GateEntitlementResult> {
  if (!isEntitlementConfigured(env)) {
    throw new Error(
      'entitlement transport not configured (OPERATOR_MCP_WEBHOOK_SECRET / URL unset)'
    )
  }
  const app = resolveCustomerFlyApp(customerSlug)
  if (!app) throw new Error(`entitlement: unknown customer ${customerSlug}`)

  const bearer = await deriveRuntimeReadKey(env.OPERATOR_MCP_WEBHOOK_SECRET!, customerSlug)
  const url = `${machineBaseUrl(env.OPERATOR_RUNTIME_READ_URL!, app)}/entitlement/set`
  const resp = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`gate entitlement set failed: ${resp.status} ${detail.slice(0, 200)}`)
  }
  const data: Partial<GateEntitlementResult> = await resp.json()
  const applied = Array.isArray(data.applied) ? data.applied : []
  // The Machine echoes the rows it actually wrote. Compare it to what we sent
  // (ss#2314): a 200 whose echo is empty or partial means the seat dropped a
  // change — the shape an override key it cannot index takes when it does not
  // 409 — and recording that as `applied` would put a posture in the client's
  // ledger that the runtime never took. Throwing keeps the module's stated
  // invariant true: a change the Machine did not acknowledge is never recorded.
  const missing = unacknowledgedChanges(body.changes, applied)
  if (missing.length > 0) {
    throw new Error(
      `gate entitlement set incomplete: the Operator acknowledged ${applied.length} of ` +
        `${body.changes.length} changes (unconfirmed: ${missing.join(', ')})`
    )
  }
  return { applied, persona: data.persona ?? body.persona, updated_at: data.updated_at ?? '' }
}

/**
 * Requested changes the Machine's echo does not confirm at the exact ceiling
 * we asked for. Order-independent; an echo carrying extra rows is fine (the
 * batch is atomic seat-side, so extras are other personas' rows, never a
 * substitution for ours).
 */
export function unacknowledgedChanges(
  requested: readonly { action_class: string; ceiling: string }[],
  applied: readonly { action_class: string; ceiling: string }[]
): string[] {
  const seen = new Set(applied.map((a) => `${a.action_class}=${a.ceiling}`))
  return requested
    .filter((c) => !seen.has(`${c.action_class}=${c.ceiling}`))
    .map((c) => `${c.action_class}=${c.ceiling}`)
}

/**
 * Execute one tier change end to end: compile, apply on the Machine, record.
 * The outcome the portal renders says `applied` and means it — the runtime
 * was enforcing the new level before the row was written.
 */
export async function applyTierChange(
  db: D1Database,
  env: EntitlementGateEnv,
  deps: { grid: RoutineGrid; live: LiveExposure },
  input: TierChangeInput
): Promise<TierChangeOutcome> {
  const compiled = compileTierChange(deps.grid, deps.live, {
    routine: input.routine,
    targetTier: input.targetTier,
    vertical: input.vertical,
  })
  if (!compiled.ok) return { kind: 'rejected', rejections: compiled.rejections }
  const delta = compiled.delta
  if (delta.noop) return { kind: 'noop', delta }

  try {
    await setEntitlementOnMachine(env, input.customerSlug, {
      persona: deps.live.personaSlug,
      changes: gateChangesOf(delta),
      actor_id: input.actor.email,
      reason: input.reason,
    })
  } catch (err) {
    return { kind: 'failed', error: err instanceof Error ? err.message : 'gate set failed' }
  }

  await db
    .prepare(
      'INSERT INTO operator_entitlement_changes ' +
        '(id, entity_id, customer_slug, routine, from_tier, to_tier, delta_json, actor_user_id, ' +
        'actor_email, actor_role, source, reason, status, pr_url, pr_number) ' +
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    )
    .bind(
      crypto.randomUUID(),
      input.entityId,
      input.customerSlug,
      delta.routine,
      delta.fromTier,
      delta.toTier,
      JSON.stringify(delta.exposureChanges),
      input.actor.userId,
      input.actor.email,
      input.actor.role,
      input.source,
      input.reason,
      'applied',
      null,
      null
    )
    .run()

  return { kind: 'applied', delta }
}

export interface LiveOverrideReadEnv {
  OPERATOR_RUNTIME_READ_SECRET?: string
  OPERATOR_RUNTIME_READ_URL?: string
}

/**
 * Outcome of reading the Machine's live override store, as a state the caller
 * can render honestly (ss#2314). The old `Record | null` return collapsed four
 * different situations into two values: an empty map meant BOTH "no overrides
 * are set" and "the persona name did not match anything on the seat", and null
 * meant BOTH "this seat has no read transport configured" and "the read
 * failed" — so a display fell back to authored-only config without ever being
 * able to say the live posture was unknown.
 */
export type LiveOverrideRead =
  /** The seat answered. `overrides` may legitimately be empty. */
  | { status: 'ok'; overrides: Record<string, string> }
  /** No read transport for this seat — the live posture was never asked for. */
  | { status: 'unconfigured' }
  /** The seat was asked and did not answer usably. */
  | { status: 'unavailable'; detail: string }
  /** The seat answered about OTHER personas only — a config/identity defect. */
  | { status: 'persona_mismatch'; requested: string; seatPersonas: string[] }

/**
 * Project the seat's `/runtime/entitlements` payload onto one persona.
 *
 * Split out so the caller stays inside the complexity ceiling, and because the
 * persona discrimination is the interesting half: an empty projection is only
 * "no overrides are set" when the seat reported NO personas at all or reported
 * the one we asked about. If it reported rows under other names, the client's
 * overrides are being enforced somewhere we cannot see them (ss#2314).
 */
export function projectOverridesForPersona(
  entries: readonly { persona?: string; action_class?: string; ceiling?: string }[],
  personaSlug: string
): LiveOverrideRead {
  const out: Record<string, string> = {}
  const seatPersonas = new Set<string>()
  for (const entry of entries) {
    if (typeof entry.persona === 'string') seatPersonas.add(entry.persona)
    if (entry.persona === personaSlug && entry.action_class && entry.ceiling) {
      out[entry.action_class] = entry.ceiling
    }
  }
  if (seatPersonas.size > 0 && !seatPersonas.has(personaSlug)) {
    return { status: 'persona_mismatch', requested: personaSlug, seatPersonas: [...seatPersonas] }
  }
  return { status: 'ok', overrides: out }
}

/**
 * Read the Machine's live override store (`GET /runtime/entitlements`) for
 * display: the settings page overlays these onto the projected authored
 * exposure so the tier shown is the tier ENFORCED, not the authored default.
 *
 * Every non-`ok` status means the displayed tier is the AUTHORED one and the
 * enforced one is unknown — the caller must not present it as the live
 * posture. Enforcement is Machine-side regardless; this is a display path, and
 * its failure mode is a wrong-looking safety control, not a wrong enforcement.
 */
export async function readLiveOverrides(
  env: LiveOverrideReadEnv,
  customerSlug: string,
  personaSlug: string
): Promise<LiveOverrideRead> {
  const secret = env.OPERATOR_RUNTIME_READ_SECRET
  const urlTemplate = env.OPERATOR_RUNTIME_READ_URL
  if (typeof secret !== 'string' || secret.length === 0) return { status: 'unconfigured' }
  if (typeof urlTemplate !== 'string' || urlTemplate.length === 0) {
    return { status: 'unconfigured' }
  }
  const app = resolveCustomerFlyApp(customerSlug)
  if (!app) return { status: 'unconfigured' }
  try {
    const bearer = await deriveRuntimeReadKey(secret, customerSlug)
    const resp = await fetch(`${machineBaseUrl(urlTemplate, app)}/runtime/entitlements`, {
      headers: { Authorization: `Bearer ${bearer}`, 'X-Tenant-Slug': customerSlug },
    })
    if (!resp.ok) return { status: 'unavailable', detail: `runtime read ${resp.status}` }
    const data: {
      entries?: { persona?: string; action_class?: string; ceiling?: string }[]
    } = await resp.json()
    return projectOverridesForPersona(Array.isArray(data.entries) ? data.entries : [], personaSlug)
  } catch (err) {
    return {
      status: 'unavailable',
      detail: err instanceof Error ? err.message : 'runtime read failed',
    }
  }
}

export interface EntitlementChangeRow {
  id: string
  routine: string
  from_tier: string
  to_tier: string
  actor_email: string
  actor_role: string
  source: string
  reason: string
  status: string
  pr_url: string | null
  created_at: string
}

/** Applied changes for one customer, newest first (audit + portal surface). */
export async function listEntitlementChanges(
  db: D1Database,
  customerSlug: string,
  limit = 50
): Promise<EntitlementChangeRow[]> {
  const res = await db
    .prepare(
      'SELECT id, routine, from_tier, to_tier, actor_email, actor_role, source, reason, ' +
        'status, pr_url, created_at FROM operator_entitlement_changes ' +
        'WHERE customer_slug = ? ORDER BY created_at DESC LIMIT ?'
    )
    .bind(customerSlug, limit)
    .all<EntitlementChangeRow>()
  return res.results ?? []
}
