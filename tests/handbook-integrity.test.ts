/**
 * Structural-integrity gate for the Venture Handbook (docs/handbook/).
 *
 * The handbook is the E-Myth operations manual rendered at /admin/playbook. It is
 * only useful if it stays structurally sound and its citations stay honest. This
 * test is the hard gate behind the maintenance contract in docs/handbook/README.md:
 * it runs in `npm run verify` and CI, and blocks merge on
 *
 *   1. malformed frontmatter (missing title, bad section, non-numeric order),
 *   2. a /admin/playbook/<slug> cross-link that points at no page (dead link),
 *   3. a cited same-repo source file that does not exist (moved/renamed/deleted
 *      source the page was never updated to match - the accuracy-forcing check),
 *   4. two pages colliding on the same (section, order) sidebar slot,
 *   5. an em dash in any page (house style; these files are not covered by the
 *      .astro forbidden-strings scan).
 *
 * The complementary git-mtime staleness signal (a source changed after the page)
 * is advisory and lives in scripts/handbook-drift.mjs (`npm run handbook:drift`),
 * not here, because a source edit does not always require a doc edit.
 *
 * @see docs/handbook/README.md - the maintenance contract
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync } from 'fs'
import { resolve, join } from 'path'
import { parse as parseYaml } from 'yaml'
import { buildAdrRows, indexSummaries, renderAdrIndex } from '../scripts/lib/handbook-adr-index'
import {
  buildConnectorInventory,
  committedBlock,
  parseManifest,
  renderConnectorInventory,
} from '../scripts/lib/handbook-connector-inventory'

const HANDBOOK = resolve('docs/handbook')
const REPO_ROOT = resolve('.')
const SECTIONS = ['business', 'product', 'system', 'operations', 'reference']

// Bare (non-URL) hrefs are treated as repo paths only when rooted here. Anything
// else with no scheme (e.g. a crane_doc('global', ...) pseudo-ref) is skipped.
const REPO_PATH_ROOTS = [
  'src/',
  'docs/',
  'migrations/',
  'workers/',
  'operator/',
  'scripts/',
  'public/',
  'tests/',
  '.github/',
]

interface Page {
  file: string
  slug: string
  frontmatter: Record<string, unknown>
  body: string
  raw: string
}

function splitFrontmatter(raw: string): { fm: Record<string, unknown>; body: string } {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!m) return { fm: {}, body: raw }
  const fm = (parseYaml(m[1]) as Record<string, unknown>) ?? {}
  return { fm, body: m[2] }
}

const pages: Page[] = readdirSync(HANDBOOK)
  .filter((f) => f.endsWith('.md') && f !== 'README.md')
  .map((file) => {
    const raw = readFileSync(join(HANDBOOK, file), 'utf8')
    const { fm, body } = splitFrontmatter(raw)
    return { file, slug: file.replace(/\.md$/, ''), frontmatter: fm, body, raw }
  })

const slugSet = new Set(pages.map((p) => p.slug))

/** Resolve a source href to a same-repo path, or null if it is external/non-path. */
function repoPathOf(href: string): string | null {
  const blob = href.match(
    /^https:\/\/github\.com\/venturecrane\/ss-console\/(?:blob|tree)\/[^/]+\/(.+)$/
  )
  if (blob) return blob[1].replace(/[#?].*$/, '')
  if (!/^[a-z]+:\/\//.test(href) && !href.includes('(')) {
    const clean = href.replace(/[#?].*$/, '')
    if (REPO_PATH_ROOTS.some((r) => clean.startsWith(r))) return clean
  }
  return null
}

describe('handbook integrity', () => {
  it('finds the handbook pages', () => {
    expect(pages.length).toBeGreaterThan(20)
  })

  it('every page has valid frontmatter', () => {
    const errors: string[] = []
    for (const p of pages) {
      const { title, section, order } = p.frontmatter as {
        title?: unknown
        section?: unknown
        order?: unknown
      }
      if (typeof title !== 'string' || title.trim() === '')
        errors.push(`${p.file}: missing or empty 'title'`)
      if (typeof section !== 'string' || !SECTIONS.includes(section))
        errors.push(
          `${p.file}: 'section' must be one of ${SECTIONS.join(', ')} (got ${String(section)})`
        )
      if (order !== undefined && typeof order !== 'number')
        errors.push(`${p.file}: 'order' must be a number when present`)
    }
    expect(errors, errors.join('\n')).toEqual([])
  })

  it('every /admin/playbook/<slug> cross-link resolves to a real page', () => {
    const errors: string[] = []
    for (const p of pages) {
      const refs = p.body.match(/\/admin\/playbook\/([a-z0-9-]+)/g) ?? []
      for (const ref of refs) {
        const slug = ref.replace('/admin/playbook/', '')
        if (!slugSet.has(slug)) errors.push(`${p.file}: dead cross-link -> /admin/playbook/${slug}`)
      }
    }
    expect(errors, errors.join('\n')).toEqual([])
  })

  it('every cited same-repo source file exists', () => {
    const errors: string[] = []
    for (const p of pages) {
      const sources = (p.frontmatter.sources as { label?: string; href?: string }[]) ?? []
      for (const s of sources) {
        if (!s?.href) continue
        const repoPath = repoPathOf(s.href)
        if (repoPath && !existsSync(resolve(REPO_ROOT, repoPath)))
          errors.push(`${p.file}: cited source does not exist -> ${repoPath}`)
      }
    }
    expect(errors, errors.join('\n')).toEqual([])
  })

  it('no two pages share a (section, order) sidebar slot', () => {
    const seen = new Map<string, string>()
    const errors: string[] = []
    for (const p of pages) {
      const { section, order } = p.frontmatter as { section?: string; order?: number }
      if (order === undefined) continue
      const key = `${section}#${order}`
      if (seen.has(key))
        errors.push(`${section} order ${order}: ${seen.get(key)} and ${p.file} collide`)
      else seen.set(key, p.file)
    }
    expect(errors, errors.join('\n')).toEqual([])
  })

  it('no page contains an em dash (house style)', () => {
    const errors: string[] = []
    for (const p of pages) {
      if (p.raw.includes('—')) errors.push(`${p.file}: contains an em dash (use a spaced hyphen)`)
    }
    expect(errors, errors.join('\n')).toEqual([])
  })
})

/**
 * Pages whose claim is completeness are generated, and pinned to the tree
 * (2026-09-25 code review, Documentation 1 and 2). adr-index.md said "every
 * ADR" and stopped at 0065 with 0089 on disk; connectors-channels.md went
 * eleven weeks without the tools the window added to live connectors. Both are
 * now rendered from their sources, and these cases fail when the committed text
 * and the tree disagree. Regenerate with `npm run handbook:adr-index -- --write`
 * and `npm run handbook:connectors -- --write`.
 */
describe('generated handbook content matches the tree', () => {
  const ADR_DIR = resolve('docs/adr')
  const adrPage = readFileSync(join(HANDBOOK, 'adr-index.md'), 'utf8')
  const connectorsPage = readFileSync(join(HANDBOOK, 'connectors-channels.md'), 'utf8')

  it('adr-index.md is exactly what the ADR corpus renders', () => {
    expect(adrPage).toBe(renderAdrIndex(buildAdrRows(ADR_DIR)) + '\n')
  })

  it('adr-index.md names every numbered ADR file (the claim its summary makes)', () => {
    const files = readdirSync(ADR_DIR).filter((f) => /^\d{4}-.*\.md$/.test(f))
    expect(files.length).toBeGreaterThan(80)
    expect(files.filter((f) => !adrPage.includes(f))).toEqual([])
  })

  it('the ADR renderer strips em dashes and relative links (the check can fail)', () => {
    const summaries = indexSummaries(
      '- [0001-x.md](./0001-x.md) - A \u2014 see [0002](./0002-y.md)\n'
    )
    expect(summaries.get('0001-x.md')).toContain('\u2014')
    const page = renderAdrIndex([
      {
        number: '0001',
        file: '0001-x.md',
        title: 'T \u2014 U',
        summary: summaries.get('0001-x.md') ?? '',
      },
    ])
    expect(page).not.toContain('\u2014')
    expect(page).not.toContain('](./')
  })

  it('the connector inventory block is exactly what operator/connectors renders', () => {
    const rendered = renderConnectorInventory(
      buildConnectorInventory(resolve('operator/connectors'))
    )
    expect(
      committedBlock(connectorsPage),
      'connectors-channels.md lost its generated-block markers'
    ).toBe(rendered)
  })

  it('the inventory sees every connector manifest and every tool in it', () => {
    const entries = buildConnectorInventory(resolve('operator/connectors'))
    const manifests = readdirSync(resolve('operator/connectors')).filter((d) =>
      existsSync(resolve('operator/connectors', d, 'manifest.toml'))
    )
    expect(entries.map((e) => e.dir).sort()).toEqual(manifests.sort())
    for (const e of entries) {
      expect(e.name, `${e.dir}: no [connector] name parsed`).not.toBe('')
      expect(e.tools.length, `${e.dir}: no tool_classes parsed`).toBeGreaterThan(0)
    }
  })

  it('a new tool in a manifest changes the block (the check can fail)', () => {
    const base = parseManifest(
      '[connector]\nname = "x"\n[connector.tool_classes]\nread_a = "read"\n'
    )
    const grown = parseManifest(
      '[connector]\nname = "x"\n[connector.tool_classes]\nread_a = "read"\nsend_b = "external_send"\n'
    )
    const render = (m: typeof base) =>
      renderConnectorInventory([{ dir: 'x', toolModules: [], ...m }])
    expect(render(grown)).not.toBe(render(base))
  })
})
