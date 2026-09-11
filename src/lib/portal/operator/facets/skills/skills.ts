/**
 * Operator SKILLS facet — the shared inventory + initiation view model (ADR 0069
 * Slice 3; signed-off brief docs/design/operator/surface-briefs/operator-skills.md).
 *
 * Answers the console's central "what can it do?" question: the skills configured
 * on the active persona, in authored order, plus HOW each one gets set in motion
 * (its initiation modes). One shared resolver + one shared viewer
 * (`components/portal/operator/facets/OperatorSkills.astro`), mounted in both
 * portals per Lock 4 — the facet registry points `skills` here.
 *
 * Honesty (brief §5): only what the projection carries. The portal holds the
 * skill SLUG, never the `SKILL.md` prose, so a display name is a REFORMAT of the
 * slug (`humanizeSlug`), never an invented description sentence. We deliberately
 * do NOT surface:
 *   - enabled / on-off — the projection dropped `enabled`; Configure's "On" is a
 *     hardcoded constant (settings.ts::skillToggleRowsFromPersona), not real
 *     state, so reproducing it would be theater;
 *   - version / cost / scope — dropped from the projection;
 *   - trust ceiling / exposure / autonomy — that is the Governance facet (Lock 4:
 *     one viewer per facet; Skills is inventory + initiation only).
 *
 * Pure and total: a null config, no active persona, or an empty skills list all
 * yield an empty list, and the viewer renders the honest empty state
 * (docs/style/empty-state-pattern.md) — never a fabricated placeholder row.
 */

import type { CustomerConfigRow, PersonaSkill } from '../../../customer-config'
import type { SkillInitiation } from '../../../../operator/customer-yaml/types'
import { SKILL_SUMMARIES } from './skill-summaries'
import { scheduleDetailBySkill } from '../schedule/schedule'

/**
 * Reformat a skill slug into a client-legible display name — SENTENCE case
 * (brief §5: `matter-inbox-router` → "Matter inbox router"), so a skill reads
 * like a capability, not a shouting label. This is a pure reformat of the slug,
 * never an invented description sentence; the portal holds only the slug. Kept
 * separate from `offerings.humanizeSlug` (Title case, for operator display
 * NAMES) because these are different concerns: a name is a proper noun, a skill
 * is a description of a capability.
 */
export function humanizeSkillName(slug: string): string {
  const words = slug.split('-').filter(Boolean)
  if (words.length === 0) return ''
  const [first, ...rest] = words
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}

export interface OperatorSkillView {
  /** Humanized display name, e.g. "matter-inbox-router" → "Matter inbox router". */
  name: string
  /** The raw authored slug — stable key / title, never shown as prose. */
  slug: string
  /**
   * Client-facing one-line "what it does", from the reviewed SKILL_SUMMARIES
   * catalog. null when the skill has no authored summary (renders name-only —
   * honest degradation, never a fabricated line).
   */
  summary: string | null
  /** Client-legible initiation labels; empty when no mode is set (show nothing). */
  initiation: string[]
  /**
   * Plain-language schedule prose from the projected cron entries via the
   * work facet's deterministic describer ("Weekdays at 7:17 a.m."), or null
   * (nothing scheduled / not describable — never mistranslated).
   */
  scheduleDetail: string | null
}

export interface OperatorSkillsModel {
  skills: OperatorSkillView[]
}

/**
 * Map the three initiation booleans to client-legible labels, in a stable order
 * (request → schedule → event). When every mode is false, returns [] so the
 * viewer shows nothing rather than imply a trigger the skill does not carry.
 */
export function initiationLabels(init: SkillInitiation): string[] {
  const labels: string[] = []
  if (init.manual) labels.push('On request')
  if (init.scheduled) labels.push('On a schedule')
  if (init.webhook) labels.push('When something happens')
  return labels
}

/**
 * Compose the Skills view model from the config projection. Selects the active
 * persona (status === 'active', the same rule as the identity hero) and maps its authored skill list,
 * preserving order. No second DB read — the caller passes the already-projected,
 * typed config.
 */
export function resolveOperatorSkills(config: CustomerConfigRow | null): OperatorSkillsModel {
  const persona = config?.personas.find((p) => p.status === 'active') ?? null
  const schedules = scheduleDetailBySkill(persona?.cron ?? [])
  const skills: OperatorSkillView[] = (persona?.skills ?? []).map((s: PersonaSkill) => ({
    name: humanizeSkillName(s.name),
    slug: s.name,
    summary: SKILL_SUMMARIES[s.name] ?? null,
    initiation: initiationLabels(s.initiation),
    scheduleDetail: schedules.get(s.name) ?? null,
  }))
  return { skills }
}
