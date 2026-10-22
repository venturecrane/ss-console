/**
 * Structural-integrity gate for the ADR corpus (docs/adr/).
 *
 * The ADR numbering has collided three times (0044 -> 0061, 0068 -> 0069, and
 * 0069 -> 0073 on 2026-07-13). Each collision was a mechanical hazard: two files
 * sharing a number, or a file orphaned from the index. This test makes both
 * conditions impossible to merge:
 *
 *   1. no two files in docs/adr/ share the same NNNN numeric prefix, and
 *   2. every docs/adr/NNNN-*.md file has an entry (its filename) in
 *      docs/adr/index.md, so a renumber/add can never silently orphan a record,
 *   3. every path an ADR lists under `enforced_by:` exists (see below).
 *
 * Kept dependency-light on purpose (fs + vitest only).
 *
 * @see docs/adr/index.md - the numbering note records the collision history
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'fs'
import { resolve, join } from 'path'

const ADR_DIR = resolve('docs/adr')
const INDEX = join(ADR_DIR, 'index.md')

const adrFiles = readdirSync(ADR_DIR).filter((f) => /^\d{4}-.*\.md$/.test(f))

describe('ADR corpus integrity', () => {
  it('has ADR files to check', () => {
    expect(adrFiles.length).toBeGreaterThan(0)
  })

  it('no two ADR files share the same NNNN numeric prefix', () => {
    const byNumber = new Map<string, string[]>()
    for (const file of adrFiles) {
      const num = file.slice(0, 4)
      const list = byNumber.get(num) ?? []
      list.push(file)
      byNumber.set(num, list)
    }
    const collisions = [...byNumber.entries()].filter(([, files]) => files.length > 1)
    expect(
      collisions,
      `Duplicate ADR number prefixes: ${collisions
        .map(([num, files]) => `${num} -> ${files.join(', ')}`)
        .join('; ')}. Renumber one to the next free slot and record it in index.md.`
    ).toEqual([])
  })

  it('every ADR file is listed in index.md', () => {
    const index = readFileSync(INDEX, 'utf8')
    const missing = adrFiles.filter((file) => !index.includes(file))
    expect(
      missing,
      `ADR files missing an entry in docs/adr/index.md: ${missing.join(', ')}. ` +
        `Add a "- [<file>](./<file>) - <one-line summary>" line so the record is not orphaned.`
    ).toEqual([])
  })
})

/**
 * `enforced_by:` (2026-10-22). ADR 0012 sat "accepted" for five months while
 * describing paths, triggers and a CLI that never existed, because nothing tied
 * the record to the code that carries it out. An ADR that declares
 * `enforced_by:` in its frontmatter names the tests, workflows and scripts that
 * enforce it, and every named path must exist in this tree, so renaming or
 * deleting an enforcer fails the build instead of silently orphaning the
 * record. The field is opt-in: an ADR without it is not checked.
 */
function enforcedBy(markdown: string): string[] | null {
  const fm = markdown.match(/^---\n([\s\S]*?)\n---\n/)
  if (!fm) return null
  const lines = fm[1].split('\n')
  const start = lines.findIndex((l) => /^enforced_by:\s*$/.test(l))
  if (start === -1) return null
  const paths: string[] = []
  for (const line of lines.slice(start + 1)) {
    const item = line.match(/^\s+-\s+(\S.*?)\s*$/)
    if (!item) break
    paths.push(item[1].replace(/^['"]|['"]$/g, ''))
  }
  return paths
}

describe('ADR enforced_by paths', () => {
  const declaring = adrFiles
    .map((file) => ({ file, paths: enforcedBy(readFileSync(join(ADR_DIR, file), 'utf8')) }))
    .filter((a): a is { file: string; paths: string[] } => a.paths !== null)

  it('the parser reads a block list and ignores files without the key', () => {
    expect(
      enforcedBy('---\ntitle: x\nenforced_by:\n  - a.ts\n  - "b.yml"\nnext: 1\n---\n')
    ).toEqual(['a.ts', 'b.yml'])
    expect(enforcedBy('---\ntitle: x\n---\n')).toBeNull()
    expect(enforcedBy('# no frontmatter\n')).toBeNull()
  })

  it('at least the records that introduced the field declare it', () => {
    const names = declaring.map((a) => a.file)
    expect(names).toContain('0044-r2-authoritative-live-reconfig.md')
    expect(names).toContain('0094-customer-configuration-as-built.md')
  })

  it('every declaring ADR names at least one enforcer, and every named path exists', () => {
    const problems: string[] = []
    for (const { file, paths } of declaring) {
      if (paths.length === 0) problems.push(`${file}: enforced_by is empty`)
      for (const p of paths) {
        if (!existsSync(resolve(p))) problems.push(`${file}: ${p} does not exist`)
      }
    }
    expect(
      problems,
      `ADR enforced_by entries must name repo-relative paths that exist: ${problems.join('; ')}`
    ).toEqual([])
  })
})
