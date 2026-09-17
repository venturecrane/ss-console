import { describe, it, expect } from 'vitest'
import {
  startsLabels,
  resolveOperatorWork,
  resolveStandingCaps,
  mappedBannedTools,
} from '../src/lib/portal/operator/facets/work/work'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import path from 'node:path'
import { SKILL_SUMMARIES } from '../src/lib/portal/operator/facets/skills/skill-summaries'
import type {
  CustomerConfigRow,
  PersonaConfig,
  PersonaSkill,
} from '../src/lib/portal/customer-config'
import type {
  RoutineGrid,
  RoutineGridRow,
  RoutineGridEnforcement,
  RoutineTier,
} from '../src/lib/operator/routine-grid'
import type { SkillInitiation } from '../src/lib/operator/customer-yaml/types'

/**
 * Operator "The work" facet resolver (ADR 0076; console structure doc §3.2).
 * Read-only projection of the routine grid (ADR 0075) into the lifecycle-grouped
 * view model, plus the gridless skills fallback. Every rendered fact traces to
 * an authored grid field, the fixed tier / starts maps, or the reviewed skill
 * summaries — nothing invented.
 *
 * Fixtures carry NO vertical vocabulary: the grid supplies the vertical as DATA
 * at runtime, so the resolver (and these fixtures) stay neutral.
 */

// 'inbox-triage' is a real, vertical-neutral entry in the reviewed summaries
// catalog — used to prove a summary is attached from the catalog, never invented.
// (Was 'health-monitor' until that skill was ripped 2026-07-24.)
const SUMMARIZED_SLUG = 'inbox-triage'

function enforcement(p: Partial<RoutineGridEnforcement> = {}): RoutineGridEnforcement {
  return {
    initiation: 'manual',
    exposure_keys: {},
    content_floor: false,
    banned_tools: [],
    notes: '',
    ...p,
  }
}

function row(p: Partial<RoutineGridRow> = {}): RoutineGridRow {
  // Ceiling defaults to the start tier (no graduation headroom) unless the
  // fixture sets it explicitly. Tiers are applied LAST so the computed defaults
  // win over the base literals, without duplicating keys across the spread.
  const start_tier: RoutineTier = p.start_tier ?? 'flag-only'
  const ceiling_tier: RoutineTier = p.ceiling_tier ?? start_tier
  return {
    routine: 'A routine',
    letter_section: 'Section one',
    skills: [],
    start_verbatim: 'We surface it.',
    ceiling_verbatim: 'We surface it.',
    enforcement: enforcement(),
    ...p,
    start_tier,
    ceiling_tier,
  }
}

function grid(rows: RoutineGridRow[]): RoutineGrid {
  return {
    adr: 'ADR 0075',
    seat: 'test-seat',
    persona: 'test-persona',
    source_letter: 'test-letter',
    rows,
  }
}

/** Grid-mode config: only routine_grid is read in this branch. */
/**
 * Grid-mode config. The grid persona carries `internal_write: autonomous`
 * because a dial-less routine's level is the lower of its authored tier and its
 * live writing ceiling (see resolveLiveTier): with no writing permission the row
 * correctly reads "not currently authorized", which is a different assertion
 * than the tier mapping these fixtures exercise.
 */
function gridConfig(rows: RoutineGridRow[]): CustomerConfigRow {
  return {
    routine_grid: grid(rows),
    personas: [
      {
        slug: 'test-persona',
        status: 'active',
        entitlements: { exposure: { internal_write: 'autonomous' } },
      },
    ],
  } as unknown as CustomerConfigRow
}

// --- gridless-mode fixtures (mirror the skills resolver's inputs) -------------

function init(p: Partial<SkillInitiation> = {}): SkillInitiation {
  return { manual: false, scheduled: false, webhook: false, ...p }
}

function skill(name: string, i: Partial<SkillInitiation> = {}): PersonaSkill {
  return { name, initiation: init(i) }
}

function persona(p: Partial<PersonaConfig>): PersonaConfig {
  return {
    slug: 'p',
    status: 'active',
    name: 'X',
    title: null,
    signature_html: null,
    tone: [],
    send_as: null,
    entitlements: {} as PersonaConfig['entitlements'],
    skills: [],
    channel_bindings: [],
    ...p,
  }
}

/** Gridless-mode config: no grid, skills read from the active persona. */
function gridlessConfig(personas: PersonaConfig[]): CustomerConfigRow {
  return { routine_grid: null, personas } as unknown as CustomerConfigRow
}

