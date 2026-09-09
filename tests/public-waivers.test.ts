/**
 * The `@public` waiver must be falsifiable.
 *
 * `npm run deadcode` (knip in production mode, see knip.jsonc) fails on any
 * export with no production importer. The one escape hatch is a `@public`
 * JSDoc tag at the definition. Until 2026-09-09 the tag's justification was
 * unverified prose, and the code review that day found three of fifteen tags
 * naming consumers that did not exist (a cron worker never created, an
 * overlay that contains no TypeScript, an invoice page with no local variant)
 * and six more citing tests that merely grep the export's own source line.
 * Every one of those passed a green gate. This test is what makes the tag
 * cost something.
 *
 * Rule: a `@public` block must name at least one repo-relative path; every
 * named path must exist; and at least one named path must reference the
 * exported symbol. When that path is a test file, the test must IMPORT the
 * symbol, not match its source text: `expect(source()).toContain('export
 * function x')` is the assertion the tag sits above, restated, and proves
 * nothing about a consumer. A drift guard that imports a registry and walks
 * it is a real consumer; a grep is not.
 *
 * Two shapes are legitimate and both are covered by the rule as written:
 *   - a contract consumed only by a test by design (drift guards, SQL-CASE
 *     twins, closed vocabularies, merge-gate checks), named by the test that
 *     imports it;
 *   - an export whose consumer is a file knip reports as unused (a component
 *     or adapter layer nobody has retired yet), named by that file. The
 *     `files` warning keeps that cluster visible; retiring it is a product
 *     decision, and this test does not pretend otherwise.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync, readdirSync, statSync } from 'fs'
import { join, resolve } from 'path'

const ROOT = resolve(__dirname, '..')
const SCAN_ROOTS = ['src', 'workers', 'scripts']

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist' || name.startsWith('.')) continue
    const full = join(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (/\.(ts|tsx|mjs)$/.test(name) && !/\.test\.(ts|tsx|mjs)$/.test(name)) out.push(full)
  }
  return out
}

interface Waiver {
  file: string
  line: number
  symbol: string
  text: string
  paths: string[]
}

const PATH_RE =
  /(?:src|tests|scripts|workers|operator|public|migrations|\.github|\.claude)\/[\w./@[\]-]+/g
const EXPORT_RE =
  /^export (?:declare )?(?:async )?(?:function\*?|const|let|class|interface|type|enum|abstract class) (\w+)/

function collectWaivers(): Waiver[] {
  const waivers: Waiver[] = []
  for (const root of SCAN_ROOTS) {
    const dir = join(ROOT, root)
    if (!existsSync(dir)) continue
    for (const file of walk(dir)) {
      const lines = readFileSync(file, 'utf-8').split('\n')
      for (let i = 0; i < lines.length; i++) {
        if (!lines[i].includes('@public')) continue
        // Walk to the end of the comment block, then to the export it decorates.
        let j = i
        while (j < lines.length && !lines[j].includes('*/')) j++
        let k = j + 1
        while (k < lines.length && lines[k].trim() === '') k++
        const decl = lines[k] ?? ''
        const m = EXPORT_RE.exec(decl)
        // Gather the whole block text (from the opening /** upward).
        let s = i
        while (s > 0 && !lines[s].includes('/**')) s--
        const text = lines.slice(s, j + 1).join('\n')
        waivers.push({
          file: file.slice(ROOT.length + 1),
          line: i + 1,
          symbol: m ? m[1] : '',
          text,
          paths: [...new Set(text.match(PATH_RE) ?? [])].map((p) => p.replace(/[.,:;)]+$/, '')),
        })
      }
    }
  }
  return waivers
}

const IMPORT_RE = (symbol: string) =>
  new RegExp(`import\\s+(?:type\\s+)?\\{[^}]*\\b${symbol}\\b[^}]*\\}\\s+from`, 's')

const waivers = collectWaivers()

describe('@public waivers name a consumer that exists and really consumes', () => {
  it('finds the waivers at all (an empty scan would pass every rule vacuously)', () => {
    expect(waivers.length).toBeGreaterThanOrEqual(10)
  })

  it('every waiver decorates an export declaration', () => {
    const orphans = waivers.filter((w) => !w.symbol)
    expect(
      orphans.map((w) => `${w.file}:${w.line}`),
      'a @public tag must sit in the JSDoc directly above an `export` declaration'
    ).toEqual([])
  })

  for (const w of waivers) {
    const label = `${w.file}:${w.line} ${w.symbol}`

    it(`${label} names at least one repo path`, () => {
      expect(
        w.paths,
        `@public on ${w.symbol} must name the file that consumes it (a test that imports it, or a production file)`
      ).not.toEqual([])
    })

    it(`${label}: every named path exists`, () => {
      const missing = w.paths.filter((p) => !existsSync(join(ROOT, p)))
      expect(missing, `@public on ${w.symbol} names a path that does not exist`).toEqual([])
    })

    it(`${label}: a named path imports or references the symbol`, () => {
      // Directories (a block may mention `operator/customers/` in passing) are
      // not consumers; only files can import or reference a symbol.
      const consumers = w.paths.filter(
        (p) => existsSync(join(ROOT, p)) && statSync(join(ROOT, p)).isFile() && p !== w.file
      )
      const real = consumers.filter((p) => {
        const body = readFileSync(join(ROOT, p), 'utf-8')
        const isTest = /(^|\/)tests\//.test(p) || /\.test\.(ts|tsx|mjs)$/.test(p)
        if (isTest) return IMPORT_RE(w.symbol).test(body)
        return new RegExp(`\\b${w.symbol}\\b`).test(body)
      })
      expect(
        real,
        `@public on ${w.symbol} names no path that consumes it. A test must import the symbol; ` +
          `a source-text assertion (readFileSync + toContain) is the tag restated, not a consumer.`
      ).not.toEqual([])
    })
  }
})
