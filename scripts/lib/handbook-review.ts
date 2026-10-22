/**
 * Which handbook pages need a person to read them again, and why.
 *
 * The first drift radar compared a page's last commit with its sources' last
 * commits. Any edit to a page, even a frontmatter regroup, reset its clock, so
 * the 2026-10-21 Wave 1 and 2 edits silenced it: 27 flagged pages became 3
 * without anyone checking anything. This keys on `last_reviewed` instead, which
 * only a deliberate check against the sources sets (docs/handbook/README.md):
 *
 *   source changed   a cited source's last commit is after the page's review date
 *   overdue          the page's review window has run out
 *   unreviewed       the page has never been checked against its sources
 *
 * The first two are findings; the third is the backlog, reported so it shrinks
 * rather than hides. Pure over its inputs so tests can drive it without git.
 */

import { reviewStatus } from '../../src/lib/handbook/freshness'

export interface ReviewPage {
  file: string
  section: string
  lastReviewed?: string
  reviewEvery?: number
  /** Same-repo paths the page cites in `sources`. */
  sources: string[]
}

export interface ReviewFindings {
  sourceChanged: { file: string; reviewed: string; changes: { path: string; on: string }[] }[]
  overdue: { file: string; reviewed: string; overdueByDays: number }[]
  unreviewed: { file: string; section: string }[]
}

/**
 * @param lastCommitDay the UTC calendar day (YYYY-MM-DD) of a path's last commit, or null
 */
export function reviewFindings(
  pages: ReviewPage[],
  lastCommitDay: (path: string) => string | null,
  today: Date
): ReviewFindings {
  const out: ReviewFindings = { sourceChanged: [], overdue: [], unreviewed: [] }
  for (const p of [...pages].sort((a, b) => a.file.localeCompare(b.file))) {
    if (!p.lastReviewed) {
      out.unreviewed.push({ file: p.file, section: p.section })
      continue
    }
    // A source committed on the review day counts as seen: reviews are dated by
    // day, and the reviewer had that day's tree in front of them.
    const changes = p.sources
      .map((path) => ({ path, on: lastCommitDay(path) }))
      .filter((c): c is { path: string; on: string } => !!c.on && c.on > p.lastReviewed!)
    if (changes.length) out.sourceChanged.push({ file: p.file, reviewed: p.lastReviewed, changes })
    const status = reviewStatus(new Date(`${p.lastReviewed}T00:00:00Z`), p.reviewEvery, today)
    if (status.kind === 'overdue')
      out.overdue.push({
        file: p.file,
        reviewed: p.lastReviewed,
        overdueByDays: status.overdueByDays,
      })
  }
  return out
}

export function hasFindings(f: ReviewFindings): boolean {
  return f.sourceChanged.length > 0 || f.overdue.length > 0
}

export function renderReviewReport(f: ReviewFindings): string {
  const lines: string[] = []
  if (f.sourceChanged.length) {
    lines.push('## A source changed after the page was reviewed', '')
    for (const p of f.sourceChanged)
      lines.push(
        `- \`${p.file}\` (reviewed ${p.reviewed}): ${p.changes.map((c) => `\`${c.path}\` ${c.on}`).join(', ')}`
      )
    lines.push('')
  }
  if (f.overdue.length) {
    lines.push('## Review overdue', '')
    for (const p of f.overdue)
      lines.push(`- \`${p.file}\` (reviewed ${p.reviewed}), overdue by ${p.overdueByDays} days`)
    lines.push('')
  }
  lines.push(`## Never reviewed: ${f.unreviewed.length} page(s)`, '')
  if (f.unreviewed.length) {
    const bySection = new Map<string, string[]>()
    for (const p of f.unreviewed)
      bySection.set(p.section, [...(bySection.get(p.section) ?? []), p.file])
    for (const [section, files] of bySection)
      lines.push(`- ${section}: ${files.map((x) => `\`${x}\``).join(', ')}`)
    lines.push('')
  }
  lines.push(
    'To clear a page: read it against every cited source, fix what disagrees, and set `last_reviewed` to that day (docs/handbook/README.md).'
  )
  return lines.join('\n')
}