describe('startsLabels', () => {
  it('maps each initiation mode to its client-legible label by substring detection', () => {
    expect(startsLabels('manual')).toEqual(['On request'])
    expect(startsLabels('scheduled')).toEqual(['On a schedule'])
    expect(startsLabels('webhook')).toEqual(['When something happens'])
  })

  it('detects modes inside a free-text initiation string, in a stable order', () => {
    // Words appear out of canonical order in the source string; output is fixed
    // request → schedule → event regardless.
    expect(startsLabels('webhook and scheduled; also manual')).toEqual([
      'On request',
      'On a schedule',
      'When something happens',
    ])
  })

  it('returns [] when no known mode is present (viewer shows nothing)', () => {
    expect(startsLabels('')).toEqual([])
    expect(startsLabels('something else entirely')).toEqual([])
  })
})

describe('resolveOperatorWork — grid mode', () => {
  it('groups rows into sections by letter_section in first-appearance order, preserving row order', () => {
    const model = resolveOperatorWork(
      gridConfig([
        row({ routine: 'First', letter_section: 'Alpha' }),
        row({ routine: 'Second', letter_section: 'Beta' }),
        row({ routine: 'Third', letter_section: 'Alpha' }),
      ])
    )
    expect(model.mode).toBe('grid')
    if (model.mode !== 'grid') return
    expect(model.sections.map((s) => s.name)).toEqual(['Alpha', 'Beta'])
    expect(model.sections[0].routines.map((r) => r.routine)).toEqual(['First', 'Third'])
    expect(model.sections[1].routines.map((r) => r.routine)).toEqual(['Second'])
  })

  it('renders each tier as its locked plain sentence for the Today line', () => {
    const model = resolveOperatorWork(
      gridConfig([
        row({ routine: 'a', start_tier: 'flag-only' }),
        row({ routine: 'b', start_tier: 'prepare-and-route' }),
        row({ routine: 'c', start_tier: 'auto-handle' }),
      ])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    const today = model.sections[0].routines.map((r) => r.todaySentence)
    expect(today).toEqual(['Surfaces it', 'Prepares it for you', 'Handles it'])
  })

  it('renders the LIVE level, not the grid start, when a client has moved the dial', () => {
    // 2026-09-17: Duties rendered `start_tier` while Settings rendered the live
    // override, so the two pages contradicted each other after a client change.
    const sendRow = row({
      routine: 'Client chase',
      start_tier: 'prepare-and-route',
      ceiling_tier: 'auto-handle',
      ceiling_verbatim: 'Auto-handle (once you are comfortable)',
      enforcement: enforcement({ exposure_keys: { external_send_client: 'draft_for_review' } }),
    })
    const config = {
      routine_grid: grid([sendRow]),
      personas: [
        {
          slug: 'test-persona',
          status: 'active',
          entitlements: { exposure: { external_send_client: 'draft_for_review' } },
        },
      ],
    } as unknown as CustomerConfigRow

    const authored = resolveOperatorWork(config, { confirmed: true, overrides: {} })
    if (authored.mode !== 'grid') throw new Error('expected grid mode')
    expect(authored.sections[0].routines[0].todaySentence).toBe('Prepares it for you')
    expect(authored.sections[0].routines[0].canBecomeSentence).toBe('Handles it')
    expect(authored.sections[0].routines[0].levelUnconfirmed).toBe(false)

    const raised = resolveOperatorWork(config, {
      confirmed: true,
      overrides: { external_send_client: 'autonomous' },
    })
    if (raised.mode !== 'grid') throw new Error('expected grid mode')
    // The client raised it: Duties says so, and there is no headroom left.
    expect(raised.sections[0].routines[0].todaySentence).toBe('Handles it')
    expect(raised.sections[0].routines[0].canBecomeSentence).toBeNull()
  })

  it('marks each row unconfirmed when the Machine did not answer, and never invents a level', () => {
    const config = gridConfig([row({ start_tier: 'auto-handle' })])
    const unreached = resolveOperatorWork(config, { confirmed: false, overrides: {} })
    if (unreached.mode !== 'grid') throw new Error('expected grid mode')
    expect(unreached.levelsConfirmed).toBe(false)
    expect(unreached.sections[0].routines[0].levelUnconfirmed).toBe(true)

    // No live read supplied at all (the admin config surface): no claim either way.
    const authoredOnly = resolveOperatorWork(config)
    if (authoredOnly.mode !== 'grid') throw new Error('expected grid mode')
    expect(authoredOnly.levelsConfirmed).toBeNull()
    expect(authoredOnly.sections[0].routines[0].levelUnconfirmed).toBe(false)
  })

  it('says a routine is not authorized when its writing permission is off', () => {
    const noWrite = {
      routine_grid: grid([row({ routine: 'Chronology', start_tier: 'auto-handle' })]),
      personas: [{ slug: 'test-persona', status: 'active', entitlements: { exposure: {} } }],
    } as unknown as CustomerConfigRow
    const model = resolveOperatorWork(noWrite, { confirmed: true, overrides: {} })
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    expect(model.sections[0].routines[0].todaySentence).toBe('Not currently authorized')
    expect(model.sections[0].routines[0].canBecomeSentence).toBeNull()
  })

  it('shows the starting setting verbatim only when it says more than the tier name', () => {
    const model = resolveOperatorWork(
      gridConfig([
        row({ routine: 'plain', start_tier: 'flag-only', start_verbatim: 'Flag-only' }),
        row({
          routine: 'defined',
          start_tier: 'auto-handle',
          start_verbatim: 'On request (a run builds it, an update brings it current)',
          start_tier_note: 'Normalizes to auto-handle: no per-item human step after the request.',
        }),
      ])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    const [plain, defined] = model.sections[0].routines
    expect(plain.startDetail).toBeNull()
    expect(defined.startDetail).toBe('On request (a run builds it, an update brings it current)')
  })

  it('surfaces a Can-become sentence + verbatim when the ceiling exceeds the start (headroom)', () => {
    const model = resolveOperatorWork(
      gridConfig([
        row({
          start_tier: 'prepare-and-route',
          ceiling_tier: 'auto-handle',
          ceiling_verbatim: 'You may authorize sending on its own.',
        }),
      ])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    const r = model.sections[0].routines[0]
    expect(r.canBecomeSentence).toBe('Handles it')
    expect(r.canBecomeVerbatim).toBe('You may authorize sending on its own.')
    expect(r.capVerbatim).toBeNull()
  })

  it('renders the ceiling verbatim as the standing cap (no Can-become) when ceiling equals start', () => {
    const model = resolveOperatorWork(
      gridConfig([
        row({
          start_tier: 'flag-only',
          ceiling_tier: 'flag-only',
          ceiling_verbatim: 'It never acts on its own here.',
        }),
      ])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    const r = model.sections[0].routines[0]
    expect(r.canBecomeSentence).toBeNull()
    expect(r.canBecomeVerbatim).toBeNull()
    expect(r.capVerbatim).toBe('It never acts on its own here.')
  })

  it('parses the row initiation into client-legible starts labels', () => {
    const model = resolveOperatorWork(
      gridConfig([row({ enforcement: enforcement({ initiation: 'manual, scheduled' }) })])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    expect(model.sections[0].routines[0].startsLabels).toEqual(['On request', 'On a schedule'])
  })

  it('humanizes implementing-skill slugs and attaches the reviewed summary, null when uncatalogued', () => {
    const model = resolveOperatorWork(
      gridConfig([row({ skills: [SUMMARIZED_SLUG, 'a-skill-with-no-summary'] })])
    )
    if (model.mode !== 'grid') throw new Error('expected grid mode')
    const skills = model.sections[0].routines[0].skills
    expect(skills[0]).toEqual({
      name: 'Inbox triage',
      slug: SUMMARIZED_SLUG,
      summary: SKILL_SUMMARIES[SUMMARIZED_SLUG],
    })
    expect(skills[1].summary).toBeNull()
  })
})

describe('resolveOperatorWork — gridless fallback', () => {
  it('degrades to the skills inventory (identical to the Skills resolver) when no grid is projected', () => {
    const model = resolveOperatorWork(
      gridlessConfig([
        persona({
          skills: [
            skill('welcome-message', { webhook: true }),
            skill('weekly-summary', { scheduled: true }),
          ],
        }),
      ])
    )
    expect(model.mode).toBe('gridless')
    if (model.mode !== 'gridless') return
    expect(model.skills.map((s) => s.slug)).toEqual(['welcome-message', 'weekly-summary'])
    expect(model.skills[0].name).toBe('Welcome message')
    expect(model.skills[0].initiation).toEqual(['When something happens'])
    expect(model.skills[1].initiation).toEqual(['On a schedule'])
  })

  it('is gridless with an empty skills list when config is null (honest empty state)', () => {
    const model = resolveOperatorWork(null)
    expect(model.mode).toBe('gridless')
    if (model.mode !== 'gridless') return
    expect(model.skills).toEqual([])
  })

  it('is gridless with an empty skills list when a gridless config has no active persona', () => {
    const model = resolveOperatorWork(
      gridlessConfig([persona({ status: 'archived', skills: [skill('x', { manual: true })] })])
    )
    if (model.mode !== 'gridless') throw new Error('expected gridless mode')
    expect(model.skills).toEqual([])
  })
})

describe('authority view (console blueprint §4 — entitlements as one honest view)', () => {
  it('renders the active persona exposure map as plain rows in stable action-class order', () => {
    const model = resolveOperatorWork(
      gridlessConfig([
        persona({
          entitlements: {
            exposure: {
              external_send_client: 'draft_for_review',
              internal_write: 'autonomous',
              destructive: 'refused',
              external_send: 'confirm',
            },
          },
        }),
      ])
    )
    expect(model.authority).toEqual([
      { label: 'Writing inside your systems', sentence: 'Handles it on its own' },
      { label: 'Sending outside the firm', sentence: 'Asks first' },
      { label: 'Email to your clients', sentence: 'Prepares it for a person' },
      { label: 'Deleting or changing records', sentence: 'Never' },
    ])
  })

  it('unauthored classes NEVER render as rows (fail-closed stays invisible, not invented)', () => {
    const model = resolveOperatorWork(
      gridlessConfig([persona({ entitlements: { exposure: { internal_write: 'autonomous' } } })])
    )
    expect(model.authority).toHaveLength(1)
  })

  it('is empty for a null config, no active persona, or an empty exposure map', () => {
    expect(resolveOperatorWork(null).authority).toEqual([])
    expect(
      resolveOperatorWork(gridlessConfig([persona({ status: 'archived' })])).authority
    ).toEqual([])
    expect(
      resolveOperatorWork(gridlessConfig([persona({ entitlements: { exposure: {} } })])).authority
    ).toEqual([])
  })

  it('grid mode carries the same authority block', () => {
    const model = resolveOperatorWork({
      routine_grid: { adr: '0075', seat: 's', persona: 'p', source_letter: 'x', rows: [row()] },
      personas: [persona({ entitlements: { exposure: { internal_write: 'draft_for_review' } } })],
    } as unknown as CustomerConfigRow)
    expect(model.mode).toBe('grid')
    expect(model.authority).toEqual([
      { label: 'Writing inside your systems', sentence: 'Prepares it for a person' },
    ])
  })
})

describe('standing caps (banned_tools display map)', () => {
  const enforcement = (banned: string[]): RoutineGridEnforcement => ({
    initiation: 'manual',
    exposure_keys: {},
    content_floor: false,
    banned_tools: banned,
    notes: '',
  })
  const row = (banned: string[]): RoutineGridRow => ({
    routine: 'r',
    letter_section: 'S',
    skills: [],
    start_tier: 'prepare-and-route',
    ceiling_tier: 'prepare-and-route',
    start_verbatim: 'x',
    ceiling_verbatim: 'x',
    enforcement: enforcement(banned),
  })

  it('dedupes across rows and never renders a raw token', () => {
    const caps = resolveStandingCaps([
      row(['payments_*']),
      row(['payments_*', 'trust_ledger_write']),
      row(['some_unmapped_token']),
    ])
    expect(caps).toEqual(['Moving money or making payments', 'Posting to money ledgers'])
    expect(caps.join(' ')).not.toContain('_')
  })

  it('every banned_tools token authored in a shipped grid has a display sentence', () => {
    // Silent drops are silent caps: a token any real grid authors must be in
    // the closed display map, or the leave-alone box under-reports.
    const customersDir = path.resolve(__dirname, '../operator/customers')
    const mapped = new Set(mappedBannedTools())
    const missing: string[] = []
    for (const customer of readdirSync(customersDir)) {
      const gridPath = path.join(customersDir, customer, 'routine-grid.yaml')
      if (!existsSync(gridPath)) continue
      const text = readFileSync(gridPath, 'utf8')
      for (const m of text.matchAll(/banned_tools:\s*\[([^\]]*)\]/g)) {
        for (const raw of m[1].split(',')) {
          const token = raw.trim().replace(/^['"]|['"]$/g, '')
          if (token && !mapped.has(token)) missing.push(`${customer}: ${token}`)
        }
      }
    }
    expect(missing, `unmapped banned_tools tokens:\n${missing.join('\n')}`).toEqual([])
  })
})
