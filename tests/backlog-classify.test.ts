/**
 * Fixture self-test for the backlog census classifier.
 *
 * Why this exists. The hand-built backlog review this classifier replaces was
 * retracted twice, and one of the four errors was an instrument that had never
 * been run against a case with a known answer: a regex that could not, by
 * construction, match the repo's own `ss#NNNN` convention. It reported zero and
 * zero was believed.
 *
 * So every rule below is asserted twice: once on a case that must match, and
 * once on the SAME case with the deciding field perturbed, which must stop
 * matching. A test that only ever asserts the passing direction cannot tell a
 * working rule from a rule that returns the expected answer for every input.
 * That is the enterprise's standing lesson (a check that cannot fail has
 * measured nothing) applied to the checker itself.
 *
 * The classifier is pure, so this file needs no network and no fixtures on
 * disk. It runs in `npm run verify` and therefore in CI, which is the point:
 * the census's own correctness is gated on every merge, not audited annually.
 *
 * @see scripts/backlog/classify.ts
 */

import { describe, it, expect } from 'vitest'
import {
  censusClock,
  classify,
  countAcs,
  isBot,
  normaliseBotTitle,
  renderReport,
  type IssueRecord,
  type Snapshot,
  type Verdict,
} from '../scripts/backlog/classify'

const NOW = '2026-08-24T12:00:00Z'

/** A deliberately boring issue: matches no rule, lands in `needs-probe`. */
function issue(over: Partial<IssueRecord> = {}): IssueRecord {
  return {
    number: 1000,
    title: 'a plain issue',
    authorLogin: 'SMDurgan',
    createdAt: '2026-08-23T12:00:00Z',
    updatedAt: '2026-08-23T12:00:00Z',
    body: 'no acceptance criteria here',
    labels: [],
    commentCount: 3,
    reopenedBy: [],
    everClosed: false,
    linkedByMergedPr: [],
    namingCommits: [],
    ...over,
  }
}

function snap(issues: IssueRecord[]): Snapshot {
  return { fetchedAt: NOW, repo: 'venturecrane/ss-console', issues }
}

function verdictOf(issues: IssueRecord[], number: number): Verdict {
  const row = classify(snap(issues), NOW).rows.find((r) => r.number === number)
  if (!row) throw new Error(`no row for #${number}`)
  return row.verdict
}

describe('countAcs', () => {
  it('counts checked and unchecked markdown checkboxes', () => {
    const body = [
      '- [x] done one',
      '- [ ] not done',
      '* [X] done two',
      'prose - [ ] not a list item',
    ].join('\n')
    expect(countAcs(body)).toEqual({ total: 3, checked: 2, unchecked: 1 })
  })

  it('does NOT count a PR-style table row claiming "met"', () => {
    // The PR's claim about itself is not the issue's state. This is exactly the
    // gap that let merged work sit behind unticked ACs.
    expect(countAcs('| (repo) something | met | evidence |').total).toBe(0)
  })

  it('reports zero for a body with no criteria', () => {
    expect(countAcs('just prose')).toEqual({ total: 0, checked: 0, unchecked: 0 })
  })
})

describe('isBot', () => {
  it('recognises workflow authors', () => {
    expect(isBot('github-actions')).toBe(true)
    expect(isBot('github-actions[bot]')).toBe(true)
  })

  it('does not mistake an agent session for a bot', () => {
    expect(isBot('SMDurgan')).toBe(false)
  })
})

describe('normaliseBotTitle', () => {
  it('collapses a date-stamped workflow title', () => {
    expect(normaliseBotTitle('Routine run with no terminal state 2026-08-24')).toBe(
      normaliseBotTitle('Routine run with no terminal state 2026-08-18')
    )
  })

  it('collapses a version-stamped workflow title', () => {
    expect(
      normaliseBotTitle('Hermes upstream v2026.8.19 available (fleet blessed at v2026.7.1)')
    ).toBe(normaliseBotTitle('Hermes upstream v2026.8.3 available (fleet blessed at v2026.7.1)'))
  })

  it('keeps genuinely different findings apart', () => {
    expect(normaliseBotTitle('Routine run with no terminal state 2026-08-24')).not.toBe(
      normaliseBotTitle('Unaudited send detected 2026-08-24')
    )
  })
})

