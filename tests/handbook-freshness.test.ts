import { describe, it, expect } from 'vitest'
import { reviewStatus, readingOrder, neighbours } from '../src/lib/handbook/freshness'

const d = (iso: string) => new Date(iso)

describe('reviewStatus', () => {
  it('a page with no review date is unreviewed, not old', () => {
    expect(reviewStatus(undefined, 30, d('2026-10-21T12:00:00Z'))).toEqual({ kind: 'unreviewed' })
  })

  it('counts whole UTC days and reports days remaining', () => {
    expect(reviewStatus(d('2026-10-21'), 30, d('2026-10-31T09:00:00Z'))).toEqual({
      kind: 'current',
      daysSince: 10,
      dueInDays: 20,
    })
  })

  it('the last day of the window is still current; the next is overdue', () => {
    expect(reviewStatus(d('2026-10-01'), 30, d('2026-10-31T23:59:00Z')).kind).toBe('current')
    expect(reviewStatus(d('2026-10-01'), 30, d('2026-11-01T00:00:00Z'))).toEqual({
      kind: 'overdue',
      daysSince: 31,
      overdueByDays: 1,
    })
  })

  it('a Phoenix evening (already the next UTC day) counts the UTC day, consistently', () => {
    // 2026-10-21 20:00 MST is 2026-10-22 03:00 UTC.
    expect(reviewStatus(d('2026-10-21'), 30, d('2026-10-22T03:00:00Z'))).toMatchObject({
      daysSince: 1,
    })
  })

  it('an invalid date is treated as unreviewed', () => {
    expect(reviewStatus(new Date('not a date'), 30, d('2026-10-21')).kind).toBe('unreviewed')
  })
})

describe('reading order', () => {
  const entries = [
    { id: 'arch', title: 'Architecture', section: 'engineering' as const, order: 1 },
    { id: 'week', title: 'Week', section: 'running' as const, order: 2 },
    { id: 'start', title: 'Start', section: 'running' as const, order: 1 },
    { id: 'overview', title: 'Overview', section: 'business' as const, order: 1 },
  ]

  it('orders by section display order, then order', () => {
    expect(readingOrder(entries).map((e) => e.id)).toEqual(['start', 'week', 'overview', 'arch'])
  })

  it('neighbours crosses section boundaries and stops at the ends', () => {
    expect(neighbours(entries, 'week')).toMatchObject({
      prev: { id: 'start' },
      next: { id: 'overview' },
    })
    expect(neighbours(entries, 'start').prev).toBeUndefined()
    expect(neighbours(entries, 'arch').next).toBeUndefined()
    expect(neighbours(entries, 'missing')).toEqual({})
  })
})
