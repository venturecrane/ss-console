/**
 * Operator "The work" facet — the shared routine-grid view model (ADR 0076,
 * console structure doc §3.2; signed-off brief
 * docs/design/operator/surface-briefs/operator-the-work.md).
 *
 * Chapter 2 of the console is the spine: the rendered job description crossed
 * with an authority matrix. It answers the owner's three questions in one page —
 * "what does it do", "how does each duty start", and "how much does it do on its
 * own" — grouped by the lifecycle sections the authored grid names.
 *
 * This resolver is READ-ONLY and pure. It maps the projected routine grid
 * (CustomerConfigRow.routine_grid, ADR 0075) into a two-mode view model:
 *
 *   - GRID mode (a validated grid is projected): sections in grid order, each
 *     with its routines. Every rendered fact traces to an authored grid field —
 *     the tier SENTENCES and the "starts" LABELS below are the only fixed maps,
 *     and the skill summaries come from the existing reviewed catalog. No runtime
 *     paraphrase, no invented copy (Captain reset 2026-07-14: accurate read-only
 *     depiction only; graduation requests and a permanent-rules block are out).
 *
 *   - GRIDLESS mode (no grid — smd, Hosted Agent, any pre-grid seat, or a null
 *     config): the honest degradation, the exact skills + initiation inventory
 *     the Skills page renders today, reused via resolveOperatorSkills. No invented
 *     tiers, no empty grid scaffolding.
 *
 * This module carries NO vertical vocabulary: the lifecycle section names, the
 * routine names, and the letter's verbatim tier language all arrive as authored
 * DATA at runtime (correct — the grid supplies the vertical, the code stays
 * neutral).
 */

import type { CustomerConfigRow } from '../../../customer-config'
import type { RoutineGridRow, RoutineTier } from '../../../../operator/routine-grid'
import {
  ACCEPTED_ACTION_CLASSES,
  type AuthoredExposureActionClass,
  type ExposureCeiling,
} from '../../../../operator/customer-yaml/types'
import { humanizeSkillName, resolveOperatorSkills, type OperatorSkillView } from '../skills/skills'
import { scheduleDetailBySkill } from '../schedule/schedule'
import { SKILL_SUMMARIES } from '../skills/skill-summaries'

/**
 * Closed tier → plain-sentence map (locked, Captain 2026-07-14). The reader is a
 * colleague, not a technician: the internal token names never reach the page and
 * may change freely in code. The letter's own verbatim phrasing stays available
 * per row (start/ceiling verbatim) as the contract language.
 */
const TIER_SENTENCE: Record<RoutineTier, string> = {
  'flag-only': 'Surfaces it',
  'prepare-and-route': 'Prepares it for you',
  'auto-handle': 'Handles it',
}

/**
 * THE AUTHORITY VIEW (console blueprint §4 — the "entitlements as one honest
 * view" coverage gap). The active persona's authored exposure map, rendered as
 * one block: each authored action class in plain language with its ceiling as
 * a plain sentence. Sparse by design — an UNAUTHORED class fails closed at
 * runtime (ADR 0035) and renders only through the fixed footer sentence in the
 * viewer, never as an invented row. Both display maps are closed and
 * display-only; the authored tokens never reach the page.
 */
const AUTHORITY_CLASS_LABEL: Record<AuthoredExposureActionClass, string> = {
  internal_write: 'Writing inside your systems',
  external_send: 'Sending outside the firm',
  external_send_internal: 'Email to your own team',
  external_send_client: 'Email to your clients',
  external_send_vendor: 'Email to your vendors',
  commitment: 'Making commitments for the firm',
  destructive: 'Deleting or changing records',
  code_execution: 'Running code',
}

const CEILING_SENTENCE: Record<ExposureCeiling, string> = {
  autonomous: 'Handles it on its own',
  confirm: 'Asks first',
  draft_for_review: 'Prepares it for a person',
  refused: 'Never',
}

/** One authority row: an authored exposure ceiling in plain language. */
export interface WorkAuthorityRow {
  /** Plain-language action-class label from the closed map. */
  label: string
  /** The authored ceiling as a plain sentence. */
  sentence: string
}

/**
 * Map the active persona's sparse exposure map into authority rows, in the
 * stable ACCEPTED_ACTION_CLASSES order. Only authored keys render; `read` is
 * never authored (it is not an exposure class).
 */
