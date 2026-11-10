/**
 * The overdue-deferral lister (scripts/ci-reconcile-deferrals.mjs): after merge
 * nothing else reads a runtime row's `enforce_by`, so this lists every
 * deferral whose date has passed and that no later merged PR has discharged
 * with a `met` row. Synthetic bodies only (the repo is public).
 */
import { describe, it, expect } from 'vitest'
import {
  dischargedBy,
  findOverdue,
  formatReport,
  overdueRows,
  windows,
} from '../scripts/ci-reconcile-deferrals.mjs'

const NOW = new Date('2026-11-09T12:00:00Z')

const table = (...rows: string[]) =>
  ['| AC | Status | Evidence |', '|---|---|---|', ...rows].join('\n')

const DEFERRED_PAST = table(
  '| (runtime) the seat sends the reply | deferred | owner: lead; enforce_by: 2026-11-01 |'
)
const DEFERRED_FUTURE = table(
  '| (runtime) the seat sends the reply | deferred | owner: lead; enforce_by: 2026-12-01 |'
)
const UNMET = table(
  '| (runtime) a seat with the lane on answers | not met | |',
  '| (runtime) the first real request is watched | not met | |'
)
const MET_FOR_10 = `Discharges #10.\n\n${table('| (runtime) the seat sends the reply | met | vfy_01ABCDEFGHJKMNPQRSTVWXYZ01 |')}`

describe('overdue deferrals', () => {
  it('lists a deferral valid when merged whose enforce_by has passed (control)', () => {
    expect(overdueRows(DEFERRED_PAST, '2026-10-20T00:00:00Z', NOW)).toEqual([
      { ac: '(runtime) the seat sends the reply', enforceBy: '2026-11-01', replayed: false },
    ])
  })

  it('leaves a deferral whose date is still ahead', () => {
    expect(overdueRows(DEFERRED_FUTURE, '2026-10-20T00:00:00Z', NOW)).toEqual([])
  })

  it('reads "not met" rows only in replay mode, due on the merge date', () => {
    expect(overdueRows(UNMET, '2026-11-06T20:21:02Z', NOW)).toEqual([])
    const replayed = overdueRows(UNMET, '2026-11-06T20:21:02Z', NOW, { replayUnmet: true })
    expect(replayed.map((r) => [r.enforceBy, r.replayed])).toEqual([
      ['2026-11-06', true],
      ['2026-11-06', true],
    ])
  })

  it('a later merged PR that references the number with a met row discharges it', () => {
    const prs = [
      { number: 10, body: DEFERRED_PAST, mergedAt: '2026-10-20T00:00:00Z' },
      { number: 20, body: MET_FOR_10, mergedAt: '2026-11-05T00:00:00Z' },
    ]
    expect(dischargedBy(prs[0], prs)).toBe(20)
    expect(findOverdue(prs, NOW)).toEqual({ findings: [], discharged: 1 })
  })

  it('a reference without a met row, an earlier PR, or #100 for #10 does not discharge', () => {
    const base = { number: 10, body: DEFERRED_PAST, mergedAt: '2026-10-20T00:00:00Z' }
    const cases = [
      { number: 21, body: 'Follows #10, nothing proven.', mergedAt: '2026-11-05T00:00:00Z' },
      { number: 5, body: MET_FOR_10, mergedAt: '2026-10-01T00:00:00Z' },
      {
        number: 22,
        body: MET_FOR_10.replace('#10', '#100'),
        mergedAt: '2026-11-05T00:00:00Z',
      },
    ]
    for (const c of cases) {
      expect(dischargedBy(base, [base, c])).toBeNull()
    }
    expect(findOverdue([base, ...cases], NOW).findings.map((f) => f.number)).toEqual([10])
  })

  it('replay: #3852-shaped unmet rows surface; a CI-only PR citing it with its own met row does not discharge them', () => {
    const prs = [
      { number: 3852, cls: 'client-lane', body: UNMET, mergedAt: '2026-11-06T20:21:02Z' },
      {
        number: 3909,
        cls: 'none',
        body: `Example: #3852.\n\n${MET_FOR_10}`,
        mergedAt: '2026-11-09T18:00:00Z',
      },
    ]
    expect(findOverdue(prs, NOW).findings).toEqual([])
    const { findings, discharged } = findOverdue(prs, NOW, { replayUnmet: true })
    expect(discharged).toBe(0)
    expect(findings.map((f) => [f.number, f.cls, f.rows.length])).toEqual([
      [3852, 'client-lane', 2],
    ])
    // A client-surface PR that cites it with a met row does discharge it.
    prs.push({
      number: 3950,
      cls: 'client-lane',
      body: `Switches on #3852.\n\n${MET_FOR_10}`,
      mergedAt: '2026-11-09T19:00:00Z',
    })
    expect(findOverdue(prs, NOW, { replayUnmet: true })).toEqual({ findings: [], discharged: 1 })
  })

  it('the report separates client-surface rows from process rows and never asks for the client label', () => {
    const text = formatReport(
      [
        {
          number: 10,
          mergedAt: '2026-10-20T00:00:00Z',
          cls: 'client-lane',
          rows: [{ ac: 'a', enforceBy: '2026-11-01', replayed: false }],
        },
        {
          number: 11,
          mergedAt: '2026-10-21T00:00:00Z',
          cls: 'none',
          rows: [{ ac: 'b', enforceBy: '2026-11-02', replayed: false }],
        },
      ],
      { scanned: 40, days: 120, discharged: 0 }
    )
    expect(text).toMatch(/2 row\(s\) on 2 PR\(s\), 1 of them on a client surface/)
    expect(text).toMatch(/never labelled `client`/)
    expect(text).toMatch(/^#10 \(merged 2026-10-20, client-lane\)$/m)
  })
})

describe('search windows stay under the cap', () => {
  it('covers 120 days in windows of at most 30 days with no gap', () => {
    const w = windows(new Date('2026-07-12T00:00:00Z'), NOW)
    expect(w[0][0]).toBe('2026-07-12')
    expect(w[w.length - 1][1]).toBe('2026-11-09')
    for (let i = 1; i < w.length; i++) {
      const prevEnd = new Date(`${w[i - 1][1]}T00:00:00Z`).getTime()
      expect(new Date(`${w[i][0]}T00:00:00Z`).getTime() - prevEnd).toBe(86_400_000)
    }
    for (const [a, b] of w) {
      const span =
        (new Date(`${b}T00:00:00Z`).getTime() - new Date(`${a}T00:00:00Z`).getTime()) / 86_400_000
      expect(span).toBeLessThanOrEqual(29)
    }
  })
})
