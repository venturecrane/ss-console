/**
 * Routine-tier → enforcement compiler (#2003, ADR 0075 + ADR 0069 Lock 3).
 *
 * The routine grid RECORDS how each routine's CURRENT tier is enforced
 * (`enforcement.exposure_keys`); it does not DERIVE the enforcement for a
 * different tier. Nothing in the repo did — which is why the portal's
 * entitlement control could not be built: a tier change had no compilable
 * meaning. This module is that missing piece.
 *
 * Given a seat's grid + live config and a requested (routine, target tier),
 * it returns either a typed config delta (the exact exposure/initiation
 * changes the seat must author) or a typed list of rejections. It is PURE —
 * no I/O, no D1, no git. Persisting the delta and delivering it to the
 * running Machine are separate, sequenced concerns (ADR 0012: git is the
 * source of truth; ADR 0044's live-apply path is REVERTED and unbuilt).
 *
 * ## What a tier means, derived not invented
 *
 * The tier vocabulary is the client letter's (ADR 0075). Its enforcement
 * meaning is read off the grid's own authored rows rather than assumed:
 *
 *   - `flag-only`        — the routine only surfaces. Its send action class
 *                          carries NO authored exposure (fail-closed per ADR
 *                          0035: unauthored = refused, never defaulted).
 *   - `prepare-and-route` — the routine prepares work a person sends:
 *                          send class = `draft_for_review`.
 *   - `auto-handle`      — the routine completes on its own:
 *                          send class = `autonomous`.
 *
 * The routine's send action class is discovered from the row's own authored
 * exposure keys (e.g. `external_send_client`, `external_send_vendor`,
 * `external_send`). A row with no send key has NO graduation path — matching
 * what those rows' notes already say ("The skill carries no draft or send
 * tool, so there is no path above flag-only"). Such a row is rejected rather
 * than silently synthesizing a send capability its skills do not have.
 *
 * ## The ceilings, in precedence order
 *
 * 1. **Letter ceiling** (`row.ceiling_tier`) — the client commitment the grid
 *    compiles from a dated letter. NON-RAISABLE here: raising it is a
 *    commitment change, which is the Captain's call and a letter, not a
 *    portal click.
 * 2. **Vertical floor** (ADR 0025, `VERTICAL_FLOORS`) — regulation-compelled,
 *    non-raisable. Empty today (ADR 0073) but checked, so re-adding a floor
 *    binds this path automatically.
 * 3. **No-op** — a request already at its live value compiles to no delta and
 *    is reported as such (never audited as a change that did not happen).
 */

import {
  changeDirection,
  getVerticalFloor,
  restrictiveness,
  type Ceiling,
  type ChangeDirection,
} from './vertical-floors'
import type { RoutineGrid, RoutineGridRow, RoutineTier } from './routine-grid'
import { ROUTINE_TIERS } from './routine-grid'
import { EXPOSURE_ACTION_CLASSES } from './customer-yaml/types'

/** Ordering of the tier vocabulary, least → most autonomous. */
const TIER_RANK: Readonly<Record<RoutineTier, number>> = {
  'flag-only': 0,
  'prepare-and-route': 1,
  'auto-handle': 2,
}

/**
 * Exposure ceiling a tier implies for a routine's SEND action class.
 * `null` = the class carries no authored exposure at this tier (fail-closed,
 * ADR 0035) — the flag-only posture.
 */
const TIER_SEND_CEILING: Readonly<Record<RoutineTier, Ceiling | null>> = {
  'flag-only': null,
  'prepare-and-route': 'draft_for_review',
  'auto-handle': 'autonomous',
}

/** Action classes that are NOT a routine's send class (internal bookkeeping). */
const NON_SEND_CLASSES: ReadonlySet<string> = new Set(['internal_write'])

export type RejectionCode =
  | 'unknown_routine'
  | 'invalid_tier'
  | 'above_letter_ceiling'
  | 'no_graduation_path'
  | 'below_vertical_floor'
  | 'persona_missing'
  | 'unknown_exposure_key'

export interface Rejection {
  code: RejectionCode
  message: string
}

export interface ExposureChange {
  /** Persona whose entitlements carry the key. */
  personaSlug: string
  /** Exposure action class, e.g. `external_send_client`. */
  actionClass: string
  /** Live authored value; null when the class is currently unauthored. */
  from: Ceiling | null
  /** Target value; null means REMOVE the key (return to fail-closed). */
  to: Ceiling | null
  direction: ChangeDirection | 'authorize' | 'deauthorize'
}

export interface TierChangeDelta {
  routine: string
  skills: readonly string[]
  fromTier: RoutineTier
  toTier: RoutineTier
  /** Empty when the request is a no-op at the config layer. */
  exposureChanges: readonly ExposureChange[]
  /** True when live config already satisfies the target. */
  noop: boolean
}

export type TierChangeResult =
  { ok: true; delta: TierChangeDelta } | { ok: false; rejections: readonly Rejection[] }

