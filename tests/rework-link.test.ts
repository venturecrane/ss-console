/**
 * Fixture self-test for the rework measure (scripts/rework/link.ts, report.ts).
 *
 * Each link type is asserted on a case that must link, and again on the same
 * case with the deciding field perturbed, which must stop linking. A fix that
 * links to nothing is a first-class case: it must land in the residue, never
 * vanish from the denominator. A measure that cannot report "unlinked" would
 * read every fix as rework, which is the construct the retrospective's review
 * rejected.
 */

import { describe, it, expect } from 'vitest'
import {
  kindOf,
  linkFixes,
  referencedNumbers,
  SHARED_FILE_PRS,
  WEAK_WINDOW_DAYS,
  type PrRecord,
} from '../scripts/rework/link'
import { measure, renderReport } from '../scripts/rework/report'

function pr(over: Partial<PrRecord> & { number: number }): PrRecord {
  return {
    title: `chore: pr ${over.number}`,
    headRefName: 'chore/x',
    body: '',
    createdAt: '2026-09-01T00:00:00Z',
    mergedAt: '2026-09-02T00:00:00Z',
    files: [],
    commitMessages: [],
    closesIssues: [],
    blamed: null,
    ...over,
  }
}

const FEAT_A = pr({
  number: 100,
  title: 'feat(lane): add the lane',
  mergedAt: '2026-09-05T00:00:00Z',
  files: ['src/lane.ts', 'src/lane-util.ts'],
  commitMessages: ['feat: add the lane\n', 'fix: typo caught in review\n'],
})
const FEAT_B = pr({
  number: 200,
  title: 'feat(board): add the board',
  mergedAt: '2026-09-06T00:00:00Z',
  files: ['src/board.ts'],
  closesIssues: [1050],
})
const CHORE = pr({ number: 300, mergedAt: '2026-09-01T00:00:00Z', files: ['src/old.ts'] })

const FIX_EXPLICIT = pr({
  number: 401,
  title: 'fix(lane): stop the double send',
  mergedAt: '2026-09-07T00:00:00Z',
  body: 'Regression from #100: the retry resent.',
  files: ['src/other.ts'],
  blamed: {},
})
const FIX_BLAME = pr({
  number: 402,
  title: 'fix(board): count rows once',
  mergedAt: '2026-09-08T00:00:00Z',
  files: ['src/board.ts'],
  blamed: { '200': 10, '300': 2 },
})
const FIX_WEAK = pr({
  number: 403,
  title: 'fix: tidy the lane helper',
  mergedAt: '2026-09-10T00:00:00Z',
  files: ['src/lane-util.ts'],
  blamed: {},
})
const FIX_NOTHING = pr({
  number: 404,
  title: 'fix(ci): pin the runner',
  mergedAt: '2026-09-11T00:00:00Z',
  files: ['.github/workflows/ci.yml'],
  blamed: { '300': 4 },
})
const FIX_CHAIN = pr({
  number: 405,
  title: 'fix(lane): the double-send fix missed one path',
  mergedAt: '2026-09-12T00:00:00Z',
  body: 'Follow-up to #401.',
  blamed: {},
})
const FIX_ISSUE = pr({
  number: 406,
  title: 'fix(board): empty board renders',
  mergedAt: '2026-09-13T00:00:00Z',
  body: 'Reopens the gap in #1050.',
  blamed: {},
})

const ALL = [
  FEAT_A,
  FEAT_B,
  CHORE,
  FIX_EXPLICIT,
  FIX_BLAME,
  FIX_WEAK,
  FIX_NOTHING,
  FIX_CHAIN,
  FIX_ISSUE,
]

function linkOf(prs: PrRecord[], fix: number) {
  const link = linkFixes(prs).links.find((l) => l.fix === fix)
  if (!link) throw new Error(`no link row for #${fix}`)
  return link
}

describe('kindOf', () => {
  it('reads the conventional title prefix, then the branch name', () => {
    expect(kindOf({ title: 'feat(x): y', headRefName: 'chore/z' })).toBe('feat')
    expect(kindOf({ title: 'hotfix: y', headRefName: 'feat/z' })).toBe('fix')
    expect(kindOf({ title: 'Set up the library', headRefName: 'feat/library' })).toBe('feat')
    expect(kindOf({ title: 'Set up the library', headRefName: 'worktree-agent-1' })).toBe('other')
  })
})

describe('referencedNumbers', () => {
  it('ignores the squash suffix and the PR itself, reads ss#NNNN', () => {
    const p = pr({ number: 500, title: 'fix: x (#500)', body: 'see #123 and ss#4567, url/#999' })
    expect(referencedNumbers(p)).toEqual([123, 4567])
  })
})