// Each block asserts the rule fires, then perturbs ONLY the deciding field and
// asserts it stops firing. The second half is what makes the first half mean
// something.
describe('verdict rules, each with its falsifier', () => {
  it('autofile-duplicate: a bot re-filing the same finding', () => {
    // Both bare: nobody commented, no commit names either. That is the only
    // shape this rule may fire on.
    const older = issue({
      number: 2401,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-18',
      commentCount: 0,
    })
    const newer = issue({
      number: 2567,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-24',
      commentCount: 0,
    })
    expect(verdictOf([older, newer], 2567)).toBe('autofile-duplicate')
    // The oldest copy is the one finding, not a duplicate of itself.
    expect(verdictOf([older, newer], 2401)).not.toBe('autofile-duplicate')
  })

  it('a re-file that someone WORKED is not a bare duplicate', () => {
    // Found while acting on the census, 2026-08-24, before anything was closed.
    // Three workflow re-files carried work the rule could not see: one had 5
    // merged commits (the Hermes v0.20 promotion), one carried a live analysis
    // with a verify ID, and one carried a comment from a prior session saying it
    // was deliberately paired with another issue and they close together.
    // Marking any of them `autofile-duplicate` would have erased a live thread.
    const canonical = issue({
      number: 2225,
      authorLogin: 'github-actions',
      title: 'Hermes upstream v2026.8.3 available',
      commentCount: 0,
    })
    const worked = issue({
      number: 2444,
      authorLogin: 'github-actions',
      title: 'Hermes upstream v2026.8.18 available',
      commentCount: 0,
      namingCommits: ['a1', 'b2', 'c3', 'd4', 'e5'],
    })
    expect(verdictOf([canonical, worked], 2444)).not.toBe('autofile-duplicate')
  })

  it('a re-file someone COMMENTED on is not a bare duplicate either', () => {
    const canonical = issue({
      number: 2401,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-18',
      commentCount: 0,
    })
    const discussed = issue({
      number: 2567,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-24',
      commentCount: 1,
    })
    expect(verdictOf([canonical, discussed], 2567)).not.toBe('autofile-duplicate')
  })

  it('falsifier: with no work signal, the same re-file IS a duplicate', () => {
    // Without this the rule above could be satisfied by never firing at all.
    const canonical = issue({
      number: 2401,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-18',
      commentCount: 0,
    })
    const bare = issue({
      number: 2567,
      authorLogin: 'github-actions',
      title: 'Routine run with no terminal state 2026-08-24',
      commentCount: 0,
      namingCommits: [],
    })
    expect(verdictOf([canonical, bare], 2567)).toBe('autofile-duplicate')
  })

  it('falsifier: an agent-authored title is never collapsed, even if identical', () => {
    const a = issue({ number: 10, authorLogin: 'SMDurgan', title: 'same title 2026-08-18' })
    const b = issue({ number: 11, authorLogin: 'SMDurgan', title: 'same title 2026-08-24' })
    expect(verdictOf([a, b], 11)).not.toBe('autofile-duplicate')
  })

  it('falsifier: an agent-authored row carries NO fingerprint at all', () => {
    // Found by mutation testing, 2026-08-24. Moving the bot check off the
    // fingerprint assignment left every verdict identical (the lookup map is
    // still bot-only), so the verdict assertions above all stayed green while
    // the census reported a fingerprint on rows the doc comment says are null.
    // A surviving mutant is a hole in the suite, not a pass.
    const rows = classify(
      snap([
        issue({ number: 10, authorLogin: 'SMDurgan' }),
        issue({ number: 11, authorLogin: 'github-actions' }),
      ]),
      NOW
    ).rows
    expect(rows.find((r) => r.number === 10)?.autofileFingerprint).toBeNull()
    expect(rows.find((r) => r.number === 11)?.autofileFingerprint).not.toBeNull()
  })

  it('gate-held: a CI gate reopened it', () => {
    const held = issue({ number: 2490, reopenedBy: ['github-actions'], everClosed: true })
    expect(verdictOf([held], 2490)).toBe('gate-held')
  })

  it('falsifier: a human reopen is not a gate hold', () => {
    const held = issue({ number: 2490, reopenedBy: ['SMDurgan'], everClosed: true })
    expect(verdictOf([held], 2490)).not.toBe('gate-held')
  })

  it('close-acs-met: every criterion ticked', () => {
    const done = issue({ number: 2511, body: '- [x] one\n- [x] two' })
    expect(verdictOf([done], 2511)).toBe('close-acs-met')
  })

  it('falsifier: one unticked criterion is enough to withhold the verdict', () => {
    const nearly = issue({ number: 2511, body: '- [x] one\n- [ ] two' })
    expect(verdictOf([nearly], 2511)).not.toBe('close-acs-met')
  })

  it('falsifier: an issue with NO criteria is not "all criteria met"', () => {
    // Vacuous truth is the classic way this rule goes wrong: zero of zero
    // unticked would close every issue in the repo.
    expect(verdictOf([issue({ number: 7, body: 'prose only' })], 7)).not.toBe('close-acs-met')
  })

  it('tick-blocked: a merged PR links it, criteria still unticked', () => {
    const blocked = issue({ number: 2490, linkedByMergedPr: [2493], body: '- [ ] one' })
    expect(verdictOf([blocked], 2490)).toBe('tick-blocked')
  })

  it('falsifier: no linked PR means nothing was blocked', () => {
    const unlinked = issue({
      number: 2490,
      linkedByMergedPr: [],
      body: '- [ ] one',
      commentCount: 3,
    })
    expect(verdictOf([unlinked], 2490)).not.toBe('tick-blocked')
  })

  it('never-worked: no comments, no commits, long idle', () => {
    const cold = issue({
      number: 2081,
      commentCount: 0,
      namingCommits: [],
      createdAt: '2026-07-01T12:00:00Z',
      updatedAt: '2026-07-01T12:00:00Z',
    })
    expect(verdictOf([cold], 2081)).toBe('never-worked')
  })

  it('falsifier: one merged commit naming it disqualifies "never worked"', () => {
    const touched = issue({
      number: 2081,
      commentCount: 0,
      namingCommits: ['deadbee'],
      createdAt: '2026-07-01T12:00:00Z',
      updatedAt: '2026-07-01T12:00:00Z',
    })
    expect(verdictOf([touched], 2081)).not.toBe('never-worked')
  })

  it('falsifier: a recent untouched issue is not stale yet', () => {
    const fresh = issue({
      number: 2081,
      commentCount: 0,
      createdAt: '2026-08-22T12:00:00Z',
      updatedAt: '2026-08-22T12:00:00Z',
    })
    expect(verdictOf([fresh], 2081)).not.toBe('never-worked')
  })

  it('never-worked keys on AGE, not on `updatedAt` idleness', () => {
    // Found by running the census on the live backlog, 2026-08-24. 18 open
    // issues were older than 30 days but only 2 had been idle 30 days, because
    // GitHub bumps `updatedAt` on cross-references and label edits. Keying the
    // rule on idleness measured "has anything anywhere mentioned this", not
    // "has anyone worked it".
    const old = issue({
      number: 2081,
      createdAt: '2026-06-01T12:00:00Z',
      updatedAt: '2026-08-23T12:00:00Z', // bumped by a cross-reference, not by work
      commentCount: 0,
      namingCommits: [],
    })
    expect(verdictOf([old], 2081)).toBe('never-worked')
  })

  it('commits-unticked: merged work names it, criteria still unticked', () => {
    // The dominant decidable class in this repo. PR linkage is near-absent
    // (4 of 154 issues), so a commit subject naming the issue is the real
    // evidence that work landed.
    const landed = issue({
      number: 2448,
      namingCommits: ['a1', 'b2'],
      body: '- [ ] one\n- [x] two',
    })
    expect(verdictOf([landed], 2448)).toBe('commits-unticked')
  })

  it('falsifier: no naming commit means nothing landed to reconcile', () => {
    const nothing = issue({ number: 2448, namingCommits: [], body: '- [ ] one\n- [x] two' })
    expect(verdictOf([nothing], 2448)).not.toBe('commits-unticked')
  })

  it('falsifier: commits with every criterion ticked is close-acs-met, not unticked', () => {
    const done = issue({ number: 2448, namingCommits: ['a1'], body: '- [x] one' })
    expect(verdictOf([done], 2448)).toBe('close-acs-met')
  })

  it('needs-probe is the residue, not a silent default for everything', () => {
    expect(verdictOf([issue({ number: 999 })], 999)).toBe('needs-probe')
  })
})

