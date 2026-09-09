/**
 * Tests for the Operator calendar list helper
 * (src/lib/portal/operator/calendar.ts).
 *
 * The page surface composes parseCalendarListParams → applyCalendarFilters
 * → applyCalendarSort → paginateCalendarItems, plus a separate
 * `detectConflicts` over the page rows. Each piece is independently
 * exercised here so the URL contract and the conflict semantics are
 * regression-protected against drift before the Hermes bridge (#821)
 * and the calendar connector (#822) land and start shipping real rows.
 *
 * The page-rendering resolver `listCalendarItems` returns an empty list
 * today (no bridge, no connector). That contract is also tested here —
 * the build must fail loudly if a future change starts seeding mock
 * rows from this module, since that would be a Pattern A/B fabrication
 * violation per CLAUDE.md.
 */

import { describe, it, expect } from 'vitest'
import {
  CALENDAR_ITEM_TYPES,
  DEFAULT_CALENDAR_PAGE_SIZE,
  MAX_CALENDAR_PAGE_SIZE,
  applyCalendarFilters,
  applyCalendarSort,
  buildCalendarListPage,
  detectConflicts,
  formatCalendarItemType,
  formatTimeRange,
  listCalendarItems,
  paginateCalendarItems,
  parseCalendarListParams,
  type CalendarItem,
  type CalendarListParams,
} from '../src/lib/portal/operator/calendar'
import type { SubscriptionRow } from '../src/lib/portal/product-access'

function makeItem(overrides: Partial<CalendarItem> = {}): CalendarItem {
  return {
    id: 'c-1',
    type: 'ai_scheduled',
    title: 'Deposition: Acme v. Beta',
    startsAt: '2026-05-21T09:00:00Z',
    endsAt: '2026-05-21T10:30:00Z',
    skill: 'deposition-scheduling',
    relatedMatterId: 'm-1',
    relatedMatterTitle: 'Acme v. Beta',
    location: 'Conference Room B',
    ...overrides,
  }
}

const baseParams: CalendarListParams = {
  types: [],
  fromIso: null,
  toIso: null,
  sort: 'start_asc',
  page: 1,
  pageSize: DEFAULT_CALENDAR_PAGE_SIZE,
}

describe('parseCalendarListParams', () => {
  it('returns defaults for an empty query string', () => {
    const params = parseCalendarListParams(new URLSearchParams())
    expect(params).toEqual(baseParams)
  })

  it('parses repeated type params and comma-separated type values', () => {
    const params = parseCalendarListParams(
      new URLSearchParams('type=ai_scheduled&type=ai_proposed,ai_scheduled')
    )
    expect(params.types.slice().sort()).toEqual(['ai_proposed', 'ai_scheduled'])
  })

  it('silently drops unknown type values rather than throwing', () => {
    const params = parseCalendarListParams(new URLSearchParams('type=ai_scheduled,bogus,deleted'))
    expect(params.types).toEqual(['ai_scheduled'])
  })

  it('accepts a valid from/to ISO date pair', () => {
    const params = parseCalendarListParams(
      new URLSearchParams('from=2026-05-01&to=2026-05-31T23:59:59Z')
    )
    expect(params.fromIso).toBe('2026-05-01')
    expect(params.toIso).toBe('2026-05-31T23:59:59Z')
  })

  it('rejects unparseable from/to values', () => {
    const params = parseCalendarListParams(new URLSearchParams('from=nope&to=alsonope'))
    expect(params.fromIso).toBeNull()
    expect(params.toIso).toBeNull()
  })

  it('treats empty from/to as null', () => {
    const params = parseCalendarListParams(new URLSearchParams('from=&to=  '))
    expect(params.fromIso).toBeNull()
    expect(params.toIso).toBeNull()
  })

  it('falls back to start_asc on unknown sort values', () => {
    expect(parseCalendarListParams(new URLSearchParams('sort=random')).sort).toBe('start_asc')
  })

  it('accepts the valid sort vocabulary', () => {
    expect(parseCalendarListParams(new URLSearchParams('sort=start_desc')).sort).toBe('start_desc')
    expect(parseCalendarListParams(new URLSearchParams('sort=start_asc')).sort).toBe('start_asc')
  })

  it('clamps page to 1 when below 1', () => {
    expect(parseCalendarListParams(new URLSearchParams('page=0')).page).toBe(1)
    expect(parseCalendarListParams(new URLSearchParams('page=-2')).page).toBe(1)
  })

  it('caps pageSize at MAX_CALENDAR_PAGE_SIZE', () => {
    expect(parseCalendarListParams(new URLSearchParams('pageSize=10000')).pageSize).toBe(
      MAX_CALENDAR_PAGE_SIZE
    )
  })

  it('uses DEFAULT_CALENDAR_PAGE_SIZE for invalid pageSize values', () => {
    expect(parseCalendarListParams(new URLSearchParams('pageSize=abc')).pageSize).toBe(
      DEFAULT_CALENDAR_PAGE_SIZE
    )
    expect(parseCalendarListParams(new URLSearchParams('pageSize=0')).pageSize).toBe(
      DEFAULT_CALENDAR_PAGE_SIZE
    )
  })
})