/** Live exposure map for the seat's persona, as authored (may be sparse). */
export interface LiveExposure {
  personaSlug: string
  exposure: Readonly<Record<string, string>>
}

function isTier(v: string): v is RoutineTier {
  return (ROUTINE_TIERS as readonly string[]).includes(v)
}

function asCeiling(v: string | undefined): Ceiling | null {
  if (v === 'autonomous' || v === 'confirm' || v === 'draft_for_review' || v === 'refused') {
    return v
  }
  return null
}

/**
 * The routine's send action class, discovered from its authored enforcement
 * keys. Returns null when the routine authors no send class at all — the
 * structural signal that it has no graduation path.
 */
export function sendActionClassOf(row: RoutineGridRow): string | null {
  for (const key of Object.keys(row.enforcement.exposure_keys)) {
    if (!NON_SEND_CLASSES.has(key)) return key
  }
  return null
}

/**
 * True when a grid key names an action class the seat's override store can
 * actually hold a row for. Mirrors the Machine's `_OVERRIDABLE_ACTIONS`
 * (`shared/exposure_override.py`) — see `EXPOSURE_ACTION_CLASSES`.
 */
export function isHonoredActionClass(key: string): boolean {
  return (EXPOSURE_ACTION_CLASSES as readonly string[]).includes(key)
}

/**
 * The resolved live tier plus WHY it resolved that way (ss#2314).
 *
 * `flag-only` has two causes that used to be the same value:
 *   - the key is honored and simply carries no authored/override value —
 *     the legitimate fail-closed posture (ADR 0035/0056), and
 *   - the key matches nothing the seat can honor — a CONFIG DEFECT that was
 *     rendering as a safety posture nobody was enforcing.
 *
 * `unknownActionClass` names the offending key in the second case and is null
 * in the first. The tier is fail-closed either way: a broken key never relaxes
 * a posture, it only stops being silent about it.
 */
export interface LiveTierResolution {
  tier: RoutineTier
  /** The grid key the seat cannot honor, or null when the key is fine. */
  unknownActionClass: string | null
}

/**
 * The tier a routine is CURRENTLY at, derived from live config rather than
 * from the grid's recorded `start_tier` (which is the letter's starting
 * point, not necessarily today's state after a prior change), together with
 * the key-health signal callers need to tell a posture from a defect.
 */
export function resolveLiveTier(row: RoutineGridRow, live: LiveExposure): LiveTierResolution {
  const sendClass = sendActionClassOf(row)
  // No send class at all is structural, not a defect: the row's skills carry
  // no draft or send tool, so flag-only is the only tier it HAS.
  if (sendClass === null) return { tier: 'flag-only', unknownActionClass: null }
  if (!isHonoredActionClass(sendClass)) {
    // Fail closed AND say so. The authored value (if any) is deliberately not
    // consulted — a key the Machine cannot index has no enforced meaning, and
    // reading one would be inventing a posture from a string.
    return { tier: 'flag-only', unknownActionClass: sendClass }
  }
  const authored = asCeiling(live.exposure[sendClass])
  if (authored === null) return { tier: 'flag-only', unknownActionClass: null }
  if (authored === 'autonomous') return { tier: 'auto-handle', unknownActionClass: null }
  // `refused` IS flag-only: the runtime dial expresses a flag-only target as
  // an explicit refused override (the store has no delete verb), which is
  // enforcement-equivalent to the unauthored key. Mapping it to
  // prepare-and-route rendered a lowered routine one tier too high — found by
  // the ss#2003 live probe (portal set flag-only; page re-rendered
  // prepare-and-route while the Machine correctly held refused).
  if (authored === 'refused') return { tier: 'flag-only', unknownActionClass: null }
  return { tier: 'prepare-and-route', unknownActionClass: null }
}

/**
 * Tier-only accessor for callers that genuinely need nothing else. Prefer
 * `resolveLiveTier` on any DISPLAY path: this function cannot distinguish a
 * fail-closed posture from a broken key, which is the ss#2314 defect itself.
 */
export function liveTierOf(row: RoutineGridRow, live: LiveExposure): RoutineTier {
  return resolveLiveTier(row, live).tier
}

/**
 * Compile a requested tier change into a config delta, or reject it.
 *
 * `vertical` feeds the ADR 0025 floor check; pass the seat's authored
 * vertical (null when unknown — no floor then applies, matching
 * `getVerticalFloor`).
 */
/**
 * Structural + ceiling guards, split out to keep `compileTierChange` inside
 * the 75-line function ceiling. Returns the resolved row/target on success.
 */
