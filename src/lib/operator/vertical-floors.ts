/**
 * Ceilings, their ordering, and the non-raisable vertical floors.
 *
 * The pure half of config governance (ADR 0026 / ADR 0030 §4): no I/O, no
 * audit writer, just the vocabulary the entitlement compiler and the portal
 * governance surface both reason with. It lives in `src/lib/operator/`
 * because the compiler is the lower layer; until 2026-09-10 this sat in
 * `src/lib/portal/operator/config-governance.ts` and the compiler imported
 * it upward, the one back-edge in the Operator split (code review
 * 2026-09-10, Architecture 4). The portal module re-exports these names so
 * its callers are unchanged.
 */

import type { ActionClass } from './customer-yaml/types'

export type Ceiling = 'autonomous' | 'confirm' | 'draft_for_review' | 'refused'

/**
 * Restrictiveness ordering — mirrors `operator/adapter/trust_ceiling.py`
 * `_RESTRICTIVENESS`. Higher = more restrictive. Single source for the
 * raise/lower asymmetry and the floor comparison on the TS side.
 */
const RESTRICTIVENESS: Record<Ceiling, number> = {
  autonomous: 0,
  confirm: 1,
  draft_for_review: 2,
  refused: 3,
}

export function isCeiling(value: unknown): value is Ceiling {
  return (
    value === 'autonomous' ||
    value === 'confirm' ||
    value === 'draft_for_review' ||
    value === 'refused'
  )
}

export function restrictiveness(c: Ceiling): number {
  return RESTRICTIVENESS[c]
}

// 'n/a' is never RETURNED by changeDirection() below, but it is the DB default
// for config_change_audit.direction (migration 0046, CHECK allows it). This is
// the READ type for that column (see ConfigChangeAuditRow.direction), so 'n/a'
// must stay to model a row written via the column default. Not dead — load-bearing
// for the read path.
export type ChangeDirection = 'raise' | 'lower' | 'lateral' | 'n/a'

/**
 * Direction of a ceiling change. A "raise" moves toward LESS restrictive
 * (more autonomy) — the privileged direction ADR 0026 §5 guards.
 */
export function changeDirection(oldValue: Ceiling, newValue: Ceiling): ChangeDirection {
  const delta = RESTRICTIVENESS[newValue] - RESTRICTIVENESS[oldValue]
  if (delta < 0) return 'raise'
  if (delta > 0) return 'lower'
  return 'lateral'
}

/**
 * Non-raisable per-action-class vertical floors (ADR 0025 / ADR 0022
 * compliance constraints). Seeded constant — mirrors the runtime source of
 * truth (`hermes-smd-overlay` `shared/action_classes.py` `VERTICAL_FLOORS`);
 * kept tiny, keys asserted to be real action classes (see
 * config-governance.test.ts) so the portal and the runtime can never drift on
 * the identifier.
 *
 * Currently EMPTY: the law-firm external-send-draft-floor was removed 2026-07
 * (Captain decision, ADR 0073). Outside-send is governed by the firm's
 * authored exposure per ADR 0035 — supervision (ABA 512) is held by the audit
 * journal + attribution + fail-closed entitlement, not by a non-raisable send
 * gate. The floor machinery stays for any future regulation-compelled floor;
 * re-adding an entry requires a Captain decision, and the runtime map must be
 * updated in the same breath.
 */
export const VERTICAL_FLOORS: Readonly<Record<string, Partial<Record<ActionClass, Ceiling>>>> = {}

/**
 * @public Membership guard. tests/config-governance.test.ts imports it to assert every floor key
 * is an accepted action class. No runtime caller, by design.
 */
export function verticalFloorActionClasses(): string[] {
  const keys = new Set<string>()
  for (const floors of Object.values(VERTICAL_FLOORS)) {
    for (const k of Object.keys(floors)) keys.add(k)
  }
  return [...keys]
}

export function getVerticalFloor(vertical: string | null, action: ActionClass): Ceiling | null {
  if (!vertical) return null
  return VERTICAL_FLOORS[vertical]?.[action] ?? null
}

export interface FloorCheck {
  allowed: boolean
  reason: string | null
}

/**
 * A requested ceiling may not be LESS restrictive than the floor. Returns
 * disallowed for a raise above the floor; allowed when at/below the floor or
 * when no floor applies.
 */
export function checkFloor(floor: Ceiling | null, requested: Ceiling): FloorCheck {
  if (floor === null) return { allowed: true, reason: null }
  if (RESTRICTIVENESS[requested] < RESTRICTIVENESS[floor]) {
    return {
      allowed: false,
      reason: `vertical floor requires '${floor}' for this action class; '${requested}' would raise above it`,
    }
  }
  return { allowed: true, reason: null }
}
