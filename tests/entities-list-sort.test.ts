/**
 * Pure-helper tests for the per-stage entity-list re-sorts.
 * No DB — each helper takes already-loaded entities + hydrated maps and
 * returns a re-ordered array.
 */

import { describe, it, expect } from 'vitest'

import { sortProspectRows, sortMeetingsRows, sortLostRows } from '../src/lib/entities/list-sort'
import type { Entity } from '../src/lib/db/entities'
import type { Meeting } from '../src/lib/db/meetings'
import type { ContextEntry } from '../src/lib/db/context'

const NOW = new Date('2026-04-24T12:00:00Z')

const entity = (id: string, overrides: Partial<Entity> = {}): Entity =>
  ({
    id,
    org_id: 'org',
    name: id.toUpperCase(),
    slug: id,
    phone: null,
    website: null,
    stage: 'prospect',
    stage_changed_at: '2026-04-01T00:00:00Z',
    pain_score: null,
    vertical: null,
    area: null,
    employee_count: null,
    tier: null,
    summary: null,
    next_action: null,
    next_action_at: null,
    source_pipeline: null,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
    ...overrides,
  }) as Entity

describe('sortProspectRows', () => {
  it('clusters inert (no outreach drafted) rows above active rows', () => {
    const a = entity('a-active', { next_action_at: '2026-04-15T00:00:00Z' })
    const b = entity('b-inert', { next_action_at: '2026-04-15T00:00:00Z' })
    const c = entity('c-active', { next_action_at: '2026-04-10T00:00:00Z' })
    const outreach = new Map<string, ContextEntry>([
      ['a-active', {} as ContextEntry],
      ['c-active', {} as ContextEntry],
    ])
    const result = sortProspectRows([a, b, c], outreach)
    expect(result.map((e) => e.id)).toEqual(['b-inert', 'c-active', 'a-active'])
  })

  it('orders within each cluster by next_action_at ASC, nulls trailing', () => {
    const noDate = entity('no-date', { next_action_at: null })
    const earlier = entity('earlier', { next_action_at: '2026-04-10T00:00:00Z' })
    const later = entity('later', { next_action_at: '2026-04-15T00:00:00Z' })
    const outreach = new Map<string, ContextEntry>()
    const result = sortProspectRows([noDate, later, earlier], outreach)
    expect(result.map((e) => e.id)).toEqual(['earlier', 'later', 'no-date'])
  })

  it('returns the same array order when there are no rows', () => {
    expect(sortProspectRows([], new Map())).toEqual([])
  })
})

describe('sortMeetingsRows', () => {
  const meeting = (id: string, overrides: Partial<Meeting> = {}): Meeting =>
    ({
      id,
      org_id: 'org',
      entity_id: 'e',
      meeting_type: null,
      scheduled_at: null,
      completed_at: null,
      duration_minutes: null,
      transcript_path: null,
      extraction: null,
      live_notes: null,
      completion_notes: null,
      status: 'scheduled',
      created_at: '2026-04-01T00:00:00Z',
      ...overrides,
    }) as Meeting

  it('puts completed-awaiting-proposal rows first', () => {
    const drafty = entity('drafty')
    const future = entity('future')
    const meetingsMap = new Map<string, Meeting[]>([
      ['drafty', [meeting('m1', { status: 'completed', completed_at: '2026-04-15T00:00:00Z' })]],
      ['future', [meeting('m2', { scheduled_at: '2026-05-01T00:00:00Z' })]],
    ])
    const result = sortMeetingsRows([future, drafty], meetingsMap, new Map(), NOW)
    expect(result.map((e) => e.id)).toEqual(['drafty', 'future'])
  })

  it('orders upcoming rows by earliest scheduled_at first', () => {
    const farther = entity('far')
    const closer = entity('close')
    const meetingsMap = new Map<string, Meeting[]>([
      ['far', [meeting('mf', { scheduled_at: '2026-06-01T00:00:00Z' })]],
      ['close', [meeting('mc', { scheduled_at: '2026-05-01T00:00:00Z' })]],
    ])
    const result = sortMeetingsRows([farther, closer], meetingsMap, new Map(), NOW)
    expect(result.map((e) => e.id)).toEqual(['close', 'far'])
  })

  it('puts past-due ahead of upcoming', () => {
    const pastDue = entity('past')
    const upcoming = entity('up')
    const meetingsMap = new Map<string, Meeting[]>([
      ['past', [meeting('mp', { scheduled_at: '2026-04-20T00:00:00Z' })]],
      ['up', [meeting('mu', { scheduled_at: '2026-05-01T00:00:00Z' })]],
    ])
    const result = sortMeetingsRows([upcoming, pastDue], meetingsMap, new Map(), NOW)
    expect(result.map((e) => e.id)).toEqual(['past', 'up'])
  })

  it('preserves DAL order on ties', () => {
    const a = entity('a')
    const b = entity('b')
    const meetingsMap = new Map<string, Meeting[]>([
      ['a', [meeting('ma', { status: 'cancelled' })]],
      ['b', [meeting('mb', { status: 'cancelled' })]],
    ])
    const result = sortMeetingsRows([a, b], meetingsMap, new Map(), NOW)
    expect(result.map((e) => e.id)).toEqual(['a', 'b'])
  })
})

describe('sortLostRows', () => {
  it('orders most-recently-lost first', () => {
    const earlier = entity('earlier', { stage_changed_at: '2026-03-01T00:00:00Z' })
    const later = entity('later', { stage_changed_at: '2026-04-01T00:00:00Z' })
    const result = sortLostRows([earlier, later])
    expect(result.map((e) => e.id)).toEqual(['later', 'earlier'])
  })

  it('returns an empty array unchanged', () => {
    expect(sortLostRows([])).toEqual([])
  })
})
