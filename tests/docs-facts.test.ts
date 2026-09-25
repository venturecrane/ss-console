/**
 * One number per fact: the prose that restates a count or a version is pinned
 * to the thing it counts.
 *
 * The 2026-09-10 code review found three different decision counts for one
 * decision stack (CLAUDE.md said 37 through #55, docs/adr/decision-stack.md
 * said 38 through #56, docs/adr/index.md said 34 through #51), an ADR range in
 * CLAUDE.md that stopped at 0061 with 0087 on disk, and a README naming
 * TypeScript 5 against a ^6 dependency. None of these can be caught by reading
 * one file. This test derives each number from its source (the decision
 * headings, the ADR directory, package.json) and fails when any restatement
 * disagrees, so the next drift is a red check rather than a review finding.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'

const read = (p: string): string => readFileSync(resolve(p), 'utf8')

/** The decision stack, parsed from its own headings. */
function decisionFacts(): { active: number; max: number; superseded: number[] } {
  const src = read('docs/adr/decision-stack.md')
  const lines = src.split('\n')
  const numbers: number[] = []
  const superseded: number[] = []
  lines.forEach((line, i) => {
    const m =
      line.match(/^## Decision #(\d+)\b/) ??
      line.match(/^## Venture-Wide Positioning Standard \(#(\d+)\)/)
    if (!m) return
    const n = Number(m[1])
    numbers.push(n)
    // A superseded decision either says so in its heading or opens with a
    // SUPERSEDED callout within the first few lines of its section.
    const window = lines.slice(i, i + 6).join('\n')
    if (/SUPERSEDED/.test(line) || /^> \*\*SUPERSEDED/m.test(window)) superseded.push(n)
  })
  return {
    active: numbers.length - superseded.length,
    max: Math.max(...numbers),
    superseded: superseded.sort((a, b) => a - b),
  }
}

const facts = decisionFacts()
const supersededText = facts.superseded.map((n) => `#${n}`).join(', ')

describe('decision stack: the count in prose equals the count of headings', () => {
  it('parses a plausible stack (sanity: the headings are there)', () => {
    expect(facts.active).toBeGreaterThan(20)
    expect(facts.superseded.length).toBeGreaterThan(0)
  })

  it('docs/adr/decision-stack.md "Decisions locked" row', () => {
    const row = read('docs/adr/decision-stack.md').match(
      /\|\s*\*\*Decisions locked\*\*\s*\|\s*(\d+) active decisions[^|]*numbered through #(\d+); (\d+) superseded \(([^)]*)\)/
    )
    expect(row, 'the Decisions locked row must keep its shape').not.toBeNull()
    expect([Number(row![1]), Number(row![2]), Number(row![3]), row![4]]).toEqual([
      facts.active,
      facts.max,
      facts.superseded.length,
      supersededText,
    ])
  })

  it('CLAUDE.md Key Reference', () => {
    const m = read('CLAUDE.md').match(
      /\((\d+) active decisions across 6 layers, numbered through #(\d+) \((\d+) superseded: ([^)]*)\)/
    )
    expect(m, 'CLAUDE.md must keep the Decision Stack sentence shape').not.toBeNull()
    expect([Number(m![1]), Number(m![2]), Number(m![3]), m![4]]).toEqual([
      facts.active,
      facts.max,
      facts.superseded.length,
      supersededText,
    ])
  })

  it('docs/adr/index.md pointer', () => {
    const m = read('docs/adr/index.md').match(
      /\((\d+) active across [^;]*; numbered through #(\d+); (\d+) superseded: ([^;)]*)/
    )
    expect(m, 'docs/adr/index.md must keep the decision-corpus sentence shape').not.toBeNull()
    expect([Number(m![1]), Number(m![2]), Number(m![3]), m![4]]).toEqual([
      facts.active,
      facts.max,
      facts.superseded.length,
      supersededText,
    ])
  })
})

describe('ADR range in CLAUDE.md is not a stale upper bound', () => {
  it('names no specific highest ADR (the directory is the range)', () => {
    const claude = read('CLAUDE.md')
    expect(claude).not.toMatch(/through `docs\/adr\/\d{4}-\*\.md`/)
    expect(claude).toContain('`docs/adr/0004-*.md` onward')
  })

  it('the directory really does run past 0061, the bound the old sentence carried', () => {
    const highest = readdirSync(resolve('docs/adr'))
      .map((f) => f.match(/^(\d{4})-/)?.[1])
      .filter((n): n is string => Boolean(n))
      .sort()
      .at(-1)
    expect(Number(highest)).toBeGreaterThan(61)
  })
})

/**
 * Every major the README's Stack list states is the major package.json
 * declares. TypeScript was the first pinned (2026-09-10 review: "TypeScript 5"
 * against ^6); the 2026-09-25 review then found "Vitest 4" against ^5.0.0,
 * because only TypeScript was pinned. So every version on the list is pinned
 * here, and a Stack line naming a version this table does not know fails too.
 */
describe('README names the majors the repo declares', () => {
  const pkg = JSON.parse(read('package.json')) as {
    dependencies?: Record<string, string>
    devDependencies?: Record<string, string>
  }
  const declared = { ...pkg.dependencies, ...pkg.devDependencies }
  const stack = read('README.md').match(/## Stack\n([\s\S]*?)\n## /)?.[1] ?? ''
  const STATED: Array<[label: string, pkgName: string]> = [
    ['Astro', 'astro'],
    ['React', 'react'],
    ['TypeScript', 'typescript'],
    ['Vitest', 'vitest'],
    ['ESLint', 'eslint'],
    ['Tailwind', 'tailwindcss'],
  ]

  it('finds the Stack section (sanity: the check can fail)', () => {
    expect(stack.length).toBeGreaterThan(0)
  })

  for (const [label, pkgName] of STATED) {
    it(`README.md "${label} N" equals the major of ${pkgName}`, () => {
      const major = (declared[pkgName] ?? '').match(/(\d+)\./)?.[1]
      expect(major, `package.json must declare ${pkgName}`).toBeDefined()
      const readme = stack.match(new RegExp(`\\b${label} (\\d+)\\b`))
      expect(readme, `README.md Stack must name a ${label} major`).not.toBeNull()
      expect(readme![1]).toBe(major)
    })
  }

  it('every "<Name> <major>" the Stack section states is in the table above', () => {
    const known = new Set(STATED.map(([label]) => label))
    const stated = [...stack.matchAll(/\b([A-Z][A-Za-z]+) (\d+)\b/g)].map((m) => m[1])
    expect(stated.filter((name) => !known.has(name))).toEqual([])
  })
})
