/**
 * Render docs/handbook/adr-index.md from docs/adr/index.md and the ADR files.
 *
 * The handbook page says it lists every ADR. Hand-maintained, it stopped at
 * 0065 while the directory ran to 0089 (2026-09-25 code review, Documentation
 * 1). docs/adr/index.md is already held complete by tests/adr-integrity.test.ts,
 * so this page is derived from it: one row per numbered file, the title from
 * the ADR's own frontmatter or H1, the one-line summary from its index entry,
 * and a row for every number the sequence skips. tests/handbook-integrity.test.ts
 * fails when the committed page differs from what this renders.
 *
 * Output obeys the handbook's own rules: no em dashes (house style, pinned by
 * the same test), and links that resolve from /admin/playbook (the index's
 * relative `./NNNN-x.md` links would not), so they are rewritten to GitHub.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const BLOB = 'https://github.com/venturecrane/ss-console/blob/main/docs/adr/'

export interface AdrRow {
  number: string
  file: string | null
  title: string
  summary: string
}

/** The handbook forbids em dashes; the ADR corpus uses them freely. */
function houseStyle(text: string): string {
  return text.replace(/\s+\u2014\s+/g, ' - ').replace(/\u2014/g, '-')
}

/** Relative ADR links in the index resolve from docs/adr/, not from the playbook. */
function absoluteLinks(text: string): string {
  return text.replace(/\]\(\.\/([^)]+)\)/g, (_m, target: string) => `](${BLOB}${target})`)
}

/** A table cell cannot carry a raw pipe. */
function cell(text: string): string {
  return text.replace(/\|/g, '\\|')
}

/** Title from frontmatter `title:` or the first H1, without its "ADR NNNN" prefix. */
export function adrTitle(markdown: string): string {
  let body = markdown
  const fm = markdown.match(/^---\n([\s\S]*?)\n---\n?/)
  if (fm) {
    const t = fm[1].match(/^title:\s*(.+)$/m)
    if (t) return stripPrefix(t[1].trim().replace(/^['"]|['"]$/g, ''))
    body = markdown.slice(fm[0].length)
  }
  const h1 = body.match(/^# (.+)$/m)
  return h1 ? stripPrefix(h1[1].trim()) : ''
}

function stripPrefix(title: string): string {
  return title.replace(/^ADR\s+\d{4}\s*[:\u2014-]?\s*/, '')
}

/** "- [0001-x.md](./0001-x.md) - summary" lines from the index's Files list. */
export function indexSummaries(indexMarkdown: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const line of indexMarkdown.split('\n')) {
    const m = line.match(/^- \[(\d{4}-[^\]]+\.md)\]\(\.\/\1\) - (.+)$/)
    if (m) out.set(m[1], m[2].trim())
  }
  return out
}

export function buildAdrRows(adrDir: string): AdrRow[] {
  const files = readdirSync(adrDir)
    .filter((f) => /^\d{4}-.*\.md$/.test(f))
    .sort()
  const summaries = indexSummaries(readFileSync(join(adrDir, 'index.md'), 'utf8'))
  const byNumber = new Map(files.map((f) => [f.slice(0, 4), f]))
  const highest = Math.max(...files.map((f) => Number(f.slice(0, 4))))
  const rows: AdrRow[] = []
  for (let n = 1; n <= highest; n++) {
    const number = String(n).padStart(4, '0')
    const file = byNumber.get(number) ?? null
    if (!file) {
      rows.push({ number, file: null, title: '(no file)', summary: 'Number never assigned.' })
      continue
    }
    rows.push({
      number,
      file,
      title: adrTitle(readFileSync(join(adrDir, file), 'utf8')),
      summary: summaries.get(file) ?? '',
    })
  }
  return rows
}

export function renderAdrIndex(rows: AdrRow[]): string {
  const gaps = rows.filter((r) => !r.file).map((r) => r.number)
  const present = rows.filter((r) => r.file)
  const lines = [
    '---',
    'title: ADR Index',
    'section: reference',
    'order: 1',
    'summary: Every Architecture Decision Record, numerically, with its title, its one-line summary, and a link to the source',
    'sources:',
    '  - label: docs/adr/ directory',
    '    href: https://github.com/venturecrane/ss-console/tree/main/docs/adr',
    '  - label: ADR index (source)',
    '    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/index.md',
    '---',
    '',
    '## What this is',
    '',
    'The map into the "why" canon: every numbered ADR in `docs/adr/`, in order, with a link to the file. ADRs capture narrower architectural choices, mostly about the Operator platform; the broader go-to-market decision corpus lives separately on [The Decision Stack](/admin/playbook/decision-stack). Always cite the ADR number when referencing a decision elsewhere.',
    '',
    "This page is generated, not written. The title is the ADR's own; the summary, including every amendment and supersession note, is its entry in `docs/adr/index.md`, which `tests/adr-integrity.test.ts` keeps complete. To change a summary, edit the index and regenerate with `npm run handbook:adr-index`; `tests/handbook-integrity.test.ts` blocks merge while this page and the corpus disagree.",
    '',
    '## Numbering',
    '',
    `${present.length} records, 0001 through ${rows[rows.length - 1].number}. ` +
      (gaps.length
        ? `The sequence skips ${gaps.join(', ')}: never assigned, no file lost. `
        : 'The sequence has no gaps. ') +
      'Numbers have collided and been renumbered (the static-secret contract moved from 0044 to 0061; the independent-oversight record moved from 0068 to 0069 to 0074); the history is in the numbering note at the top of [docs/adr/index.md](https://github.com/venturecrane/ss-console/blob/main/docs/adr/index.md), and `tests/adr-integrity.test.ts` now refuses a duplicate prefix.',
    '',
    '## The records',
    '',
    '| # | Title | Summary | Source |',
    '|---|---|---|---|',
    ...rows.map((r) =>
      r.file
        ? `| ${r.number} | ${cell(houseStyle(r.title))} | ${cell(houseStyle(absoluteLinks(r.summary)))} | [${r.file}](${BLOB}${r.file}) |`
        : `| ${r.number} | ${r.title} | ${r.summary} | - |`
    ),
    '',
    '## Related',
    '',
    '- The go-to-market decision corpus these ADRs sit alongside: [The Decision Stack](/admin/playbook/decision-stack)',
    '- Vocabulary used throughout: [Glossary](/admin/playbook/glossary)',
    '- Where every doc lives: [Docs Map](/admin/playbook/docs-map)',
  ]
  return lines.join('\n')
}
