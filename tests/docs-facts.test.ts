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
import { existsSync, readFileSync, readdirSync } from 'node:fs'
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
 * The 2026-09-04 review found repository-map.md stating 68 migrations with 107
 * on disk; the 2026-10-06 review found it stating 107 with 115. A count that
 * moves every week is not restated in prose at all: the directory is the count.
 */
describe('repository map states no migration count', () => {
  const row = read('docs/handbook/repository-map.md')
    .split('\n')
    .find((line) => line.startsWith('| `migrations/` |'))

  it('finds the migrations row (sanity: the check can fail)', () => {
    expect(row).toBeDefined()
  })

  it('the row names no number of migrations', () => {
    expect(row).not.toMatch(/\b\d+ (forward )?migrations\b/)
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

/**
 * The overlay plug-in count the platform page states is the number of plug-in
 * directories on disk. It said 29 on 2026-11-09; the enrollment plug-in (ADR
 * 0106) made it 30 the next day and nothing noticed. A plug-in is a directory
 * under operator/hermes-smd-overlay/plugins/ that carries its own plugin.yaml.
 */
describe('operator platform page states the real overlay plug-in count', () => {
  const dir = 'operator/hermes-smd-overlay/plugins'
  const onDisk = readdirSync(resolve(dir), { withFileTypes: true }).filter(
    (d) => d.isDirectory() && existsSync(resolve(dir, d.name, 'plugin.yaml'))
  ).length
  const stated = read('docs/handbook/operator-platform.md').match(
    /\((\d+) under `operator\/hermes-smd-overlay\/plugins\/`/
  )

  it('finds the sentence and a plausible tree (sanity: the check can fail)', () => {
    expect(
      stated,
      'operator-platform.md must keep the "(N under `.../plugins/`" shape'
    ).not.toBeNull()
    expect(onDisk).toBeGreaterThan(10)
  })

  it('the stated count equals the plug-in directories on disk', () => {
    expect(Number(stated![1])).toBe(onDisk)
  })
})

/**
 * The guide's product library states the counts a representative quotes to a
 * prospect (kinds of action, connection tools, industry packs, kinds of queued
 * job, seat configurations). PR #4020 rewrote the library by hand from the
 * tree; nothing kept those numbers true afterwards. Each one is derived here
 * from the file it counts, so a new connector tool, pack, lane or action class
 * turns the library red instead of leaving a rep quoting last month's number.
 */
describe('product library: the counts it states equal the tree', () => {
  const LIB = 'operator/skills/product-guide/references'
  const lib = (name: string): string => read(`${LIB}/${name}`)
  const numbersRow = (label: string): string => {
    const row = lib('built-versus-planned.md')
      .split('\n')
      .find((line) => line.startsWith(`| ${label} `))
    expect(row, `built-versus-planned.md must keep its "${label}" row`).toBeDefined()
    return row!.split('|')[2].trim()
  }

  // ---- kinds of action: the ActionClass enum in the overlay's trust gate
  const enumBlock =
    read('operator/hermes-smd-overlay/shared/action_classes.py').match(
      /^class ActionClass\(str, enum\.Enum\):\n([\s\S]*?)\n(?=\S)/m
    )?.[1] ?? ''
  const actionClasses = [...enumBlock.matchAll(/^ {4}[A-Z_]+ = \(?\s*"([a-z_]+)"/gm)].map(
    (m) => m[1]
  )

  // ---- connection tools: [connector.tool_classes] in each connector manifest
  const CONNECTORS = 'operator/connectors'
  const manifests = readdirSync(resolve(CONNECTORS)).filter((d) =>
    existsSync(resolve(CONNECTORS, d, 'manifest.toml'))
  )
  const toolClasses = (dir: string): Record<string, number> => {
    const section =
      read(`${CONNECTORS}/${dir}/manifest.toml`).match(
        /^\[connector\.tool_classes\]\n([\s\S]*?)(?=^\[|(?![\s\S]))/m
      )?.[1] ?? ''
    const counts: Record<string, number> = {}
    for (const m of section.matchAll(/^[a-z0-9_]+\s*=\s*"([a-z_]+)"/gm))
      counts[m[1]] = (counts[m[1]] ?? 0) + 1
    return counts
  }
  const total = (c: Record<string, number>): number => Object.values(c).reduce((a, b) => a + b, 0)
  const allTools: Record<string, number> = {}
  for (const d of manifests)
    for (const [k, v] of Object.entries(toolClasses(d))) allTools[k] = (allTools[k] ?? 0) + v

  // ---- job lanes: operator/contracts/job-lanes.yaml, the one declaration
  const lanesBody = read('operator/contracts/job-lanes.yaml').split(/^lanes:\n/m)[1] ?? ''
  const laneBlocks = [
    ...(lanesBody.split(/^\S/m)[0] ?? '').matchAll(/^ {2}([a-z]+):\n((?: {4}.*\n)*)/gm),
  ]
  const queued = laneBlocks.filter((m) => /^ {4}queued: true$/m.test(m[2]))
  const watched = queued.filter((m) => /^ {4}audit_prefix: \S+/m.test(m[2]))

  // ---- packs and seats
  const packs = readdirSync(resolve('operator/verticals'), { withFileTypes: true }).filter(
    (d) => d.isDirectory() && !d.name.startsWith('_')
  ).length
  const seats = readdirSync(resolve('operator/customers'), { withFileTypes: true }).filter(
    (d) =>
      d.isDirectory() &&
      !d.name.startsWith('_') &&
      !d.name.includes('.decommissioned') &&
      existsSync(resolve('operator/customers', d.name, 'customer.yaml'))
  ).length

  it('derives a plausible tree (sanity: the checks can fail)', () => {
    expect(actionClasses).toContain('read')
    expect(actionClasses).toContain('refused')
    expect(actionClasses.length).toBeGreaterThan(5)
    expect(manifests).toContain('smokeball')
    expect(total(allTools)).toBeGreaterThan(50)
    expect(queued.length).toBeGreaterThan(3)
    expect(packs).toBeGreaterThan(5)
    expect(seats).toBeGreaterThan(2)
    expect(existsSync(resolve('operator/verticals/_template'))).toBe(true)
    expect(existsSync(resolve('operator/customers/_template'))).toBe(true)
  })

  it('kinds of action: permission-model.md and the numbers table equal the enum', () => {
    const limited = actionClasses.length - 2 // read and refused take no limit
    const pm = lib('permission-model.md').match(
      /There are (\d+) kinds: (\d+) that can be given a limit, plus `read` and `refused`\./
    )
    expect(pm, 'permission-model.md must keep the "There are N kinds" sentence').not.toBeNull()
    expect([Number(pm![1]), Number(pm![2])]).toEqual([actionClasses.length, limited])
    expect(numbersRow('Kinds of action')).toBe(
      `${actionClasses.length} (${limited} that take a limit, plus read and refused)`
    )
    // The Action classes table lists exactly the enum, each kind once.
    const section = lib('permission-model.md').match(/^## Action classes\n([\s\S]*?)^## /m)?.[1]
    expect(section, 'permission-model.md must keep its "## Action classes" section').toBeDefined()
    const tabled = [...section!.matchAll(/^\| `([a-z_]+)` +\|/gm)].map((m) => m[1])
    expect([...tabled].sort()).toEqual([...actionClasses].sort())
  })

  it('SMD-written connections and their tools: the numbers table equals the manifests', () => {
    const real = manifests.filter((d) => !d.startsWith('_')).length
    expect(numbersRow('SMD-written connections')).toBe(
      `${manifests.length} (${real} real plus a test reference)`
    )
    const t = allTools
    const sends = Object.entries(t)
      .filter(([k]) => k.startsWith('external_send'))
      .reduce((a, [, v]) => a + v, 0)
    expect(numbersRow('Tools in those connections')).toBe(
      `${total(t)} (${t.read ?? 0} read, ${t.internal_write ?? 0} internal write, ${sends} send, ` +
        `${t.commitment ?? 0} commitment, ${t.destructive ?? 0} destructive)`
    )
    const together = lib('connections-and-channels.md').match(
      /The SMD-written connections together hold (\d+) tools\./
    )
    expect(
      together,
      'connections-and-channels.md must keep the "together hold N tools" sentence'
    ).not.toBeNull()
    expect(Number(together![1])).toBe(total(t))
  })

  it('each connection row in connections-and-channels.md equals its manifest', () => {
    const ROWS: Array<[rowStart: string, connector: string]> = [
      ["| Email in the client's own Microsoft 365 tenant", 'msgraph-mail'],
      ['| Industry system of record (practice management)', 'smokeball'],
      ['| Web analytics', 'ga4'],
      ["| The client's own website", 'wordpress'],
      ['| Public video feed', 'youtube-public'],
    ]
    const KIND: Record<string, string> = {
      read: 'read',
      'internal write': 'internal_write',
      send: 'external_send',
      commitment: 'commitment',
      destructive: 'destructive',
    }
    const lines = lib('connections-and-channels.md').split('\n')
    for (const [rowStart, connector] of ROWS) {
      const row = lines.find((l) => l.startsWith(rowStart))
      expect(row, `connections-and-channels.md must keep the row "${rowStart}"`).toBeDefined()
      const cell = row!.split('|')[3].trim()
      const stated: Record<string, number> = {}
      const readOnly = cell.match(/^(\d+) read tools$/)
      if (readOnly) stated.read = Number(readOnly[1])
      else {
        const m = cell.match(/^(\d+) tools: (.+)$/)
        expect(m, `${connector}: tools cell "${cell}" has an unknown shape`).not.toBeNull()
        for (const part of m![2].split(', ')) {
          const p = part.match(/^(\d+) (.+)$/)
          const kind = p ? KIND[p[2]] : undefined
          expect(kind, `${connector}: unknown kind in "${part}"`).toBeDefined()
          stated[kind!] = Number(p![1])
        }
        expect(Number(m![1]), `${connector}: stated total`).toBe(total(toolClasses(connector)))
      }
      expect(stated, `${connector}: per-kind counts`).toEqual(toolClasses(connector))
    }
  })

  it('industry packs: what-it-is.md and the numbers table equal operator/verticals/', () => {
    expect(numbersRow('Industry packs')).toBe(`${packs}, plus a template`)
    const m = lib('what-it-is.md').match(/There are (\d+) industry packs:/)
    expect(m, 'what-it-is.md must keep the "There are N industry packs" sentence').not.toBeNull()
    expect(Number(m![1])).toBe(packs)
  })

  it('kinds of queued job: jobs.md and the numbers table equal job-lanes.yaml', () => {
    expect(numbersRow('Kinds of queued job')).toBe(
      `${queued.length} (${watched.length} covered by the hourly work-health check), ` +
        'plus a note SMD approves into a digest'
    )
    const m = lib('jobs.md').match(/There are (\d+) kinds of queued job\./)
    expect(m, 'jobs.md must keep the "There are N kinds of queued job" sentence').not.toBeNull()
    expect(Number(m![1])).toBe(queued.length)
    // The one unqueued lane is the note the digest carries.
    const unqueued = laneBlocks.map((b) => b[1]).filter((n) => !queued.some((q) => q[1] === n))
    expect(unqueued).toEqual(['note'])
  })

  it('seat configurations: the numbers table equals operator/customers/', () => {
    expect(
      numbersRow('Seat configurations').startsWith(`${seats} authored, plus a template.`)
    ).toBe(true)
  })
})
