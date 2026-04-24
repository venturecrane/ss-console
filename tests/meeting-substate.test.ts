/**
 * Pure-helper tests for getMeetingSubstate / findNextScheduledMeeting.
 * No DB — both helpers operate on already-loaded meetings + quotes
 * arrays. Covers the rule both surfaces (entity meetings list and detail
 * page) need to agree on for sub-state classification.
 */

import { describe, it, expect } from 'vitest'

import { findNextScheduledMeeting, getMeetingSubstate } from '../src/lib/entities/meeting-substate'

type M = {
  id: string
  status: string
  scheduled_at: string | null
  completed_at: string | null
  created_at: string
}
type Q = { meeting_id: string | null; assessment_id: string }

const NOW = new Date('2026-04-24T12:00:00Z')

const meeting = (overrides: Partial<M> & { id: string }): M => ({
  status: 'scheduled',
  scheduled_at: null,
  completed_at: null,
  created_at: '2026-04-01T00:00:00Z',
  ...overrides,
})

describe('getMeetingSubstate', () => {
  it('returns null when there are no meetings', () => {
    expect(getMeetingSubstate([], [], NOW)).toBeNull()
  })

  it('returns completed-awaiting-proposal when a draftable meeting exists', () => {
    const m = meeting({
      id: 'm1',
      status: 'completed',
      completed_at: '2026-04-10T00:00:00Z',
    })
    expect(getMeetingSubstate([m], [], NOW)).toBe('completed-awaiting-proposal')
  })

  it('prefers awaiting-proposal even when a future meeting is also scheduled', () => {
    const completed = meeting({
      id: 'old',
      status: 'completed',
      completed_at: '2026-04-10T00:00:00Z',
    })
    const future = meeting({
      id: 'new',
      status: 'scheduled',
      scheduled_at: '2026-05-01T00:00:00Z',
    })
    expect(getMeetingSubstate([future, completed], [], NOW)).toBe('completed-awaiting-proposal')
  })

  it('returns upcoming when most-recent meeting is scheduled in the future', () => {
    const m = meeting({ id: 'm1', scheduled_at: '2026-05-01T00:00:00Z' })
    expect(getMeetingSubstate([m], [], NOW)).toBe('upcoming')
  })

  it('returns past-due when most-recent scheduled meeting is in the past', () => {
    const m = meeting({ id: 'm1', scheduled_at: '2026-04-20T00:00:00Z' })
    expect(getMeetingSubstate([m], [], NOW)).toBe('past-due')
  })

  it('returns awaiting-booking when scheduled but no scheduled_at set', () => {
    const m = meeting({ id: 'm1', scheduled_at: null })
    expect(getMeetingSubstate([m], [], NOW)).toBe('awaiting-booking')
  })

  it('returns "other" for terminal states with no actionable next step', () => {
    const m = meeting({
      id: 'm1',
      status: 'cancelled',
      scheduled_at: '2026-04-10T00:00:00Z',
    })
    expect(getMeetingSubstate([m], [], NOW)).toBe('other')
  })

  it('treats a completed meeting that already has a quote as not-draftable', () => {
    const m = meeting({
      id: 'm1',
      status: 'completed',
      completed_at: '2026-04-10T00:00:00Z',
    })
    const q: Q = { meeting_id: 'm1', assessment_id: 'a1' }
    // No draftable meeting → falls through to head classification → "other"
    // (status is 'completed', no future scheduled rows)
    expect(getMeetingSubstate([m], [q], NOW)).toBe('other')
  })
})

describe('findNextScheduledMeeting', () => {
  it('returns null when there are no future scheduled meetings', () => {
    const past = meeting({ id: 'm1', scheduled_at: '2026-03-01T00:00:00Z' })
    expect(findNextScheduledMeeting([past], NOW)).toBeNull()
  })

  it('returns the closest future scheduled meeting', () => {
    const farther = meeting({ id: 'far', scheduled_at: '2026-06-01T00:00:00Z' })
    const closer = meeting({ id: 'close', scheduled_at: '2026-05-01T00:00:00Z' })
    expect(findNextScheduledMeeting([farther, closer], NOW)?.id).toBe('close')
  })

  it('skips meetings whose status is not "scheduled"', () => {
    const cancelled = meeting({
      id: 'cx',
      status: 'cancelled',
      scheduled_at: '2026-05-01T00:00:00Z',
    })
    const upcoming = meeting({
      id: 'up',
      scheduled_at: '2026-06-01T00:00:00Z',
    })
    expect(findNextScheduledMeeting([cancelled, upcoming], NOW)?.id).toBe('up')
  })

  it('skips scheduled meetings without a scheduled_at', () => {
    const noDate = meeting({ id: 'no-date', scheduled_at: null })
    const dated = meeting({ id: 'dated', scheduled_at: '2026-05-01T00:00:00Z' })
    expect(findNextScheduledMeeting([noDate, dated], NOW)?.id).toBe('dated')
  })
})
