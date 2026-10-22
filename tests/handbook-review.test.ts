import { describe, it, expect } from 'vitest'
import { hasFindings, renderReviewReport, reviewFindings } from '../scripts/lib/handbook-review'

const today = new Date('2026-11-01T12:00:00Z')
// What git reports: for each page, the sources with a commit after the one that
// set its last_reviewed, and the day of the latest such commit.
const after: Record<string, Record<string, string>> = {
  'x.md': { 'src/b.ts': '2026-10-25' },
}
const changedSinceReview = (page: string, src: string) => after[page]?.[src] ?? null

describe('handbook review findings', () => {
  it('a source committed after the review commit is a finding; one before or in it is not', () => {
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
      changedSinceReview,
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
      changedSinceReview,
      today
    )
    expect(f.overdue).toEqual([{ file: 'z.md', reviewed: '2026-09-01', overdueByDays: 31 }])
  })

  it('a never-reviewed page is backlog, not a finding, and is reported', () => {
    const f = reviewFindings(
      [{ file: 'n.md', section: 'engineering', sources: ['src/b.ts'] }],
      changedSinceReview,
      today
    )
    expect(f.unreviewed).toEqual([{ file: 'n.md', section: 'engineering' }])
    expect(hasFindings(f)).toBe(false)
    expect(renderReviewReport(f)).toContain('Never reviewed: 1 page(s)')
  })

  it('a review committed the day after it is dated still clears the sources it saw', () => {
    // The 2026-10-21 regression: reviewed in Phoenix on the 21st, committed on
    // the 22nd UTC together with the source edits the review made. Comparing
    // days flagged it; ordering by commit does not, because git reports nothing
    // after the review commit.
    const f = reviewFindings(
      [
        {
          file: 'evening.md',
          section: 'running',
          lastReviewed: '2026-10-21',
          reviewEvery: 30,
          sources: ['src/b.ts'],
        },
      ],
      changedSinceReview,
      new Date('2026-10-22T02:00:00Z')
    )
    expect(f.sourceChanged).toEqual([])
    expect(hasFindings(f)).toBe(false)
  })
})