describe('rule ORDER is part of the spec', () => {
  it('gate-held outranks close-acs-met: reclosing would only trip the gate again', () => {
    const both = issue({
      number: 2367,
      reopenedBy: ['github-actions'],
      everClosed: true,
      body: '- [x] one',
    })
    expect(verdictOf([both], 2367)).toBe('gate-held')
  })

  it('close-acs-met outranks tick-blocked: nothing unticked means nothing blocked', () => {
    const both = issue({ number: 2500, linkedByMergedPr: [2506], body: '- [x] one' })
    expect(verdictOf([both], 2500)).toBe('close-acs-met')
  })

  it('tick-blocked outranks commits-unticked: a real GitHub link is the stronger evidence', () => {
    const both = issue({
      number: 2490,
      linkedByMergedPr: [2493],
      namingCommits: ['a1'],
      body: '- [ ] one',
    })
    expect(verdictOf([both], 2490)).toBe('tick-blocked')
  })

  it('tick-blocked outranks never-worked: a linked PR is more specific than silence', () => {
    const both = issue({
      number: 2490,
      linkedByMergedPr: [2493],
      body: '- [ ] one',
      commentCount: 0,
      createdAt: '2026-07-01T12:00:00Z',
      updatedAt: '2026-07-01T12:00:00Z',
    })
    expect(verdictOf([both], 2490)).toBe('tick-blocked')
  })
})

