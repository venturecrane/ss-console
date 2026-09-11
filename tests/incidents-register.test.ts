/**
 * Integrity gate for the post-incident register (`docs/runbooks/operator/incidents/`).
 *
 * Why this exists (ss#2547): the register was created under #2391 to close the
 * loop from incident to permanent immune-system change, and it had no test. By
 * 2026-08-22 its index listed six of the seven notes on disk. The missing one,
 * `2026-08-20-gateway-wedge-no-restart.md`, was the most recent incident in the
 * venture, and the only place a reader would look for it said the register held
 * six notes ending on 2026-08-13. An index that is quietly wrong is worse than
 * no index: it answers the question "has this happened before?" with a
 * confident no.
 *
 * The checks, in the `doctrine-integrity.test.ts` idiom:
 *
 *   1. every `20*.md` on disk appears in the index, and every indexed note
 *      exists on disk (both directions, because either gap reads as coverage)
 *   2. filenames match the convention the README states
 *   3. every note carries every `## ` heading in `_TEMPLATE.md` (an omitted
 *      section is a question nobody was made to answer)
 *   4. every index row's Class is in the fixed set (an open vocabulary cannot
 *      group recurrences, which is the whole point of the column)
 *   5. no em dashes (venture style law)
 *
 * @see docs/runbooks/operator/incidents/README.md
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'fs'
import { join, resolve } from 'path'

const INCIDENTS_DIR = resolve('docs/runbooks/operator/incidents')
const README_PATH = join(INCIDENTS_DIR, 'README.md')
const TEMPLATE_PATH = join(INCIDENTS_DIR, '_TEMPLATE.md')

/**
 * The closed class vocabulary. Closed on purpose: the column exists so a reader
 * asking "has this shape happened before?" can sort by it, and a free-text
 * column would answer that question differently every time it was filled in.
 */
const CLASSES = ['gate-regression', 'built-not-wired', 'gone-not-gone', 'identity', 'other']

const FILENAME_RE = /^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$/

function noteFiles(): string[] {
  return readdirSync(INCIDENTS_DIR)
    .filter((f) => f.startsWith('20') && f.endsWith('.md'))
    .sort()
}

/** One row of the index table: the note it names and the class it assigns. */
interface IndexRow {
  file: string
  klass: string
}

/**
 * Parse the index table out of the README.
 *
 * Deliberately tolerant about column ORDER and about the table's other cells:
 * the two facts this test needs are the filename and the class, and a parser
 * that also depended on the prose column would fail for reasons that are not
 * defects. The filename is recognized by its backticks so a note mentioned in
 * prose elsewhere in the README cannot be mistaken for an index entry.
 */
function indexRows(readme: string): IndexRow[] {
  const rows: IndexRow[] = []
  for (const line of readme.split('\n')) {
    if (!line.startsWith('|')) continue
    const cells = line
      .split('|')
      .slice(1, -1)
      .map((c) => c.trim())
    const fileCell = cells.find((c) => /^`\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md`$/.test(c))
    if (!fileCell) continue
    const file = fileCell.replaceAll('`', '')
    const klass = cells.map((c) => c.replaceAll('`', '')).find((c) => CLASSES.includes(c)) ?? ''
    rows.push({ file, klass })
  }
  return rows
}

function templateHeadings(): string[] {
  return readFileSync(TEMPLATE_PATH, 'utf8')
    .split('\n')
    .filter((l) => l.startsWith('## '))
    .map((l) => l.trim())
}

describe('the incident register index', () => {
  const readme = readFileSync(README_PATH, 'utf8')
  const rows = indexRows(readme)

  it('indexes every note on disk', () => {
    const indexed = new Set(rows.map((r) => r.file))
    const missing = noteFiles().filter((f) => !indexed.has(f))
    expect(missing, `notes on disk but absent from the index: ${missing.join(', ')}`).toEqual([])
  })

  it('names no note that is not on disk', () => {
    const onDisk = new Set(noteFiles())
    const phantom = rows.map((r) => r.file).filter((f) => !onDisk.has(f))
    expect(phantom, `indexed notes with no file: ${phantom.join(', ')}`).toEqual([])
  })

  it('assigns every indexed note a class from the fixed set', () => {
    const unclassed = rows.filter((r) => r.klass === '').map((r) => r.file)
    expect(
      unclassed,
      `indexed notes whose Class is missing or outside {${CLASSES.join(', ')}}: ${unclassed.join(', ')}`
    ).toEqual([])
  })

  it('defines the class vocabulary it uses', () => {
    // A column of values the README never defines is a column nobody can fill
    // in the same way twice.
    for (const klass of CLASSES) {
      expect(readme, `README does not define the class ${klass}`).toContain(`\`${klass}\``)
    }
  })
})

describe('the notes themselves', () => {
  const headings = templateHeadings()

  it('the template still defines the section list this test enforces', () => {
    // Guard on the guard: an empty heading list would make the next test pass
    // vacuously against notes with no sections at all.
    expect(headings.length).toBeGreaterThan(4)
  })

  for (const file of noteFiles()) {
    describe(file, () => {
      const body = readFileSync(join(INCIDENTS_DIR, file), 'utf8')

      it('has a filename matching the stated convention', () => {
        expect(FILENAME_RE.test(file)).toBe(true)
      })

      it('carries every section the template defines', () => {
        const missing = headings.filter((h) => !body.includes(h))
        expect(missing, `${file} is missing: ${missing.join(', ')}`).toEqual([])
      })

      it('contains no em dashes', () => {
        const offenders = body
          .split('\n')
          .map((line, i) => ({ line, n: i + 1 }))
          .filter(({ line }) => line.includes('—'))
          .map(({ n }) => `${file}:${n}`)
        expect(offenders).toEqual([])
      })
    })
  }
})