describe('applyCalendarFilters', () => {
  const rows: CalendarItem[] = [
    makeItem({
      id: 'a',
      type: 'ai_scheduled',
      startsAt: '2026-05-01T10:00:00Z',
      endsAt: '2026-05-01T11:00:00Z',
    }),
    makeItem({
      id: 'b',
      type: 'ai_proposed',
      startsAt: '2026-05-15T10:00:00Z',
      endsAt: '2026-05-15T11:00:00Z',
    }),
    makeItem({
      id: 'c',
      type: 'ai_scheduled',
      startsAt: '2026-05-30T10:00:00Z',
      endsAt: '2026-05-30T11:00:00Z',
    }),
  ]

  it('returns all rows when no filters are set', () => {
    expect(applyCalendarFilters(rows, baseParams)).toHaveLength(3)
  })

  it('filters by single type', () => {
    const result = applyCalendarFilters(rows, { ...baseParams, types: ['ai_proposed'] })
    expect(result.map((r) => r.id)).toEqual(['b'])
  })

  it('filters by multiple types (union)', () => {
    const result = applyCalendarFilters(rows, {
      ...baseParams,
      types: ['ai_scheduled', 'ai_proposed'],
    })
    expect(result.map((r) => r.id).sort()).toEqual(['a', 'b', 'c'])
  })

  it('filters by fromIso (drops anything before)', () => {
    const result = applyCalendarFilters(rows, { ...baseParams, fromIso: '2026-05-10' })
    expect(result.map((r) => r.id).sort()).toEqual(['b', 'c'])
  })

  it('filters by toIso (drops anything after)', () => {
    const result = applyCalendarFilters(rows, { ...baseParams, toIso: '2026-05-20' })
    expect(result.map((r) => r.id).sort()).toEqual(['a', 'b'])
  })

  it('combines from + to into a window', () => {
    const result = applyCalendarFilters(rows, {
      ...baseParams,
      fromIso: '2026-05-10',
      toIso: '2026-05-20',
    })
    expect(result.map((r) => r.id)).toEqual(['b'])
  })

  it('combines all filters', () => {
    const result = applyCalendarFilters(rows, {
      ...baseParams,
      types: ['ai_scheduled'],
      fromIso: '2026-05-10',
    })
    expect(result.map((r) => r.id)).toEqual(['c'])
  })
})

describe('applyCalendarSort', () => {
  const rows: CalendarItem[] = [
    makeItem({ id: 'mid', startsAt: '2026-05-15T10:00:00Z', endsAt: '2026-05-15T11:00:00Z' }),
    makeItem({ id: 'late', startsAt: '2026-05-30T10:00:00Z', endsAt: '2026-05-30T11:00:00Z' }),
    makeItem({ id: 'early', startsAt: '2026-05-01T10:00:00Z', endsAt: '2026-05-01T11:00:00Z' }),
  ]

  it('start_asc puts earliest first', () => {
    const result = applyCalendarSort(rows, 'start_asc')
    expect(result.map((r) => r.id)).toEqual(['early', 'mid', 'late'])
  })

  it('start_desc puts latest first', () => {
    const result = applyCalendarSort(rows, 'start_desc')
    expect(result.map((r) => r.id)).toEqual(['late', 'mid', 'early'])
  })

  it('sorts unparseable startsAt to the end (not the start)', () => {
    const rowsWithBad: CalendarItem[] = [
      ...rows,
      makeItem({ id: 'bad', startsAt: 'not-a-date', endsAt: 'also-not' }),
    ]
    const result = applyCalendarSort(rowsWithBad, 'start_asc')
    expect(result[result.length - 1].id).toBe('bad')
  })
})

