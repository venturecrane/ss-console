/**
 * Governance matrix reader + cell resolver for the admin Operator console
 * governance surface (`/admin/operator/[customer]/governance`).
 *
 * The surface shows persona-level exposure per action class plus skill-level
 * initiation. The rule that must not bend: an action class with no authored
 * exposure renders **unconfigured → fail-closed**, never a presumed
 * "draft_for_review."
 *
 * Defensive parse: a malformed row degrades to an explicit error, not a crash.
 *
 * The floor lookup and the restrictiveness ordering are the frozen governance
 * foundation (config-governance.ts); this module imports them read-only and adds
 * no new floor source.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  getVerticalFloor,
  restrictiveness,
  isCeiling,
  type Ceiling,
} from '../portal/operator/config-governance'
import {
  ACCEPTED_ACTION_CLASSES,
  type ActionClass,
  type AuthoredExposureActionClass,
  type SkillInitiation,
} from '../operator/customer-yaml/types'

export interface GovernanceSkill {
  name: string
  enabled: boolean
  initiation: SkillInitiation
}

export interface GovernancePersona {
  slug: string
  name: string
  status: string
  exposure: Partial<Record<AuthoredExposureActionClass, Ceiling>>
  skills: GovernanceSkill[]
}

export type GovernanceRead =
  | { ok: true; vertical: string | null; personas: GovernancePersona[] }
  | { ok: false; error: 'not_found' | 'malformed' }

interface ConfigRow {
  personas_json: string
  vertical: string | null
}

/**
 * Read the governance-relevant config for one customer by slug. Parses
 * `personas_json` for persona exposure, skill initiation, and enabled state,
 * which the frozen projection omits. Returns a typed error rather than throwing.
 */
export async function readGovernanceConfig(db: D1Database, slug: string): Promise<GovernanceRead> {
  const row = await db
    .prepare('SELECT personas_json, vertical FROM customer_configs WHERE customer_slug = ?')
    .bind(slug)
    .first<ConfigRow>()
  if (!row) return { ok: false, error: 'not_found' }
  try {
    return { ok: true, vertical: row.vertical, personas: parsePersonas(row.personas_json) }
  } catch {
    return { ok: false, error: 'malformed' }
  }
}

function parsePersonas(raw: string): GovernancePersona[] {
  const parsed: unknown = JSON.parse(raw)
  if (!Array.isArray(parsed)) throw new Error('personas_json is not an array')
  return parsed.map((p) => {
    if (typeof p !== 'object' || p === null) throw new Error('persona is not an object')
    const rec = p as Record<string, unknown>
    return {
      slug: typeof rec.slug === 'string' ? rec.slug : '',
      name: typeof rec.name === 'string' ? rec.name : '',
      status: typeof rec.status === 'string' ? rec.status : 'unknown',
      exposure: parseExposure(rec.entitlements),
      skills: Array.isArray(rec.skills) ? rec.skills.map(parseSkill) : [],
    }
  })
}

function parseSkill(s: unknown): GovernanceSkill {
  if (typeof s !== 'object' || s === null) throw new Error('skill is not an object')
  const rec = s as Record<string, unknown>
  return {
    name: typeof rec.name === 'string' ? rec.name : '',
    enabled: rec.enabled !== false, // default-enabled unless explicitly false
    initiation: parseInitiation(rec.initiation),
  }
}

function parseExposure(raw: unknown): Partial<Record<AuthoredExposureActionClass, Ceiling>> {
  const out: Partial<Record<AuthoredExposureActionClass, Ceiling>> = {}
  if (typeof raw !== 'object' || raw === null) return out
  const entitlements = raw as Record<string, unknown>
  const exposure = entitlements.exposure
  if (typeof exposure !== 'object' || exposure === null) return out
  const rec = exposure as Record<string, unknown>
  for (const cls of ACCEPTED_ACTION_CLASSES) {
    if (cls === 'read') continue
    const v = rec[cls]
    if (isCeiling(v)) out[cls] = v
  }
  return out
}

function parseInitiation(raw: unknown): SkillInitiation {
  if (typeof raw !== 'object' || raw === null) {
    return { manual: false, scheduled: false, webhook: false }
  }
  const rec = raw as Record<string, unknown>
  return {
    manual: rec.manual === true,
    scheduled: rec.scheduled === true,
    webhook: rec.webhook === true,
  }
}

// ===========================================================================
// Cell resolution (pure) — the ADR-0035 keystone
// ===========================================================================

export type CellStatus = 'authored' | 'fail_closed'

export interface GovernanceCell {
  actionClass: ActionClass
  /** SMD-set non-raisable floor for this class on this vertical, or null. */
  floor: Ceiling | null
  /** The authored ceiling for this class, or null when unauthored. */
  authored: Ceiling | null
  /**
   * The effective ceiling: the most restrictive of {floor, authored}. Null ONLY
   * when the class is unauthored — in which case status is `fail_closed` and the
   * surface must render "unconfigured", never a default value (ADR 0035).
   */
  effective: Ceiling | null
  status: CellStatus
}

/**
 * Resolve one persona exposure × action-class cell. `read` is enforcement-allowed
 * and not customer-authored; every other class is authored only via sparse
 * persona exposure. An unauthored class is fail-closed with no invented value.
 */
export function resolveCell(
  exposure: Partial<Record<AuthoredExposureActionClass, Ceiling>>,
  actionClass: ActionClass,
  vertical: string | null
): GovernanceCell {
  const floor = getVerticalFloor(vertical, actionClass)
  const authored: Ceiling | null =
    actionClass === 'read' ? 'autonomous' : (exposure[actionClass] ?? null)

  if (authored === null) {
    return { actionClass, floor, authored: null, effective: null, status: 'fail_closed' }
  }
  const effective = mostRestrictive(floor, authored)
  return { actionClass, floor, authored, effective, status: 'authored' }
}

/** Most restrictive of an optional floor and an authored value. */
function mostRestrictive(floor: Ceiling | null, authored: Ceiling): Ceiling {
  if (floor === null) return authored
  return restrictiveness(floor) > restrictiveness(authored) ? floor : authored
}

/** Resolve every action-class cell for a skill, in canonical class order. */
export function resolveSkillCells(
  exposure: Partial<Record<AuthoredExposureActionClass, Ceiling>>,
  vertical: string | null
): GovernanceCell[] {
  return ACCEPTED_ACTION_CLASSES.map((cls) => resolveCell(exposure, cls, vertical))
}

/** Human label for a ceiling value (display only). */
export function ceilingLabel(ceiling: Ceiling): string {
  switch (ceiling) {
    case 'autonomous':
      return 'Autonomous'
    case 'confirm':
      return 'Confirm to send'
    case 'draft_for_review':
      return 'Draft for review'
    case 'refused':
      return 'Refused'
  }
}

/** Human label for an action class (display only). */
export function actionClassLabel(actionClass: ActionClass): string {
  switch (actionClass) {
    case 'read':
      return 'Read'
    case 'internal_write':
      return 'Internal write'
    case 'external_send':
      return 'External send (outside)'
    case 'external_send_internal':
      return 'Internal send (staff)'
    case 'external_send_client':
      return 'Client send'
    case 'external_send_vendor':
      return 'Records-vendor send'
    case 'external_send_as_staff':
      return 'Send as staff (on their approval)'
    case 'commitment':
      return 'Commitment'
    case 'destructive':
      return 'Destructive'
    case 'code_execution':
      return 'Code execution'
  }
}
