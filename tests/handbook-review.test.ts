import { describe, it, expect } from 'vitest'
import { hasFindings, renderReviewReport, reviewFindings } from '../scripts/lib/handbook-review'

const today = new Date('2026-11-01T12:00:00Z')
const commits: Record<string, string> = {
  'src/a.ts': '2026-10-20',
  'src/b.ts': '2026-10-25',
}
const lastCommitDay = (p: string) => commits[p] ?? null

describe('handbook review findings', () => {
  it('a source committed after the review date is a finding; on the same day it is not', () => {
    const f = reviewFindings(
      [
        {
          file: 'x.md',
          section: 'running',
          lastReviewed: '2026-10-21',
          reviewEvery: 90,
          sources: ['src/a.ts', 'src/b.ts'],
        },
        {
          file: 'y.md',
          section: 'running',
          lastReviewed: '2026-10-25',
          reviewEvery: 90,
          sources: ['src/b.ts'],
        },
      ],
      lastCommitDay,
      today
    )
    expect(f.sourceChanged).toEqual([
      { file: 'x.md', reviewed: '2026-10-21', changes: [{ path: 'src/b.ts', on: '2026-10-25' }] },
    ])
    expect(hasFindings(f)).toBe(true)
  })

  it('a page past its window is overdue', () => {
    const f = reviewFindings(
      [
        {
          file: 'z.md',
          section: 'running',
          lastReviewed: '2026-09-01',
          reviewEvery: 30,
          sources: [],
        },
      ],
      lastCommitDay,
      today
    )
    expect(f.overdue).toEqual([{ file: 'z.md', reviewed: '2026-09-01', overdueByDays: 31 }])
  })

  it('a never-reviewed page is backlog, not a finding, and is reported', () => {
    const f = reviewFindings(
      [{ file: 'n.md', section: 'engineering', sources: ['src/b.ts'] }],
      lastCommitDay,
      today
    )
    expect(f.unreviewed).toEqual([{ file: 'n.md', section: 'engineering' }])
    expect(hasFindings(f)).toBe(false)
    expect(renderReviewReport(f)).toContain('Never reviewed: 1 page(s)')
  })

  it('an edit to the page itself does not clear anything (only last_reviewed does)', () => {
    // The old radar compared commit times, so touching a page reset its clock.
    // Here the page's own commits are not an input at all.
    const f = reviewFindings(
      [
        {
          file: 'x.md',
          section: 'running',
          lastReviewed: '2026-10-21',
          reviewEvery: 90,
          sources: ['src/b.ts'],
        },
      ],
      (p) => (p === 'docs/handbook/x.md' ? '2026-10-30' : lastCommitDay(p)),
      today
    )
    expect(f.sourceChanged).toHaveLength(1)
  })
})