function resolveAuthority(config: CustomerConfigRow | null): WorkAuthorityRow[] {
  const persona = config?.personas.find((p) => p.status === 'active') ?? null
  const exposure = persona?.entitlements.exposure ?? {}
  const rows: WorkAuthorityRow[] = []
  for (const ac of ACCEPTED_ACTION_CLASSES) {
    if (ac === 'read') continue
    const ceiling = exposure[ac]
    if (!ceiling) continue
    rows.push({ label: AUTHORITY_CLASS_LABEL[ac], sentence: CEILING_SENTENCE[ceiling] })
  }
  return rows
}

/** One implementing skill on a routine: humanized name + its reviewed summary. */
export interface WorkRoutineSkill {
  /** Humanized display name (e.g. "records-chaser" → "Records chaser"). */
  name: string
  /** The raw authored slug — stable key, never shown as prose. */
  slug: string
  /** Reviewed one-line summary from SKILL_SUMMARIES, or null (name-only). */
  summary: string | null
}

/** One routine row, every field traced to authored grid data or a fixed map. */
export interface WorkRoutineView {
  /** `routine` verbatim. */
  routine: string
  /**
   * Client-legible initiation labels parsed from the authored initiation string
   * ("On request" / "On a schedule" / "When something happens"), in the stable
   * order request → schedule → event. Empty when none detected.
   */
  startsLabels: string[]
  /** The full authored initiation string — shown at the row's detail level. */
  initiationDetail: string
  /** `start_tier` as the locked plain sentence — the row's "Today" line. */
  todaySentence: string
  /** `start_verbatim` — the letter's contract language for today's tier. */
  startVerbatim: string
  /**
   * `ceiling_tier` as the plain sentence, ONLY when the ceiling differs from the
   * start (a real graduation headroom exists). Null when ceiling equals start.
   */
  canBecomeSentence: string | null
  /** `ceiling_verbatim` — shown alongside canBecomeSentence. Null when no headroom. */
  canBecomeVerbatim: string | null
  /**
   * `ceiling_verbatim` rendered as the row's STANDING rule when ceiling equals
   * start (there is no graduation path; this is its maximum). Null otherwise.
   * Exactly one of canBecomeSentence / capVerbatim is non-null.
   */
  capVerbatim: string | null
  /** The implementing skill(s), humanized + summarized. */
  skills: WorkRoutineSkill[]
  /**
   * Plain-language schedule prose for the routine's scheduled skills
   * ("Weekdays at 7:17 a.m."), from the projected cron entries via the
   * deterministic describer. Null when nothing scheduled or not describable.
   */
  scheduleDetail: string | null
}

/** A lifecycle section: the authored `letter_section` name and its routines. */
export interface WorkSection {
  /** `letter_section` verbatim (authored data — carries the vertical). */
  name: string
  routines: WorkRoutineView[]
}

/**
 * The two-mode view model. GRID renders the lifecycle-grouped routine matrix;
 * GRIDLESS renders the Skills inventory unchanged (honest degradation), and the
 * viewer flags which introduction sentence to show.
 */
export type OperatorWorkModel =
  | {
      mode: 'grid'
      sections: WorkSection[]
      authority: WorkAuthorityRow[]
      standingCaps: string[]
    }
  | {
      mode: 'gridless'
      skills: OperatorSkillView[]
      authority: WorkAuthorityRow[]
      standingCaps: string[]
    }

/**
 * STANDING CAPS — the grid's authored `enforcement.banned_tools` tokens
 * rendered in plain language (Captain, 2026-07-15: the always-on hard limits,
 * like never moving money, must surface in "What it must leave alone" instead
 * of the box claiming nothing is blocked). Closed display map over authored
 * enforcement tokens, same pattern as the tier sentences: the token never
 * reaches the page, and an unmapped token renders nothing (the repo guard in
 * tests/operator-work-facet.test.ts keeps the map covering every token any
 * shipped grid authors, so nothing is silently dropped).
 */
const BANNED_TOOL_SENTENCE: Record<string, string> = {
  'payments_*': 'Moving money or making payments',
  trust_ledger_write: 'Posting to money ledgers',
  mcp_smokeball_create_matter: 'Creating new files in your practice management system',
}

