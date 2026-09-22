/**
 * No Operator skill models the em dash in its model-facing text.
 *
 * The overlay's outbound fabrication filter refuses any draft carrying an em
 * dash, whatever skill composed it, and the model copies the character from
 * its own instructions. On 2026-09-21 pilot-smokeball's daily digest had two
 * memos refused on the em dash alone; on 2026-09-22 the deadline escalator had
 * all five of its memos refused once for the same reason. Each refusal also
 * feeds the seat's refusal-cascade brake. Forty skills even said "No em dashes
 * anywhere" while their templates used them; the rest said nothing and still
 * shipped drafts the filter refuses. Model-facing markdown of every skill
 * (outside its tests/ fixtures) must be em-dash free.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = 'operator/skills'
const EM_DASH = String.fromCharCode(0x2014)

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

describe('no skill carries an em dash in its model-facing text', () => {
  it('finds the skills (guards against a vacuous pass)', () => {
    expect(skills.length).toBeGreaterThan(40)
  })

  for (const skill of skills) {
    it(skill, () => {
      const offenders = markdownFiles(join(ROOT, skill), join(ROOT, skill)).filter((f) =>
        readFileSync(f, 'utf8').includes(EM_DASH)
      )
      expect(offenders).toEqual([])
    })
  }
})