describe('state is never read from a `status:` label', () => {
  // crane_status renders "Triage Queue: Backlog is empty" because it reads
  // `status:` labels, and 135 of 154 open issues carry none. Labels record what
  // an agent remembered; events and content record what happened.
  it('the same issue classifies identically with and without status labels', () => {
    const bare = issue({
      number: 42,
      body: '- [ ] one',
      commentCount: 0,
      namingCommits: [],
      createdAt: '2026-07-01T12:00:00Z',
      updatedAt: '2026-07-01T12:00:00Z',
    })
    const labelled = { ...bare, labels: ['status:ready', 'prio:P1', 'type:bug'] }
    expect(verdictOf([labelled], 42)).toBe(verdictOf([bare], 42))
  })
})

describe('determinism', () => {
  const corpus = [
    issue({ number: 1, authorLogin: 'github-actions', title: 'daily thing 2026-08-01' }),
    issue({ number: 2, authorLogin: 'github-actions', title: 'daily thing 2026-08-02' }),
    issue({ number: 3, body: '- [x] a\n- [x] b' }),
    issue({ number: 4, reopenedBy: ['github-actions'], everClosed: true }),
    issue({
      number: 5,
      commentCount: 0,
      createdAt: '2026-06-01T00:00:00Z',
      updatedAt: '2026-06-01T00:00:00Z',
    }),
  ]

  it('the same snapshot and the same `now` produce byte-identical output', () => {
    const a = JSON.stringify(classify(snap(corpus), NOW))
    const b = JSON.stringify(classify(snap(corpus), NOW))
    expect(a).toBe(b)
  })

  it('`now` is a parameter, not the wall clock: moving it moves the answer', () => {
    // If the classifier called Date.now() internally this would not change.
    const early = classify(snap(corpus), '2026-06-05T00:00:00Z')
    const late = classify(snap(corpus), '2026-08-24T00:00:00Z')
    expect(early.verdictCounts['never-worked']).not.toBe(late.verdictCounts['never-worked'])
  })

  it('verdict counts sum to the population', () => {
    const census = classify(snap(corpus), NOW)
    const summed = Object.values(census.verdictCounts).reduce((a, b) => a + b, 0)
    expect(summed).toBe(census.totalOpen)
    expect(census.totalOpen).toBe(corpus.length)
  })
})