describe('paginateCalendarItems', () => {
  const rows: CalendarItem[] = Array.from({ length: 125 }, (_, i) =>
    makeItem({
      id: `c-${i}`,
      startsAt: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T10:00:00Z`,
      endsAt: `2026-05-${String((i % 28) + 1).padStart(2, '0')}T11:00:00Z`,
    })
  )

  it('returns one page when totalCount <= pageSize', () => {
    const page = paginateCalendarItems(rows.slice(0, 10), 1, 50)
    expect(page.rows).toHaveLength(10)
    expect(page.pageCount).toBe(1)
  })

  it('paginates correctly across multiple pages', () => {
    const page1 = paginateCalendarItems(rows, 1, 50)
    expect(page1.rows).toHaveLength(50)
    expect(page1.pageCount).toBe(3)

    const page3 = paginateCalendarItems(rows, 3, 50)
    expect(page3.rows).toHaveLength(25)
  })

  it('clamps out-of-range page to last page', () => {
    const page = paginateCalendarItems(rows, 999, 50)
    expect(page.page).toBe(3)
  })

  it('clamps below-range page to page 1', () => {
    const page = paginateCalendarItems(rows, -5, 50)
    expect(page.page).toBe(1)
  })

  it('returns pageCount=1 for empty input', () => {
    const page = paginateCalendarItems([], 1, 50)
    expect(page.rows).toEqual([])
    expect(page.totalCount).toBe(0)
    expect(page.pageCount).toBe(1)
  })
})

describe('buildCalendarListPage', () => {
  it('composes filter → sort → paginate', () => {
    const rows: CalendarItem[] = [
      makeItem({
        id: 'a',
        type: 'ai_scheduled',
        startsAt: '2026-05-01T10:00:00Z',
        endsAt: '2026-05-01T11:00:00Z',
      }),
      makeItem({
        id: 'b',
        type: 'ai_scheduled',
        startsAt: '2026-05-30T10:00:00Z',
        endsAt: '2026-05-30T11:00:00Z',
      }),
      makeItem({
        id: 'c',
        type: 'ai_proposed',
        startsAt: '2026-05-15T10:00:00Z',
        endsAt: '2026-05-15T11:00:00Z',
      }),
    ]

    const page = buildCalendarListPage(rows, {
      ...baseParams,
      types: ['ai_scheduled'],
      sort: 'start_desc',
      page: 1,
      pageSize: 50,
    })

    expect(page.rows.map((r) => r.id)).toEqual(['b', 'a'])
    expect(page.totalCount).toBe(2)
  })
})

describe('detectConflicts', () => {
  it('returns an empty map when there are no items', () => {
    expect(detectConflicts([])).toEqual(new Map())
  })

  it('returns an empty map when items do not overlap', () => {
    const rows: CalendarItem[] = [
      makeItem({ id: 'a', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T10:00:00Z' }),
      makeItem({ id: 'b', startsAt: '2026-05-01T11:00:00Z', endsAt: '2026-05-01T12:00:00Z' }),
      makeItem({ id: 'c', startsAt: '2026-05-02T09:00:00Z', endsAt: '2026-05-02T10:00:00Z' }),
    ]
    expect(detectConflicts(rows)).toEqual(new Map())
  })

  it('treats edge-touching items as non-conflicting (back-to-back schedule)', () => {
    // a ends exactly when b starts. By half-open interval semantics
    // this is NOT a conflict — it's a tight schedule, not a clash.
    const rows: CalendarItem[] = [
      makeItem({ id: 'a', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T10:00:00Z' }),
      makeItem({ id: 'b', startsAt: '2026-05-01T10:00:00Z', endsAt: '2026-05-01T11:00:00Z' }),
    ]
    expect(detectConflicts(rows)).toEqual(new Map())
  })

  it('reports overlapping items in both directions', () => {
    const rows: CalendarItem[] = [
      makeItem({ id: 'a', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T10:30:00Z' }),
      makeItem({ id: 'b', startsAt: '2026-05-01T10:00:00Z', endsAt: '2026-05-01T11:00:00Z' }),
    ]
    const result = detectConflicts(rows)
    expect(result.get('a')).toEqual(['b'])
    expect(result.get('b')).toEqual(['a'])
  })

  it('reports multi-way overlaps', () => {
    // All three overlap in the 10:00-10:30 window.
    const rows: CalendarItem[] = [
      makeItem({ id: 'a', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T10:30:00Z' }),
      makeItem({ id: 'b', startsAt: '2026-05-01T10:00:00Z', endsAt: '2026-05-01T11:00:00Z' }),
      makeItem({ id: 'c', startsAt: '2026-05-01T10:15:00Z', endsAt: '2026-05-01T10:45:00Z' }),
    ]
    const result = detectConflicts(rows)
    expect(result.get('a')).toEqual(['b', 'c'])
    expect(result.get('b')).toEqual(['a', 'c'])
    expect(result.get('c')).toEqual(['a', 'b'])
  })

  it('silently excludes items with malformed time ranges', () => {
    // Bad item should not conflict with anything, even when its
    // partial fields look like they might overlap. Empty-state
    // contract: the resolver never emits malformed rows; this is the
    // defensive path.
    const rows: CalendarItem[] = [
      makeItem({ id: 'good', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T10:00:00Z' }),
      makeItem({ id: 'bad', startsAt: 'not-a-date', endsAt: 'also-not' }),
      makeItem({
        id: 'zero',
        startsAt: '2026-05-01T09:00:00Z',
        endsAt: '2026-05-01T09:00:00Z',
      }),
    ]
    expect(detectConflicts(rows)).toEqual(new Map())
  })

  it('returns deterministic sort of conflicting ids', () => {
    // Insertion order would otherwise leak; the public contract is a
    // sorted list per id so tests / hashing stay stable.
    const rows: CalendarItem[] = [
      makeItem({ id: 'z', startsAt: '2026-05-01T09:00:00Z', endsAt: '2026-05-01T11:00:00Z' }),
      makeItem({ id: 'a', startsAt: '2026-05-01T09:30:00Z', endsAt: '2026-05-01T10:00:00Z' }),
      makeItem({ id: 'm', startsAt: '2026-05-01T09:45:00Z', endsAt: '2026-05-01T10:15:00Z' }),
    ]
    const result = detectConflicts(rows)
    expect(result.get('z')).toEqual(['a', 'm'])
  })
})

describe('formatCalendarItemType', () => {
  it('maps every type value to a friendly label', () => {
    expect(formatCalendarItemType('ai_scheduled')).toBe('Scheduled')
    expect(formatCalendarItemType('ai_proposed')).toBe('Proposed')
  })

  it('every type vocabulary entry has a label (exhaustiveness)', () => {
    for (const type of CALENDAR_ITEM_TYPES) {
      const label = formatCalendarItemType(type)
      expect(label.length).toBeGreaterThan(0)
    }
  })
})

describe('formatTimeRange', () => {
  it('formats a valid ISO start/end pair', () => {
    const formatted = formatTimeRange('2026-05-21T09:00:00Z', '2026-05-21T10:30:00Z')
    expect(formatted.datePart).toContain('May')
    expect(formatted.datePart).toContain('21')
    expect(formatted.timePart).toContain('9:00')
    expect(formatted.timePart).toContain('10:30')
    expect(formatted.tzPart).toBe('UTC')
  })

  it('passes through unparseable values without throwing', () => {
    const formatted = formatTimeRange('not-a-date', 'also-not')
    expect(formatted.datePart).toBe('not-a-date')
    expect(formatted.timePart).toBe('also-not')
    expect(formatted.tzPart).toBe('')
  })
})

describe('listCalendarItems', () => {
  it('returns an empty page until the Hermes bridge (#821) and connector (#822) land', async () => {
    // No fabrication: the bridge stub returns []. If a future change
    // adds mock rows in fetchCalendarItemsFromHermes this test fails
    // loudly — that would be a Pattern A/B fabrication violation per
    // CLAUDE.md.
    const stubSubscription: SubscriptionRow = {
      id: 'sub-test',
      org_id: 'org-test',
      entity_id: 'ent-test',
      product_slug: 'operator',
      instance_slug: 'smd',
      status: 'active',
      started_at: '2026-05-21T00:00:00Z',
      ended_at: null,
      settings_json: null,
      service_id: null,
      stripe_subscription_id: null,
      created_at: '2026-05-21T00:00:00Z',
      updated_at: '2026-05-21T00:00:00Z',
    }
    const page = await listCalendarItems(stubSubscription, baseParams)
    expect(page.rows).toEqual([])
    expect(page.totalCount).toBe(0)
    expect(page.page).toBe(1)
    expect(page.pageCount).toBe(1)
  })
})
