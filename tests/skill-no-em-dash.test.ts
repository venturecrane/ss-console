/**
 * A skill that forbids em dashes must not model them.
 *
 * Forty Operator skills tell the model "No em dashes anywhere", and the
 * overlay's outbound fabrication filter refuses any draft carrying one. Yet
 * their own templates and examples, which the model copies, were full of them.
 * On 2026-09-21 pilot-smokeball's daily digest had two memos refused on the em
 * dash alone; on 2026-09-22 the deadline escalator had all five of its memos
 * refused once for the same reason. Each refusal also feeds the seat's
 * refusal-cascade brake. Model-facing markdown of every such skill (outside
 * its tests/ fixtures) must be em-dash free.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = 'operator/skills'
const RULE = /no em[- ]dashes/i

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
    return RULE.test(readFileSync(join(ROOT, s, 'SKILL.md'), 'utf8'))
  } catch {
    return false
  }
})

describe('skills that forbid em dashes carry none in their model-facing text', () => {
  it('finds the skills that state the rule (guards against a vacuous pass)', () => {
    expect(skills.length).toBeGreaterThan(10)
  })

  for (const skill of skills) {
    it(skill, () => {
      const offenders = markdownFiles(join(ROOT, skill), join(ROOT, skill)).filter((f) =>
        readFileSync(f, 'utf8').includes('—')
      )
      expect(offenders).toEqual([])
    })
  }
})