describe('link types', () => {
  it('explicit: a #ref to a feature links at explicit', () => {
    expect(linkOf(ALL, 401)).toEqual({ fix: 401, strength: 'explicit', features: [100] })
  })
  it('explicit falsifier: the same fix without the ref does not link explicitly', () => {
    const prs = ALL.map((p) => (p.number === 401 ? { ...p, body: 'The retry resent.' } : p))
    expect(linkOf(prs, 401).strength).toBeNull()
  })

  it('blame: the top blamed PR that is a feature links at blame', () => {
    expect(linkOf(ALL, 402)).toEqual({ fix: 402, strength: 'blame', features: [200] })
  })
  it('blame falsifier: lines blamed only to a chore link to no feature by blame', () => {
    const prs = ALL.map((p) =>
      p.number === 402 ? { ...p, blamed: { '300': 10 }, files: ['src/old.ts'] } : p
    )
    expect(linkOf(prs, 402).strength).toBeNull()
  })

  it('weak: same-file overlap inside the window links at weak', () => {
    expect(linkOf(ALL, 403)).toEqual({ fix: 403, strength: 'weak', features: [100] })
  })
  it('weak falsifier: the same overlap outside the window does not link', () => {
    const late = new Date(Date.parse(FEAT_A.mergedAt) + (WEAK_WINDOW_DAYS + 1) * 86_400_000)
    const prs = ALL.map((p) => (p.number === 403 ? { ...p, mergedAt: late.toISOString() } : p))
    expect(linkOf(prs, 403).strength).toBeNull()
  })
  it('weak falsifier: overlap only on a shared file does not link', () => {
    const noise = Array.from({ length: SHARED_FILE_PRS + 1 }, (_, i) =>
      pr({ number: 900 + i, files: ['src/lane-util.ts'] })
    )
    expect(linkOf([...ALL, ...noise], 403).strength).toBeNull()
  })

  it('structural: lines blamed to the overlay fold never make the fold the repaired feature', () => {
    const fold = pr({
      number: 3411,
      title: 'feat(operator): one repository',
      mergedAt: '2026-09-03T00:00:00Z',
    })
    const fix = pr({
      number: 409,
      title: 'fix: x',
      mergedAt: '2026-09-09T00:00:00Z',
      blamed: { '3411': 40, '200': 5 },
    })
    expect(linkOf([...ALL, fold, fix], 409)).toEqual({
      fix: 409,
      strength: 'blame',
      features: [200],
    })
    const plain = pr({ ...fold, number: 3412 })
    const fix2 = { ...fix, number: 410, blamed: { '3412': 40, '200': 5 } }
    expect(linkOf([...ALL, plain, fix2], 410).features).toEqual([3412])
  })

  it('unlinked: a fix with no ref, no feature blame and no overlap links to nothing', () => {
    expect(linkOf(ALL, 404)).toEqual({ fix: 404, strength: null, features: [] })
  })

  it('chain: a ref to an earlier fix inherits that fix’s feature', () => {
    expect(linkOf(ALL, 405)).toEqual({ fix: 405, strength: 'explicit', features: [100] })
  })
  it('chain is only as strong as its weakest link', () => {
    const prs = [
      ...ALL,
      pr({
        number: 407,
        title: 'fix: again',
        mergedAt: '2026-09-14T00:00:00Z',
        body: 'after #403',
      }),
    ]
    expect(linkOf(prs, 407)).toEqual({ fix: 407, strength: 'weak', features: [100] })
  })

  it('issue: a ref to an issue a feature closed links to that feature', () => {
    expect(linkOf(ALL, 406)).toEqual({ fix: 406, strength: 'explicit', features: [200] })
  })
})

describe('measure', () => {
  const snapshot = { fetchedAt: '2026-09-20T00:00:00Z', repo: 'x/y', since: '2026-09-01', prs: ALL }

  it('counts fixes per feature by strength, lag, and pre-merge', () => {
    const m = measure(snapshot, null)
    const a = m.rows.find((r) => r.number === 100)
    expect(a?.post).toEqual({ explicit: 2, blame: 0, weak: 1 })
    expect(a?.lagDaysStrong).toBe(2)
    expect(a?.pre).toEqual({ fixPrs: 0, fixCommits: 1 })
    const b = m.rows.find((r) => r.number === 200)
    expect(b?.post).toEqual({ explicit: 1, blame: 1, weak: 0 })
  })

  it('a fix merged before its feature counts as caught pre-merge, not as rework', () => {
    const early = pr({
      number: 408,
      title: 'fix: unblock',
      mergedAt: '2026-09-04T00:00:00Z',
      body: 'for #200',
    })
    const m = measure({ ...snapshot, prs: [...ALL, early] }, null)
    const b = m.rows.find((r) => r.number === 200)
    expect(b?.pre.fixPrs).toBe(1)
    expect(b?.post).toEqual({ explicit: 1, blame: 1, weak: 0 })
  })

  it('keeps the unlinked fix in the residue and the denominator', () => {
    const m = measure(snapshot, null)
    expect(m.residue).toMatchObject({ fixes: 6, explicit: 3, blame: 1, weak: 1, unlinked: 1 })
    const month = m.byMonth.find((g) => g.key === '2026-09')
    expect(month).toMatchObject({ features: 2, withStrongFix: 2, strongFixes: 4, allFixes: 5 })
    expect(renderReport(m)).toContain('unlinked (residue): 1/6')
  })

  it('splits by session type when a map is given, unmapped otherwise', () => {
    const m = measure(snapshot, new Map([[100, 'auto-build']]))
    expect(m.bySession?.map((g) => [g.key, g.features])).toEqual([
      ['auto-build', 1],
      ['unmapped', 1],
    ])
    expect(measure(snapshot, null).bySession).toBeNull()
  })
})
