/**
 * One capability story: the marketing site, the guide's product library and
 * the venture's sales docs never disclaim a capability by build state, and
 * never say a person at SMD reads client files.
 *
 * The Captain's ruling (2026-11-13): the Operator is SMD's configurable
 * product; a capability is true if SMD can configure or change the product to
 * do it. Build state is rep knowledge with three labels, "running for a client
 * today", "built, set up per client", "configured per engagement". PR #4020
 * (the library) and PR #4027 (enablement, collateral, handbook) rewrote every
 * surface to that ruling; before them the three surfaces told three stories
 * (the library said "show only what is in production", the explainer had a
 * "Planned, not built" column, the figures said "SMD staff must look at" client
 * work). This test is what keeps them from drifting apart again: a planted
 * disclaimer anywhere in these trees is a red check naming file:line.
 *
 * The phrase list and scanner are tests/_support/capability-framing.ts. Fix a
 * failure by rewriting the TEXT in capability terms, never by narrowing the
 * list.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import {
  BANNED_BUILD_STATE_PHRASES,
  SMD_PEOPLE_READ_FILES,
  formatFindings,
  scanCapabilityFraming,
} from './_support/capability-framing'

const ROOT = resolve(import.meta.dirname, '..')

/** The rep- and prospect-facing trees. */
const SURFACES = [
  'operator/skills/product-guide',
  'operator/skills/pitch-practice',
  'operator/skills/rep-training',
  'operator/skills/rep-desk',
  'operator/roles/guide',
  'docs/enablement',
  'docs/collateral',
  'src/lib/operator-packs',
  'src/pages',
]

/**
 * Not prospect-facing: the admin console, the client portal, the API routes,
 * and the dev-only previews (not served in production).
 */
const EXCLUDED = /^src\/pages\/(admin|portal|api|dev|design-preview)\//

const TEXT_FILE = /\.(md|ya?ml|astro|tsx?|html|py|txt)$/

function surfaceFiles(): string[] {
  const out: string[] = []
  for (const dir of SURFACES) {
    const entries = readdirSync(join(ROOT, dir), { recursive: true, withFileTypes: true })
    for (const e of entries) {
      if (!e.isFile() || !TEXT_FILE.test(e.name)) continue
      const rel = join(e.parentPath, e.name).slice(ROOT.length + 1)
      if (EXCLUDED.test(rel) || rel.includes('__pycache__')) continue
      out.push(rel)
    }
  }
  return out.sort()
}

describe('capability framing: one story across the site, the guide and the docs', () => {
  const files = surfaceFiles()

  it('reads every surface (sanity: an empty walk cannot pass)', () => {
    for (const dir of SURFACES) {
      expect(
        files.some((f) => f.startsWith(dir + '/')),
        `no text files found under ${dir}`
      ).toBe(true)
    }
    expect(files).toContain('operator/skills/product-guide/references/built-versus-planned.md')
    expect(files).toContain('docs/enablement/figures-src/gen3.body.html')
    expect(files).toContain('src/pages/operator.astro')
    expect(files.some((f) => f.startsWith('src/pages/admin/'))).toBe(false)
  })

  it('no surface disclaims a capability by build state or says SMD people read client files', () => {
    const findings = files.flatMap((f) =>
      scanCapabilityFraming(f, readFileSync(join(ROOT, f), 'utf8'))
    )
    expect(formatFindings(findings)).toEqual([])
  })
})

describe('capability framing: the guard can fail', () => {
  for (const p of BANNED_BUILD_STATE_PHRASES) {
    it(`catches a planted "${p.name}"`, () => {
      const hits = scanCapabilityFraming('planted.md', `intro\n${p.example}\n`)
      expect(hits.map((h) => [h.line, h.phrase])).toContainEqual([2, p.name])
    })
  }

  it('catches a planted "SMD people read client files" sentence', () => {
    const hits = scanCapabilityFraming('planted.md', SMD_PEOPLE_READ_FILES.example)
    expect(hits.map((h) => h.phrase)).toEqual([SMD_PEOPLE_READ_FILES.name])
  })

  it('a disclaimer planted into a real library page is reported at its line', () => {
    const page = 'operator/skills/product-guide/references/built-versus-planned.md'
    const lines = readFileSync(join(ROOT, page), 'utf8').split('\n')
    const at = Math.floor(lines.length / 2)
    lines.splice(at, 0, 'The texting channel is specified, not built.')
    const hits = scanCapabilityFraming(page, lines.join('\n'))
    expect(formatFindings(hits).some((l) => l.startsWith(`${page}:${at + 1} `))).toBe(true)
  })

  it('a rehearsal must_not_contain list may name a false claim; any other YAML line may not', () => {
    const yaml = [
      '- id: q1',
      '  must_contain:',
      '    - texting is specified, not built',
      '  must_not_contain:',
      '    - a statement that SMD staff read client documents',
      '    - texting is not yet built',
      '  answer: texting is not yet built',
    ].join('\n')
    const hits = scanCapabilityFraming('questions.yaml', yaml)
    expect(hits.map((h) => h.line)).toEqual([3, 3, 7])
  })

  it('the three labels, and the ruling stated as a denial, pass', () => {
    const allowed = [
      'Running for a client today.',
      'Built, set up per client.',
      'Configured per engagement.',
      'No person at SMD reads client files; only AI agents handle them.',
      'SMD staff never see client files.',
      'The Operator is not built for an industry. It is built for your business.',
    ].join('\n')
    expect(formatFindings(scanCapabilityFraming('allowed.md', allowed))).toEqual([])
  })
})
