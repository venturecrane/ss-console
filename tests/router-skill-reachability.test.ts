import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { parse } from 'yaml'
import { describe, expect, it } from 'vitest'

/**
 * Every skill the router or self-initiation tells the model to READ is either
 * enabled on the seat or refused out loud (2026-10-06).
 *
 * The image ships every skill under /app/skills, and a seat's lane is
 * fail-closed by OMISSION from its customer.yaml. On 2026-10-06 the router told
 * the model to read_file an omitted drafting skill and carry it out, and it
 * did. The overlay now refuses to load an omitted skill's files (its skill
 * read fence answers "could not load ... say so plainly"). This test is the
 * other half: for the firm's seat, every skill named as a read target is either
 * enabled, or the sentence that names it already tells the model what to do
 * when the file will not load, so the refusal lands on a written instruction
 * rather than on an improvisation.
 */

const SEAT = 'operator/customers/ashton-price/customer.yaml'
const SKILLS = 'operator/skills'
const SOURCES = [
  `${SKILLS}/matter-inbox-router/SKILL.md`,
  `${SKILLS}/matter-inbox-router/references/routing-rubric.md`,
  `${SKILLS}/matter-inbox-router/references/algorithm.md`,
  `${SKILLS}/operator-self-initiation/SKILL.md`,
]
/** The router's own instruction for a skill file that will not load. */
const COULD_NOT_LOAD =
  /(cannot|will not|could not) read (the|that|this) skill file|if that file will not read|say so plainly/i

function enabledSkills(): Set<string> {
  const doc = parse(readFileSync(SEAT, 'utf8')) as {
    personas?: { skills?: { name?: string; enabled?: boolean }[] }[]
  }
  const out = new Set<string>()
  for (const p of doc.personas ?? [])
    for (const s of p.skills ?? []) if (s.name && s.enabled !== false) out.add(s.name)
  return out
}

/** Each sentence-ish unit (a bullet or a table row) that names a skill as a read target. */
function readTargets(): { slug: string; unit: string; file: string }[] {
  const slugs = new Set(readdirSync(SKILLS).filter((d) => existsSync(`${SKILLS}/${d}/SKILL.md`)))
  const found: { slug: string; unit: string; file: string }[] = []
  for (const file of SOURCES.filter((f) => existsSync(f))) {
    const units = readFileSync(file, 'utf8').split(/\n(?=\s*(?:- |\| |\d+\. ))/)
    for (const unit of units) {
      for (const [, slug] of unit.matchAll(/\/(?:app|opt\/data)\/skills\/([a-z0-9-]+)\//g)) {
        if (slugs.has(slug)) found.push({ slug, unit, file })
      }
      // The drafting lane names its slugs in backticks beside a <slug> path.
      if (/\/app\/skills\/<slug>\//.test(unit)) {
        for (const [, slug] of unit.matchAll(/`([a-z][a-z0-9-]+-drafter)`/g)) {
          if (slugs.has(slug)) found.push({ slug, unit, file })
        }
      }
    }
  }
  return found
}

describe('skills the router and self-initiation read, on the firm seat', () => {
  it('finds read targets at all', () => {
    // A check that cannot fail measured nothing.
    expect(readTargets().length).toBeGreaterThan(5)
  })

  it('are each enabled, or named beside the could-not-load instruction', () => {
    const enabled = enabledSkills()
    const bad = readTargets()
      .filter(({ slug, unit }) => !enabled.has(slug) && !COULD_NOT_LOAD.test(unit))
      .map(({ slug, file }) => `${slug} (${file})`)
    expect([...new Set(bad)], `omitted skills read with no refusal instruction`).toEqual([])
  })

  it('enables the demand and queued drafting lanes and none of the in-turn drafting skills', () => {
    const enabled = enabledSkills()
    expect(enabled.has('demand-letter-drafter')).toBe(true)
    // 2026-10-07: the firm's own written ask activated the queued drafting lane.
    expect(enabled.has('document-drafter')).toBe(true)
    for (const slug of [
      'discovery-response-drafter',
      'follow-up-discovery-drafter',
      'mediation-brief-drafter',
    ]) {
      expect(enabled.has(slug), `${slug} must stay off this seat`).toBe(false)
    }
  })
})
