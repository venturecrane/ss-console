/**
 * No Operator skill's model-facing text names a pilot client.
 *
 * On 2026-09-28 pilot-smokeball's date-prep reply turn staged a witness list in
 * the Okafor matter carrying "NOTE ON NATARAJAN: Scott Durgan indicated on
 * 2026-09-28 that Natarajan may be dropped." Nobody said that. The phrase came
 * from the skills' own examples ("drop Natarajan", "Is Priya Natarajan still
 * testifying?"), written against the pilot's seed matter, which the turn read
 * and took for a fact about the matter it was working on. An example that names
 * a real matter's people is indistinguishable, to the model, from that matter's
 * record. Examples use invented placeholders (Doe, Acme) instead.
 *
 * The names come from the pilot seed itself (matter keys and the witness list),
 * so a new seed matter is covered without editing this file.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = 'operator/skills'
const SEED = readFileSync('operator/customers/pilot-smokeball/seed/seed_data.py', 'utf8')

const matterBlock = SEED.slice(SEED.indexOf('MATTERS'), SEED.indexOf('TASKS'))
const surnames = [...matterBlock.matchAll(/^ {4}"[a-z]+-([a-z]+)": \{/gm)].map((m) => m[1])
const NAMES = [...new Set([...surnames, 'natarajan', 'grand valley'])]

function markdownFiles(dir: string, skillRoot: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) {
      if (relative(skillRoot, p).split('/')[0] === 'tests') continue
      out.push(...markdownFiles(p, skillRoot))
    } else if (name.endsWith('.md')) {
      out.push(p)
    }
  }
  return out
}

const skills = readdirSync(ROOT).filter((s) => {
  try {
    return statSync(join(ROOT, s, 'SKILL.md')).isFile()
  } catch {
    return false
  }
})

describe('no skill names a pilot client in its model-facing text', () => {
  it('reads the pilot names from the seed (guards against a vacuous pass)', () => {
    expect(surnames).toEqual(expect.arrayContaining(['alvarez', 'okafor', 'whitfield']))
    expect(skills.length).toBeGreaterThan(40)
  })

  for (const skill of skills) {
    it(skill, () => {
      const offenders: string[] = []
      for (const f of markdownFiles(join(ROOT, skill), join(ROOT, skill))) {
        const text = readFileSync(f, 'utf8')
        for (const name of NAMES) {
          if (new RegExp(`\\b${name}\\b`, 'i').test(text)) offenders.push(`${f}: ${name}`)
        }
      }
      expect(offenders).toEqual([])
    })
  }
})
