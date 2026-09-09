/**
 * Tests for the Operator audit log resolver
 * (src/lib/portal/operator/audit.ts).
 *
 * The page surface composes parseAuditListParams →
 * applyAuditFilters → applyAuditSort → paginateAuditEntries. Each
 * piece is independently exercised here so the URL contract
 * (filter / sort / pagination) is regression-protected against drift
 * before the Hermes bridge (#821) lands and starts shipping real rows.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  AUDIT_ACTION_TYPES,
  CONSOLE_ACTION_TYPES,
  AUDIT_DECISIONS,
  AUDIT_SORTS,
  DEFAULT_AUDIT_PAGE_SIZE,
  DEFAULT_AUDIT_RANGE_DAYS,
  MAX_AUDIT_PAGE_SIZE,
  applyAuditFilters,
  applyAuditSort,
  buildAuditListPage,
  decisionTone,
  defaultAuditDateRange,
  distinctAuditSkills,
  formatAuditDecision,
  formatAuditTimestamp,
  paginateAuditEntries,
  parseAuditListParams,
  type AuditEntry,
  type AuditListParams,
} from '../src/lib/portal/operator/audit'

function makeEntry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: '01HX5N3K2A',
    ts: '2026-05-20T10:00:00.000Z',
    actor: 'person-1',
    actorRole: 'staff',
    action: 'DRAFT_CREATED',
    target: 'draft-9',
    decision: 'draft_for_review',
    reason: 'Routine intake follow-up.',
    skill: 'client-intake',
    ...overrides,
  }
}

const baseParams: AuditListParams = {
  skills: [],
  actions: [],
  from: null,
  to: null,
  q: null,
  sort: 'ts_desc',
  page: 1,
  pageSize: DEFAULT_AUDIT_PAGE_SIZE,
}

describe('AUDIT_ACTION_TYPES vocabulary', () => {
  it('matches the writer-side ACCEPTED_ACTION_TYPES exactly (full parity)', () => {
    // 2026-06-12 code review: this was a sampled `toContain` check, which
    // let the two vocabularies drift silently (the per-step decommission
    // types shipped on the writer side first). Parse the python constant
    // from source and assert set equality — adding a type on either side
    // without the other now fails CI.
    const pySource = readFileSync(resolve('operator/adapter/audit_log.py'), 'utf-8')
    const start = pySource.indexOf('ACCEPTED_ACTION_TYPES = frozenset(')
    expect(start).toBeGreaterThan(-1)
    const end = pySource.indexOf('\n)', start)
    expect(end).toBeGreaterThan(start)
    const block = pySource.slice(start, end)
    const pyTypes = [...new Set([...block.matchAll(/"([A-Z0-9_]+)"/g)].map((m) => m[1]))]
    expect(pyTypes.length).toBeGreaterThan(0)
    expect([...AUDIT_ACTION_TYPES].sort()).toEqual(pyTypes.sort())
  })

  it('has no duplicate entries', () => {
    const set = new Set(AUDIT_ACTION_TYPES)
    expect(set.size).toBe(AUDIT_ACTION_TYPES.length)
  })
})

describe('parseAuditListParams', () => {
  it('returns defaults for an empty query string', () => {
    const params = parseAuditListParams(new URLSearchParams())
    expect(params).toEqual(baseParams)
  })

  it('parses repeated skill params and comma-separated skill values', () => {
    const params = parseAuditListParams(
      new URLSearchParams('skill=intake&skill=deadline,reminder&skill=intake')
    )
    expect(params.skills.slice().sort()).toEqual(['deadline', 'intake', 'reminder'])
  })

  it('drops action values outside the closed vocabulary', () => {
    const params = parseAuditListParams(
      new URLSearchParams('action=DRAFT_CREATED&action=NOT_A_REAL_ACTION,DRAFT_APPROVED')
    )
    expect(params.actions.slice().sort()).toEqual(['DRAFT_APPROVED', 'DRAFT_CREATED'])
  })

  it('accepts every value in AUDIT_ACTION_TYPES via the action filter', () => {
    // Defensive: if any vocabulary entry would silently get dropped by
    // the parser, this test catches it before users hit it.
    for (const action of AUDIT_ACTION_TYPES) {
      const params = parseAuditListParams(new URLSearchParams(`action=${action}`))
      expect(params.actions).toEqual([action])
    }
  })

  it('accepts every CONSOLE_ACTION_TYPES value via the action filter', () => {
    // Console-plane synthetic actions (logins, team/config events) are not
    // Machine vocabulary but must survive ?action= bookmarks.
    for (const action of CONSOLE_ACTION_TYPES) {
      const params = parseAuditListParams(new URLSearchParams(`action=${action}`))
      expect(params.actions).toEqual([action])
    }
  })

  it('normalizes from/to into ISO-parseable strings, dropping invalid', () => {
    expect(parseAuditListParams(new URLSearchParams('from=2026-05-01')).from).toBe('2026-05-01')
    expect(parseAuditListParams(new URLSearchParams('from=2026-05-01T00:00:00Z')).from).toBe(
      '2026-05-01T00:00:00Z'
    )
    expect(parseAuditListParams(new URLSearchParams('from=not-a-date')).from).toBeNull()
    expect(parseAuditListParams(new URLSearchParams('from=')).from).toBeNull()
  })

  it('lowercases q', () => {
    expect(parseAuditListParams(new URLSearchParams('q=Foo')).q).toBe('foo')
  })

  it('treats empty q as no filter', () => {
    expect(parseAuditListParams(new URLSearchParams('q=   ')).q).toBeNull()
  })

  it('falls back to ts_desc on unknown sort values', () => {
    expect(parseAuditListParams(new URLSearchParams('sort=random')).sort).toBe('ts_desc')
  })

  it('clamps page to 1 when below 1', () => {
    expect(parseAuditListParams(new URLSearchParams('page=0')).page).toBe(1)
    expect(parseAuditListParams(new URLSearchParams('page=-2')).page).toBe(1)
  })

  it('caps pageSize at MAX_AUDIT_PAGE_SIZE', () => {
    expect(parseAuditListParams(new URLSearchParams('pageSize=10000')).pageSize).toBe(
      MAX_AUDIT_PAGE_SIZE
    )
  })

  it('uses DEFAULT_AUDIT_PAGE_SIZE for invalid pageSize values', () => {
    expect(parseAuditListParams(new URLSearchParams('pageSize=abc')).pageSize).toBe(
      DEFAULT_AUDIT_PAGE_SIZE
    )
    expect(parseAuditListParams(new URLSearchParams('pageSize=0')).pageSize).toBe(
      DEFAULT_AUDIT_PAGE_SIZE
    )
  })
})

describe('defaultAuditDateRange', () => {
  it('returns a window N days wide ending at nowMs', () => {
    const now = Date.UTC(2026, 4, 21, 12, 0, 0)
    const range = defaultAuditDateRange(7, now)
    expect(range.to).toBe(new Date(now).toISOString())
    expect(range.from).toBe(new Date(now - 7 * 86400_000).toISOString())
  })

  it('defaults to DEFAULT_AUDIT_RANGE_DAYS when not specified', () => {
    const now = Date.UTC(2026, 4, 21, 12, 0, 0)
    const range = defaultAuditDateRange(undefined, now)
    expect(Date.parse(range.to) - Date.parse(range.from)).toBe(DEFAULT_AUDIT_RANGE_DAYS * 86400_000)
  })
})

describe('applyAuditFilters', () => {
  const rows: AuditEntry[] = [
    makeEntry({
      id: 'a',
      ts: '2026-05-20T10:00:00.000Z',
      skill: 'intake',
      action: 'DRAFT_CREATED',
      target: 'draft-1',
      reason: 'Routine intake follow up.',
      actor: 'pat',
    }),
    makeEntry({
      id: 'b',
      ts: '2026-05-19T10:00:00.000Z',
      skill: 'deadline',
      action: 'DRAFT_APPROVED',
      target: 'draft-2',
      reason: 'Approved by reviewer.',
      actor: 'jordan',
    }),
    makeEntry({
      id: 'c',
      ts: '2026-05-10T10:00:00.000Z',
      skill: 'intake',
      action: 'TRUST_PROMOTED',
      target: null,
      reason: null,
      actor: 'agent',
    }),
  ]

  it('returns all rows when no filters are set', () => {
    expect(applyAuditFilters(rows, baseParams)).toHaveLength(3)
  })

  it('filters by single skill', () => {
    const result = applyAuditFilters(rows, { ...baseParams, skills: ['intake'] })
    expect(result.map((r) => r.id).sort()).toEqual(['a', 'c'])
  })

  it('filters by multiple actions (union)', () => {
    const result = applyAuditFilters(rows, {
      ...baseParams,
      actions: ['DRAFT_CREATED', 'TRUST_PROMOTED'],
    })
    expect(result.map((r) => r.id).sort()).toEqual(['a', 'c'])
  })

  it('filters by from (inclusive lower bound)', () => {
    const result = applyAuditFilters(rows, { ...baseParams, from: '2026-05-19T00:00:00Z' })
    expect(result.map((r) => r.id).sort()).toEqual(['a', 'b'])
  })

  it('filters by to (inclusive upper bound)', () => {
    const result = applyAuditFilters(rows, { ...baseParams, to: '2026-05-19T23:59:59Z' })
    expect(result.map((r) => r.id).sort()).toEqual(['b', 'c'])
  })

  it('q matches against reason (case-insensitive)', () => {
    const result = applyAuditFilters(rows, { ...baseParams, q: 'approved' })
    expect(result.map((r) => r.id)).toEqual(['b'])
  })

  it('q matches against actor', () => {
    const result = applyAuditFilters(rows, { ...baseParams, q: 'jordan' })
    expect(result.map((r) => r.id)).toEqual(['b'])
  })

  it('q matches against target', () => {
    const result = applyAuditFilters(rows, { ...baseParams, q: 'draft-1' })
    expect(result.map((r) => r.id)).toEqual(['a'])
  })

  it('q on rows with null reason still matches actor / target', () => {
    // Row c has reason=null; q='agent' should still match via actor.
    const result = applyAuditFilters(rows, { ...baseParams, q: 'agent' })
    expect(result.map((r) => r.id)).toEqual(['c'])
  })

  it('combines all filters', () => {
    const result = applyAuditFilters(rows, {
      ...baseParams,
      skills: ['intake'],
      actions: ['DRAFT_CREATED'],
      from: '2026-05-19T00:00:00Z',
    })
    expect(result.map((r) => r.id)).toEqual(['a'])
  })
})

describe('applyAuditSort', () => {
  const rows: AuditEntry[] = [
    makeEntry({ id: 'oldest', ts: '2026-05-01T10:00:00.000Z' }),
    makeEntry({ id: 'newest', ts: '2026-05-21T10:00:00.000Z' }),
    makeEntry({ id: 'middle', ts: '2026-05-10T10:00:00.000Z' }),
  ]

  it('ts_desc puts newest first', () => {
    const result = applyAuditSort(rows, 'ts_desc')
    expect(result.map((r) => r.id)).toEqual(['newest', 'middle', 'oldest'])
  })

  it('ts_asc puts oldest first', () => {
    const result = applyAuditSort(rows, 'ts_asc')
    expect(result.map((r) => r.id)).toEqual(['oldest', 'middle', 'newest'])
  })

  it('covers every member of AUDIT_SORTS', () => {
    // Defensive: if the AUDIT_SORTS vocabulary grows, this test fails
    // until applyAuditSort gets a matching case branch (the switch is
    // exhaustive in TS; this catches the runtime side).
    for (const sort of AUDIT_SORTS) {
      expect(() => applyAuditSort(rows, sort)).not.toThrow()
    }
  })
})

describe('paginateAuditEntries', () => {
  const rows: AuditEntry[] = Array.from({ length: 250 }, (_, i) =>
    makeEntry({ id: `e-${i}`, ts: new Date(Date.UTC(2026, 4, 1) + i * 60_000).toISOString() })
  )

  it('returns one page when totalCount <= pageSize', () => {
    const page = paginateAuditEntries(rows.slice(0, 10), 1, 100)
    expect(page.rows).toHaveLength(10)
    expect(page.pageCount).toBe(1)
  })

  it('paginates correctly across multiple pages', () => {
    const page1 = paginateAuditEntries(rows, 1, 100)
    expect(page1.rows).toHaveLength(100)
    expect(page1.rows[0].id).toBe('e-0')
    expect(page1.pageCount).toBe(3)

    const page2 = paginateAuditEntries(rows, 2, 100)
    expect(page2.rows[0].id).toBe('e-100')

    const page3 = paginateAuditEntries(rows, 3, 100)
    expect(page3.rows).toHaveLength(50)
    expect(page3.rows[49].id).toBe('e-249')
  })

  it('clamps out-of-range page to last page', () => {
    const page = paginateAuditEntries(rows, 999, 100)
    expect(page.page).toBe(3)
    expect(page.rows[0].id).toBe('e-200')
  })

  it('clamps below-range page to page 1', () => {
    const page = paginateAuditEntries(rows, -5, 100)
    expect(page.page).toBe(1)
  })

  it('returns pageCount=1 for empty input', () => {
    const page = paginateAuditEntries([], 1, 100)
    expect(page.rows).toEqual([])
    expect(page.totalCount).toBe(0)
    expect(page.pageCount).toBe(1)
  })
})

describe('buildAuditListPage', () => {
  it('composes filter → sort → paginate (default page size 100)', () => {
    const rows: AuditEntry[] = [
      makeEntry({ id: 'a', skill: 'intake', ts: '2026-05-20T10:00:00.000Z' }),
      makeEntry({ id: 'b', skill: 'intake', ts: '2026-05-19T10:00:00.000Z' }),
      makeEntry({ id: 'c', skill: 'deadline', ts: '2026-05-18T10:00:00.000Z' }),
    ]

    const page = buildAuditListPage(rows, {
      ...baseParams,
      skills: ['intake'],
      sort: 'ts_desc',
    })

    expect(page.rows.map((r) => r.id)).toEqual(['a', 'b'])
    expect(page.totalCount).toBe(2)
    expect(page.pageSize).toBe(DEFAULT_AUDIT_PAGE_SIZE)
  })
})

describe('formatAuditTimestamp', () => {
  it('renders a parsable ISO timestamp in UTC', () => {
    const out = formatAuditTimestamp('2026-05-20T10:30:45.000Z')
    // We don't assert exact locale spacing (Intl output drifts across
    // Node releases), but the year, month, and the UTC marker should
    // all be present.
    expect(out).toContain('2026')
    expect(out).toContain('May')
    expect(out).toMatch(/UTC|Z/)
  })

  it('returns the raw value when the timestamp cannot be parsed', () => {
    // Never fabricate a "just now" or "unknown" for malformed input —
    // the raw value lets reviewers see what the system actually recorded.
    expect(formatAuditTimestamp('not-a-date')).toBe('not-a-date')
    expect(formatAuditTimestamp('')).toBe('')
  })
})

describe('formatAuditDecision / decisionTone', () => {
  it('maps every decision value to a friendly label', () => {
    expect(formatAuditDecision('allow')).toBe('Allowed')
    expect(formatAuditDecision('draft_for_review')).toBe('Drafted for review')
    expect(formatAuditDecision('refuse')).toBe('Refused')
  })

  it('returns the empty string for null', () => {
    expect(formatAuditDecision(null)).toBe('')
  })

  it('covers every member of AUDIT_DECISIONS', () => {
    for (const decision of AUDIT_DECISIONS) {
      expect(formatAuditDecision(decision)).not.toBe('')
    }
  })

  it('decisionTone returns the expected tone per decision', () => {
    expect(decisionTone('refuse')).toBe('danger')
    expect(decisionTone('allow')).toBe('success')
    expect(decisionTone('draft_for_review')).toBe('neutral')
    expect(decisionTone(null)).toBe('neutral')
  })
})

describe('distinctAuditSkills', () => {
  it('returns empty array for empty input', () => {
    expect(distinctAuditSkills([])).toEqual([])
  })

  it('returns unique skills sorted alphabetically, dropping null', () => {
    const rows: AuditEntry[] = [
      makeEntry({ id: 'a', skill: 'deadline' }),
      makeEntry({ id: 'b', skill: 'intake' }),
      makeEntry({ id: 'c', skill: null }),
      makeEntry({ id: 'd', skill: 'deadline' }),
    ]
    expect(distinctAuditSkills(rows)).toEqual(['deadline', 'intake'])
  })
})