function guardRequest(
  grid: RoutineGrid,
  live: LiveExposure,
  request: { routine: string; targetTier: string }
): { ok: true; row: RoutineGridRow; target: RoutineTier } | { ok: false; rejections: Rejection[] } {
  const row = grid.rows.find((r) => r.routine === request.routine)
  if (!row) {
    return {
      ok: false,
      rejections: [
        { code: 'unknown_routine', message: `no routine "${request.routine}" in this seat's grid` },
      ],
    }
  }
  if (!isTier(request.targetTier)) {
    return {
      ok: false,
      rejections: [
        { code: 'invalid_tier', message: `tier must be one of: ${ROUTINE_TIERS.join(', ')}` },
      ],
    }
  }
  const target: RoutineTier = request.targetTier
  const rejections: Rejection[] = []

  if (live.personaSlug !== grid.persona) {
    rejections.push({
      code: 'persona_missing',
      message: `grid targets persona "${grid.persona}" but live config carries "${live.personaSlug}"`,
    })
  }
  // Ceiling 1: the letter commitment. Non-raisable from this path.
  if (TIER_RANK[target] > TIER_RANK[row.ceiling_tier]) {
    rejections.push({
      code: 'above_letter_ceiling',
      message: `"${row.routine}" is committed at a ceiling of ${row.ceiling_tier} (${row.ceiling_verbatim}); raising it is a commitment change, not a settings change`,
    })
  }
  const sendClass = sendActionClassOf(row)
  if (sendClass === null && TIER_RANK[target] > TIER_RANK['flag-only']) {
    rejections.push({
      code: 'no_graduation_path',
      message: `"${row.routine}" authors no send action class — its skills carry no draft or send tool, so there is no path above flag-only`,
    })
  }
  // A key the Machine cannot index is refused BEFORE anything is posted
  // (ss#2314). The seat would reject the write itself (409, unknown action
  // class), but only after the console had already computed a `fromTier` from
  // a lookup that could never hit — so the client saw a generic transport
  // error for a config defect. Rejecting here names the actual cause, and
  // covers a grid that reached this path unvalidated (the D1 projection is not
  // re-run through validateRoutineGrid).
  if (sendClass !== null && !isHonoredActionClass(sendClass)) {
    rejections.push({
      code: 'unknown_exposure_key',
      message: `"${row.routine}" is configured with the exposure key "${sendClass}", which is not an action class the Operator can enforce; its level cannot be changed until the configuration is corrected`,
    })
  }
  return rejections.length > 0 ? { ok: false, rejections } : { ok: true, row, target }
}

export function compileTierChange(
  grid: RoutineGrid,
  live: LiveExposure,
  request: { routine: string; targetTier: string; vertical: string | null }
): TierChangeResult {
  const guard = guardRequest(grid, live, request)
  if (!guard.ok) return { ok: false, rejections: guard.rejections }
  const { row, target } = guard
  const sendClass = sendActionClassOf(row)

  const fromTier = liveTierOf(row, live)
  if (fromTier === target) {
    return {
      ok: true,
      delta: {
        routine: row.routine,
        skills: row.skills,
        fromTier,
        toTier: target,
        exposureChanges: [],
        noop: true,
      },
    }
  }

  // sendClass is non-null here: a null sendClass forces fromTier === 'flag-only'
  // and rejects any target above it, so an equal-tier no-op already returned.
  const actionClass = sendClass as string
  const fromValue = asCeiling(live.exposure[actionClass])
  const toValue = TIER_SEND_CEILING[target]

  // Ceiling 2: the vertical floor (non-raisable, ADR 0025). A floor binds the
  // TARGET value; an unauthored target (flag-only) is always at-or-below any
  // floor because unauthored is fail-closed.
  if (toValue !== null) {
    const floor = getVerticalFloor(request.vertical, actionClass as never)
    if (floor !== null && restrictiveness(toValue) < restrictiveness(floor)) {
      return {
        ok: false,
        rejections: [
          {
            code: 'below_vertical_floor',
            message: `${actionClass} may not be less restrictive than the ${request.vertical} floor (${floor})`,
          },
        ],
      }
    }
  }

  const direction: ExposureChange['direction'] =
    fromValue === null
      ? 'authorize'
      : toValue === null
        ? 'deauthorize'
        : changeDirection(fromValue, toValue)

  return {
    ok: true,
    delta: {
      routine: row.routine,
      skills: row.skills,
      fromTier,
      toTier: target,
      exposureChanges: [
        { personaSlug: live.personaSlug, actionClass, from: fromValue, to: toValue, direction },
      ],
      noop: false,
    },
  }
}

/**
 * Every tier a routine may legally be set to from the portal, given its
 * letter ceiling and whether it has a send class at all. Drives the control's
 * option list so a client is never offered a choice the compiler will reject.
 */
export function selectableTiers(row: RoutineGridRow): readonly RoutineTier[] {
  const hasSend = sendActionClassOf(row) !== null
  return ROUTINE_TIERS.filter((t) => {
    if (TIER_RANK[t] > TIER_RANK[row.ceiling_tier]) return false
    if (!hasSend && TIER_RANK[t] > TIER_RANK['flag-only']) return false
    return true
  })
}