/** Distinct plain-language standing caps across the grid, in first-appearance order. */
export function resolveStandingCaps(rows: readonly RoutineGridRow[]): string[] {
  const out: string[] = []
  for (const row of rows) {
    for (const tool of row.enforcement.banned_tools) {
      const sentence = BANNED_TOOL_SENTENCE[tool]
      if (sentence && !out.includes(sentence)) out.push(sentence)
    }
  }
  return out
}

/** The closed banned-tool display map's keys (for the repo coverage guard). */
/**
 * @public Coverage guard. tests/operator-work-facet.test.ts imports it to assert every banned tool
 * in the repo has a display sentence. No runtime caller, by design.
 */
export function mappedBannedTools(): string[] {
  return Object.keys(BANNED_TOOL_SENTENCE)
}

/**
 * Parse the authored free-text initiation string into the established plain
 * labels by substring detection of the three canonical modes. Order is fixed
 * (request → schedule → event), independent of where the words appear in the
 * string; empty array when none is present. The authored string itself is kept
 * verbatim on the row (initiationDetail) for the detail level.
 */
export function startsLabels(initiation: string): string[] {
  const s = initiation.toLowerCase()
  const labels: string[] = []
  if (s.includes('manual')) labels.push('On request')
  if (s.includes('scheduled')) labels.push('On a schedule')
  if (s.includes('webhook')) labels.push('When something happens')
  return labels
}

function toRoutineView(row: RoutineGridRow, schedules: Map<string, string>): WorkRoutineView {
  const graduates = row.ceiling_tier !== row.start_tier
  const scheduleProse = row.skills
    .map((slug) => schedules.get(slug))
    .filter((s): s is string => !!s)
  return {
    routine: row.routine,
    startsLabels: startsLabels(row.enforcement.initiation),
    initiationDetail: row.enforcement.initiation,
    todaySentence: TIER_SENTENCE[row.start_tier],
    startVerbatim: row.start_verbatim,
    canBecomeSentence: graduates ? TIER_SENTENCE[row.ceiling_tier] : null,
    canBecomeVerbatim: graduates ? row.ceiling_verbatim : null,
    capVerbatim: graduates ? null : row.ceiling_verbatim,
    skills: row.skills.map((slug) => ({
      name: humanizeSkillName(slug),
      slug,
      summary: SKILL_SUMMARIES[slug] ?? null,
    })),
    scheduleDetail: scheduleProse.length > 0 ? scheduleProse.join(' · ') : null,
  }
}

/**
 * Group rows into sections by `letter_section`, preserving each section's
 * first-appearance order and each row's order within it (grid order — the way
 * the client narrates the work to themselves, structure doc §2). The Map keeps
 * insertion order for its keys; `order` records first appearance explicitly for
 * clarity.
 */
function group(rows: readonly RoutineGridRow[], schedules: Map<string, string>): WorkSection[] {
  const order: string[] = []
  const bySection = new Map<string, WorkRoutineView[]>()
  for (const row of rows) {
    if (!bySection.has(row.letter_section)) {
      bySection.set(row.letter_section, [])
      order.push(row.letter_section)
    }
    bySection.get(row.letter_section)!.push(toRoutineView(row, schedules))
  }
  return order.map((name) => ({ name, routines: bySection.get(name)! }))
}

/**
 * Compose the "The work" view model from the config projection. When a validated
 * routine grid is present, render it lifecycle-grouped (grid mode). Otherwise —
 * no grid authored, or a null config — degrade to the Skills inventory
 * (gridless mode), reusing the Skills resolver so the fallback is identical to
 * today's Skills page.
 */
export function resolveOperatorWork(config: CustomerConfigRow | null): OperatorWorkModel {
  const authority = resolveAuthority(config)
  const grid = config?.routine_grid ?? null
  if (!grid) {
    return {
      mode: 'gridless',
      skills: resolveOperatorSkills(config).skills,
      authority,
      standingCaps: [],
    }
  }
  const persona = config?.personas.find((p) => p.status === 'active') ?? null
  const schedules = scheduleDetailBySkill(persona?.cron ?? [])
  return {
    mode: 'grid',
    sections: group(grid.rows, schedules),
    authority,
    standingCaps: resolveStandingCaps(grid.rows),
  }
}
