/**
 * Which handbook pages need a person to read them again, and why.
 *
 * The first drift radar compared a page's last commit with its sources' last
 * commits. Any edit to a page, even a frontmatter regroup, reset its clock, so
 * the 2026-10-21 Wave 1 and 2 edits silenced it: 27 flagged pages became 3
 * without anyone checking anything. This keys on `last_reviewed` instead, which
 * only a deliberate check against the sources sets (docs/handbook/README.md):
 *
 *   source changed   a cited source has a commit after the one that set the review
 *   overdue          the page's review window has run out
 *   unreviewed       the page has never been checked against its sources
 *
 * The first two are findings; the third is the backlog, reported so it shrinks
 * rather than hides. Pure over its inputs so tests can drive it without git.
 *
 * "After the review" is a commit order, not a date. The first version compared
 * calendar days, and on 2026-10-21 every page reviewed that evening (Phoenix)
 * landed in commits dated 10-22 UTC alongside the source edits the same review
 * made, so all 36 pages reported a changed source the moment they merged. The
 * commit that set `last_reviewed` is the review; only commits after it count.
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
 * @param changedSinceReview for a page and one of its sources, the UTC day
 *   (YYYY-MM-DD) of the source's latest commit after the commit that set the
 *   page's `last_reviewed`, or null when there is none
 */
export function reviewFindings(
  pages: ReviewPage[],
  changedSinceReview: (page: string, source: string) => string | null,
  today: Date
): ReviewFindings {
  const out: ReviewFindings = { sourceChanged: [], overdue: [], unreviewed: [] }
  for (const p of [...pages].sort((a, b) => a.file.localeCompare(b.file))) {
    if (!p.lastReviewed) {
      out.unreviewed.push({ file: p.file, section: p.section })
      continue
    }
    const changes = p.sources
      .map((path) => ({ path, on: changedSinceReview(p.file, path) }))
      .filter((c): c is { path: string; on: string } => !!c.on)
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
