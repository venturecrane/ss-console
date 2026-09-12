import { readdirSync, existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * The inbox router is the only door an inbound email has. A class that exists
 * in SKILL.md but not in `references/routing-rubric.md` is a class the router
 * cannot see on the scheduled-poll and Claude channels, where the rubric is the
 * text it reads; the rubric calls itself the source of truth and was source of
 * truth for 9 of 16 routing targets when this file was written (2026-09-11).
 *
 * The chronology class is pinned separately because it is the one routing class
 * that SPENDS the firm's authored page allowance, so two properties of it are
 * load-bearing rather than stylistic: that it is reachable at all, and that it
 * stays admin-reserved.
 *
 * WHY NOTHING HERE USES A LINE NUMBER. The change that introduced this file
 * inserted a table row and a bullet, moving every line after them. Positional
 * scoping would have been wrong on arrival. Everything is located by heading
 * and marker text instead.
 */

const SKILLS_DIR = 'operator/skills'
const ROUTER = `${SKILLS_DIR}/matter-inbox-router/SKILL.md`
const RUBRIC = `${SKILLS_DIR}/matter-inbox-router/references/routing-rubric.md`
const DRAFTING_DISCIPLINE = 'operator/templates/drafting/drafting-discipline.md'
const CHRONOLOGY = 'medical-chronology-maintainer'

/**
 * Slugs allowed to appear as a routing target with no rubric row. Empty on
 * purpose: every exemption is a class the router can reach and the rubric
 * cannot describe. A reason under 20 characters is not a reason.
 */
const ROUTER_RUBRIC_EXEMPT: Record<string, string> = {}

const read = (p: string) => readFileSync(p, 'utf8')

/** Collapse wrapping so a match cannot fail on where a line happened to break. */
const flat = (s: string) => s.replace(/\s+/g, ' ')

/** The class table: the markdown table under the routing-decision heading. */
function classTable(body: string): string {
  const lines = body.split('\n')
  const start = lines.findIndex((l) => l.startsWith('| Inbound class'))
  if (start < 0) throw new Error('class table not found under "## The routing decision"')
  const end = lines.findIndex((l, i) => i > start && !l.startsWith('|'))
  return lines.slice(start, end).join('\n')
}

/** The per-class handling bullets under step 4 of Phase 2. */
function classBullets(body: string): string {
  const lines = body.split('\n')
  const start = lines.findIndex((l) => l.trimStart().startsWith('- **'))
  if (start < 0) throw new Error('per-class bullets not found')
  const end = lines.findIndex((l, i) => i > start && /^\d+\. /.test(l))
  return lines.slice(start, end < 0 ? undefined : end).join('\n')
}

/** One class's bullet, from its bold marker to the next bullet. */
function bulletFor(body: string, marker: string): string {
  const bullets = classBullets(body)
  const at = bullets.indexOf(marker)
  if (at < 0) throw new Error(`no bullet marked ${marker}`)
  const next = bullets.indexOf('\n   - **', at + 1)
  return bullets.slice(at, next < 0 ? undefined : next)
}

const skillSlugs = () =>
  new Set(readdirSync(SKILLS_DIR).filter((d) => existsSync(`${SKILLS_DIR}/${d}/SKILL.md`)))

/** Every skill slug named as a routing target in the two routing regions. */
function routingTargets(body: string): Set<string> {
  const slugs = skillSlugs()
  const region = `${classTable(body)}\n${classBullets(body)}`
  const found = new Set<string>()
  for (const [, tok] of region.matchAll(/`([a-z][a-z0-9-]+)`/g)) {
    if (slugs.has(tok) && tok !== 'matter-inbox-router') found.add(tok)
  }
  return found
}

describe('matter-inbox-router: the chronology class', () => {
  it('is reachable on the email channel', () => {
    const bullet = flat(bulletFor(read(ROUTER), '**Chronology package request**'))
    // A RUNTIME path. A repo path (operator/skills/...) does not exist on the
    // seat, so the read would fail on the one channel this class serves.
    expect(bullet).toContain(`/app/skills/${CHRONOLOGY}/SKILL.md`)
    expect(bullet).toContain('medchron_allowance')
    expect(flat(classTable(read(ROUTER)))).toContain('Chronology package request')
  })

  it('stays admin-reserved', () => {
    // The allowance is a commercial term. If a later edit keeps the class and
    // drops the reservation, the firm's page allowance is open to every
    // rostered sender and nothing else in the repo would notice.
    const bullet = flat(bulletFor(read(ROUTER), '**Chronology package request**'))
    expect(bullet).toContain('INITIATION AUTHORITY')
    expect(bullet).toContain('Admin-classed')
    expect(bullet).toContain('requested_by')
  })

  it('is not in the drafting lane', () => {
    // The skill is content_ceiling: surface_only, tagged NeverDraft, and its own
    // selector test pins that it DECLINES a drafting ask. Two plausible future
    // edits break that: "completing" the drafting lane map with the fifth PI
    // skill, or folding the chronology ask into the drafting bullet because
    // both execute in-turn.
    expect(read(DRAFTING_DISCIPLINE)).not.toContain(CHRONOLOGY)
    expect(bulletFor(read(ROUTER), '**Attorney drafting request**')).not.toContain(CHRONOLOGY)
  })
})

describe('matter-inbox-router: SKILL.md and the rubric agree', () => {
  it('gives every routing target a rubric row', () => {
    const rubric = read(RUBRIC)
    const missing = [...routingTargets(read(ROUTER))]
      .filter((slug) => !rubric.includes(slug) && !(slug in ROUTER_RUBRIC_EXEMPT))
      .sort()
    expect(missing, `routing targets with no rubric row: ${missing.join(', ')}`).toEqual([])
  })

  it('requires a real reason for any exemption', () => {
    for (const [slug, reason] of Object.entries(ROUTER_RUBRIC_EXEMPT)) {
      expect(reason.length, `${slug}'s exemption reason is too short to review`).toBeGreaterThan(20)
    }
  })

  it('scopes to routing regions only, never to the whole body', () => {
    // The falsifier, stated without a line number. Phase 1 names `inbox-triage`
    // to compare SHAPES ("the shape matches inbox-triage Phase 1"); it is not a
    // routing target. A region that swallowed it would demand a rubric row for
    // a class that does not exist, and the "fix" would be a bogus row.
    const body = read(ROUTER)
    expect(body).toContain('inbox-triage')
    expect([...routingTargets(body)]).not.toContain('inbox-triage')
  })

  it('cites a rubric that exists', () => {
    // Deliberately narrow. Dead `references/*.md` pointers in general are
    // already policed by tests/shipped-runtime-paths.test.ts, which carries a
    // reviewed freeze list (this router has two entries on it). A second gate
    // reaching a different verdict on the same fact is worse than one gate, so
    // this only asserts the file THIS suite reads.
    expect(read(ROUTER)).toContain('references/routing-rubric.md')
    expect(existsSync(RUBRIC)).toBe(true)
  })
})
