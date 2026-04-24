/**
 * Pure-helper tests for findDraftableMeeting / findDraftableMeetings.
 * No DB — the helper takes already-loaded meetings and quotes and returns
 * a filtered+sorted view. Covers the rule both surfaces (entity detail
 * page and entity list row) need to agree on.
 */

import { describe, it, expect } from 'vitest'

import { findDraftableMeeting, findDraftableMeetings } from '../src/lib/entities/draftable-meeting'

type M = {
  id: string
  status: string
  completed_at: string | null
  created_at: string
}
type Q = { meeting_id: string | null; assessment_id: string }

const completed = (id: string, completedAt: string | null = null, createdAt = '2026-01-01'): M => ({
  id,
  status: 'completed',
  completed_at: completedAt,
  created_at: createdAt,
})

describe('findDraftableMeetings', () => {
  it('returns empty when there are no meetings', () => {
    expect(findDraftableMeetings([], [])).toEqual([])
  })

  it('excludes meetings whose id matches a quote.meeting_id', () => {
    const m1 = completed('m1', '2026-04-01')
    const m2 = completed('m2', '2026-04-02')
    const result = findDraftableMeetings([m1, m2], [{ meeting_id: 'm1', assessment_id: 'a1' }])
    expect(result.map((m) => m.id)).toEqual(['m2'])
  })

  it('excludes meetings whose id matches a quote.assessment_id (legacy)', () => {
    // A quote whose meeting_id is null but assessment_id matches the
    // meeting id — the legacy linkage we still honor.
    const m1 = completed('m1', '2026-04-01')
    const result = findDraftableMeetings([m1], [{ meeting_id: null, assessment_id: 'm1' }])
    expect(result).toEqual([])
  })

  it('only considers meetings with status === "completed"', () => {
    const m1 = completed('m1', '2026-04-01')
    const m2: M = {
      id: 'm2',
      status: 'scheduled',
      completed_at: null,
      created_at: '2026-04-02',
    }
    const result = findDraftableMeetings([m1, m2], [])
    expect(result.map((m) => m.id)).toEqual(['m1'])
  })

  it('sorts most-recently-completed first', () => {
    const earlier = completed('m1', '2026-03-01')
    const later = completed('m2', '2026-04-01')
    const result = findDraftableMeetings([earlier, later], [])
    expect(result.map((m) => m.id)).toEqual(['m2', 'm1'])
  })

  it('falls back to created_at when completed_at is missing', () => {
    const a = completed('a', null, '2026-03-01')
    const b = completed('b', null, '2026-04-01')
    const result = findDraftableMeetings([a, b], [])
    expect(result.map((m) => m.id)).toEqual(['b', 'a'])
  })
})

describe('findDraftableMeeting', () => {
  it('returns null when the list is empty', () => {
    expect(findDraftableMeeting([], [])).toBeNull()
  })

  it('returns the most recent draftable meeting', () => {
    const earlier = completed('m1', '2026-03-01')
    const later = completed('m2', '2026-04-01')
    expect(findDraftableMeeting([earlier, later], [])?.id).toBe('m2')
  })

  it('returns null when every completed meeting has a quote', () => {
    const m1 = completed('m1', '2026-04-01')
    const result = findDraftableMeeting([m1], [{ meeting_id: 'm1', assessment_id: 'a1' }])
    expect(result).toBeNull()
  })
})