// The classifier taking `now` as a parameter buys nothing if the caller always
// hands it the wall clock. It did until 2026-08-31, which made `--from` drift.
describe('censusClock: what a reclassified census is judged against', () => {
  const fetched = NOW // snap() stamps every fixture snapshot with this
  const laterWallClock = '2026-08-31T16:14:46.278Z'
  const snapshot = snap([issue()])
  const at = (over: Parameters<typeof censusClock>[1]) => censusClock(snapshot, over)

  it('a reclassified snapshot is judged at the clock it was FETCHED at', () => {
    expect(at({ reclassified: true, explicit: null, wallClock: laterWallClock })).toBe(fetched)
  })

  it('falsifier: a LIVE fetch is judged at the wall clock, not at some stored time', () => {
    // Without this, the rule above could be satisfied by always returning
    // fetchedAt, which on a live run is the same value and hides the bug.
    expect(at({ reclassified: false, explicit: null, wallClock: laterWallClock })).toBe(
      laterWallClock
    )
  })

  it('an explicit --now wins over both, for deliberate what-if questions', () => {
    const asked = '2026-12-01T00:00:00Z'
    expect(at({ reclassified: true, explicit: asked, wallClock: laterWallClock })).toBe(asked)
    expect(at({ reclassified: false, explicit: asked, wallClock: laterWallClock })).toBe(asked)
  })

  it('an unparsable --now fails loudly rather than silently becoming NaN days', () => {
    expect(() => at({ reclassified: true, explicit: 'last tuesday', wallClock: fetched })).toThrow(
      /not a parsable timestamp/
    )
  })

  it('regression: the same snapshot reclassified on two days gives the same census', () => {
    // The measured 2026-08-31 defect, in miniature. `cold` is 29 days old at
    // fetch time and 36 days old a week later, so an unpinned clock flips it
    // from `needs-probe` to `never-worked` while nobody touches the backlog.
    const cold = issue({
      number: 2081,
      commentCount: 0,
      namingCommits: [],
      createdAt: '2026-07-26T12:00:00Z',
      updatedAt: '2026-07-26T12:00:00Z',
    })
    const s = snap([cold])

    const day0 = classify(s, censusClock(s, { reclassified: true, explicit: null, wallClock: NOW }))
    const day7 = classify(
      s,
      censusClock(s, { reclassified: true, explicit: null, wallClock: laterWallClock })
    )
    expect(JSON.stringify(day7)).toBe(JSON.stringify(day0))

    // And the falsifier: the wall clock really would have moved this row, so
    // the assertion above is pinning something rather than restating a constant.
    expect(classify(s, NOW).verdictCounts['never-worked']).toBe(0)
    expect(classify(s, laterWallClock).verdictCounts['never-worked']).toBe(1)
  })
})

describe('the report states denominators', () => {
  it('every verdict count is rendered as `n of N`, never bare', () => {
    const census = classify(
      snap([issue({ number: 1 }), issue({ number: 2, body: '- [x] a' })]),
      NOW
    )
    const md = renderReport(census, NOW)
    expect(md).toContain('Open issues examined: **2**')
    expect(md).toMatch(/\| `close-acs-met` \| 1 of 2 \|/)
    expect(md).toMatch(/\| `needs-probe` \| 1 of 2 \|/)
  })

  it('the report declares what it does not decide', () => {
    const md = renderReport(classify(snap([issue()]), NOW), NOW)
    expect(md).toContain('What this census does NOT decide')
    expect(md).toContain('Is the defect still real?')
  })
})
