/**
 * Backlog census: the pure classifier.
 *
 * Why this exists (2026-08-24, the backlog-review session). A comprehensive
 * review of the 154 open issues was produced by hand and had to be retracted
 * twice. The raw counts were never the problem: 154 open, 192 filed in August,
 * 92 carrying acceptance criteria all reproduced exactly across two turns and
 * two independent instruments. Every retraction was an interpretive layer laid
 * on top of the counts without measurement:
 *
 *   1. A category asserted and never measured ("human-authored backlog").
 *   2. A population claim generalised from a single issue's comment thread.
 *   3. An instrument never tested against a known case (a regex that could not
 *      match the repo's own `ss#NNNN` convention).
 *   4. Arithmetic done by hand (87 where the answer was 86).
 *
 * None of those were ambiguity in the data. All four were narration running
 * ahead of measurement, which is a structural problem and gets a structural
 * fix: the numbers come from here, and a model may only interpret them.
 *
 * The venture's existing instrument shows why this matters. `crane_status`
 * renders "Triage Queue: Backlog is empty" because it reads `status:` labels,
 * and 135 of 154 open issues carry no `status:` label at all. A check that
 * reports "empty" over a 154-issue backlog has measured nothing (Law 12).
 * Hence the standing rule below.
 *
 * DESIGN RULES, each load-bearing:
 *
 *   - STATE IS NEVER READ FROM A `status:` LABEL. Labels record what an agent
 *     remembered to apply. Events and content record what happened.
 *   - `now` IS A PARAMETER. Never `Date.now()` inside. A classifier that reads
 *     the wall clock cannot be pinned by a fixture, and a fixture that cannot
 *     pin it cannot fail.
 *   - PURE. No network, no filesystem, no environment. Fetching lives in
 *     scripts/backlog-census.ts so that this half runs offline in CI.
 *   - VERDICTS ARE ORDERED RULES, FLAGS ARE INDEPENDENT. The verdict says what
 *     to do; the flags preserve everything the verdict discarded.
 *
 * @see tests/backlog-classify.test.ts - the fixture self-test, incl. falsifiers
 * @see docs/doctrine/agent-operating-doctrine.md - Law 12
 */

/** One open issue, as fetched. Every field is observed, none is inferred. */
export interface IssueRecord {
  number: number
  title: string
  /** Login of the account that filed it. Bots are identified by suffix, below. */
  authorLogin: string
  createdAt: string
  updatedAt: string
  body: string
  labels: string[]
  commentCount: number
  /** Distinct actor logins from ReopenedEvent, in timeline order. */
  reopenedBy: string[]
  everClosed: boolean
  /** PR numbers whose `closingIssuesReferences` name this issue. */
  linkedByMergedPr: number[]
  /** Merged commit SHAs whose subject names this issue. */
  namingCommits: string[]
}

export interface Snapshot {
  /** When the fetch ran. Staleness is judged against this, not against a re-fetch. */
  fetchedAt: string
  repo: string
  issues: IssueRecord[]
}

export type Verdict =
  | 'autofile-duplicate'
  | 'gate-held'
  | 'close-acs-met'
  | 'tick-blocked'
  | 'commits-unticked'
  | 'never-worked'
  | 'needs-probe'

export interface CensusRow {
  number: number
  title: string
  verdict: Verdict
  /** The rule that fired, quoted, so a row can be argued with. */
  because: string
  flags: string[]
  authorClass: 'bot' | 'agent'
  ageDays: number
  idleDays: number
  commentCount: number
  acsTotal: number
  acsChecked: number
  acsUnchecked: number
  linkedByMergedPr: number[]
  namingCommits: number
  reopenedByBot: boolean
  /** Only set for bot-filed issues. Null elsewhere: see normaliseBotTitle. */
  autofileFingerprint: string | null
  /** Lowest-numbered open issue sharing this fingerprint, when not itself. */
  duplicateOf: number | null
}

export interface Census {
  fetchedAt: string
  repo: string
  /** Every count in the report is `n of totalOpen`. Denominators are not optional. */
  totalOpen: number
  rows: CensusRow[]
  verdictCounts: Record<Verdict, number>
  flagCounts: Record<string, number>
}

/** Issues filed by a workflow, not by an agent session. */
const BOT_SUFFIX = '[bot]'
const BOT_LOGINS = new Set([
  'github-actions',
  'github-actions[bot]',
  'dependabot',
  'dependabot[bot]',
])

/**
 * Filed-this-long-ago is the `never-worked` threshold. Measured from
 * `createdAt`, NOT from `updatedAt`.
 *
 * Why (found on the live backlog, 2026-08-24): 18 open issues were older than
 * 30 days while only 2 had been idle 30 days. GitHub bumps `updatedAt` when
 * another issue or PR cross-references the issue, and on label edits. Keyed on
 * idleness the rule answers "has anything anywhere mentioned this", which is
 * not the question. `idleDays` is still reported as a column, because it is
 * real data; it is simply not evidence of work.
 */
const STALE_DAYS = 30

const AC_LINE = /^[ \t]*[-*] \[([ xX])\][ \t]/
const ISO_DATE = /\b\d{4}-\d{2}-\d{2}\b/g
const VERSION_TOKEN = /\bv?\d+\.\d+(?:\.\d+)*\b/g

export function isBot(login: string): boolean {
  return BOT_LOGINS.has(login) || login.endsWith(BOT_SUFFIX)
}

/**
 * Count acceptance-criteria checkboxes in an issue body.
 *
 * Deliberately markdown-literal: only a list item whose marker is a checkbox
 * counts. A prose sentence claiming an AC is met is not an AC, and a table row
 * saying "met" is the PR's claim about itself, not the issue's state.
 */
export function countAcs(body: string): { total: number; checked: number; unchecked: number } {
  let checked = 0
  let unchecked = 0
  for (const line of body.split('\n')) {
    const m = AC_LINE.exec(line)
    if (!m) continue
    if (m[1] === ' ') unchecked += 1
    else checked += 1
  }
  return { total: checked + unchecked, checked, unchecked }
}

/**
 * Fingerprint a workflow-filed title so the same finding filed daily collapses.
 *
 * Applied to BOT-FILED ISSUES ONLY, and that restriction is the safety margin.
 * Workflow titles are template plus a variable (`... 2026-08-24`,
 * `Hermes upstream v2026.8.19 available ...`), so stripping dates and version
 * tokens is lossless for them. The same normalisation over agent-written
 * titles would merge issues that merely share a date, so it is not offered.
 */
export function normaliseBotTitle(title: string): string {
  return title
    .replace(ISO_DATE, '')
    .replace(VERSION_TOKEN, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

function daysBetween(fromIso: string, toIso: string): number {
  const ms = Date.parse(toIso) - Date.parse(fromIso)
  return Math.floor(ms / 86_400_000)
}

/**
 * The clock a census is classified against.
 *
 * `now` being a parameter is worth nothing if the caller always hands it the
 * wall clock, and until 2026-08-31 that is exactly what the CLI did. Measured
 * on the committed 2026-08-24 snapshot, reclassified seven days later with the
 * identical classifier and only `now` moved:
 *
 *     now=2026-08-24  ->  never-worked  9, needs-probe 94
 *     now=2026-08-31  ->  never-worked 18, needs-probe 85
 *
 * Nine issues crossed STALE_DAYS while nobody touched them, so a run-to-run
 * diff would have reported the calendar as backlog movement. The header of
 * scripts/backlog-census.ts promised `--from` returned "byte-identical output",
 * and nothing ever diffed two runs to find out otherwise: a claim that cannot
 * fail (Law 12), sitting inside the tool built to stop exactly that.
 *
 * So a RECLASSIFIED census defaults to the snapshot's own `fetchedAt`, which
 * reproduces the original run because a live run classifies at the instant it
 * fetched. `explicit` (the CLI's `--now`) overrides for deliberate what-if
 * questions such as "which rows go stale next week", and must be stated, never
 * drifted into.
 */
export function censusClock(
  snapshot: Snapshot,
  opts: { reclassified: boolean; explicit: string | null; wallClock: string }
): string {
  if (opts.explicit !== null) {
    if (Number.isNaN(Date.parse(opts.explicit))) {
      throw new Error(`--now is not a parsable timestamp: ${opts.explicit}`)
    }
    return opts.explicit
  }
  return opts.reclassified ? snapshot.fetchedAt : opts.wallClock
}

function buildFlags(
  issue: IssueRecord,
  acs: ReturnType<typeof countAcs>,
  idleDays: number
): string[] {
  const flags: string[] = []
  if (isBot(issue.authorLogin)) flags.push('autofiled')
  if (acs.total > 0) flags.push('has-acs')
  if (acs.total > 0 && acs.unchecked === 0) flags.push('all-acs-met')
  if (issue.everClosed) flags.push('ever-closed')
  if (issue.reopenedBy.some(isBot)) flags.push('bot-reopened')
  if (issue.reopenedBy.some((a) => !isBot(a))) flags.push('human-reopened')
  if (issue.linkedByMergedPr.length > 0) flags.push('pr-linked')
  if (issue.namingCommits.length > 0) flags.push('has-merged-commits')
  if (issue.commentCount === 0) flags.push('zero-comments')
  if (idleDays > STALE_DAYS) flags.push(`stale-${STALE_DAYS}d`)
  return flags
}

/**
 * The verdict rules, in priority order. First match wins, and the order is the
 * spec: a change here changes the answer, so it is pinned by test.
 *
 * Ordering rationale:
 *   1. autofile-duplicate outranks everything. It needs no judgment at all,
 *      BUT ONLY where nobody has touched the re-file (see below).
 *   2. gate-held outranks close-acs-met. If a bot reopened the issue, closing
 *      it again just trips the same gate; satisfying the gate is the act.
 *   3. close-acs-met outranks tick-blocked. Nothing left unticked means there
 *      is nothing for the tick to block.
 *   4. never-worked is last before the residue: a stale issue with merged
 *      commits or a linked PR has more specific evidence, already matched.
 */
function decide(row: Omit<CensusRow, 'verdict' | 'because'>): {
  verdict: Verdict
  because: string
} {
  // A re-file someone WORKED is not a bare duplicate. Caught on the live
  // backlog before anything was closed (2026-08-24): three workflow re-files
  // carried threads this rule could not see -- one with 5 merged commits (the
  // Hermes v0.20 promotion), one with an analysis carrying a verify ID, and one
  // whose comment recorded a prior session's decision to keep it paired with
  // another issue so the two close together. Duplicate-closing any of them
  // would have destroyed the thread while reporting a tidy backlog.
  const untouched = row.namingCommits === 0 && row.commentCount === 0
  if (row.duplicateOf !== null && untouched) {
    return {
      verdict: 'autofile-duplicate',
      because: `workflow-filed, untouched; same normalised title as open #${row.duplicateOf}`,
    }
  }
  if (row.reopenedByBot) {
    return {
      verdict: 'gate-held',
      because: 'reopened by a CI gate; the gate is what holds it open',
    }
  }
  if (row.acsTotal > 0 && row.acsUnchecked === 0) {
    return { verdict: 'close-acs-met', because: `all ${row.acsTotal} acceptance criteria ticked` }
  }
  if (row.linkedByMergedPr.length > 0 && row.acsUnchecked > 0) {
    return {
      verdict: 'tick-blocked',
      because: `merged PR #${row.linkedByMergedPr[0]} links it, ${row.acsUnchecked} AC(s) still unticked`,
    }
  }
  if (row.namingCommits > 0 && row.acsUnchecked > 0) {
    return {
      verdict: 'commits-unticked',
      because: `${row.namingCommits} merged commit(s) name it, ${row.acsUnchecked} AC(s) still unticked`,
    }
  }
  if (row.commentCount === 0 && row.namingCommits === 0 && row.ageDays > STALE_DAYS) {
    return {
      verdict: 'never-worked',
      because: `no comments, no merged commit names it, filed ${row.ageDays}d ago`,
    }
  }
  return { verdict: 'needs-probe', because: 'no rule decides this; it needs a probe or a judgment' }
}

/**
 * Classify a snapshot. Pure: same (snapshot, now) always yields the same
 * census, which is what makes the fixture test in tests/ able to fail.
 */
export function classify(snapshot: Snapshot, now: string): Census {
  const botFingerprints = new Map<string, number>()
  for (const issue of snapshot.issues) {
    if (!isBot(issue.authorLogin)) continue
    const fp = normaliseBotTitle(issue.title)
    const seen = botFingerprints.get(fp)
    if (seen === undefined || issue.number < seen) botFingerprints.set(fp, issue.number)
  }

  const rows: CensusRow[] = snapshot.issues.map((issue) => {
    const acs = countAcs(issue.body)
    const ageDays = daysBetween(issue.createdAt, now)
    const idleDays = daysBetween(issue.updatedAt, now)
    const bot = isBot(issue.authorLogin)
    const fingerprint = bot ? normaliseBotTitle(issue.title) : null
    const oldest = fingerprint === null ? undefined : botFingerprints.get(fingerprint)
    const duplicateOf = oldest !== undefined && oldest !== issue.number ? oldest : null

    const partial: Omit<CensusRow, 'verdict' | 'because'> = {
      number: issue.number,
      title: issue.title,
      flags: buildFlags(issue, acs, idleDays),
      authorClass: bot ? 'bot' : 'agent',
      ageDays,
      idleDays,
      commentCount: issue.commentCount,
      acsTotal: acs.total,
      acsChecked: acs.checked,
      acsUnchecked: acs.unchecked,
      linkedByMergedPr: issue.linkedByMergedPr,
      namingCommits: issue.namingCommits.length,
      reopenedByBot: issue.reopenedBy.some(isBot),
      autofileFingerprint: fingerprint,
      duplicateOf,
    }
    return { ...partial, ...decide(partial) }
  })

  const verdictCounts: Record<Verdict, number> = {
    'autofile-duplicate': 0,
    'gate-held': 0,
    'close-acs-met': 0,
    'tick-blocked': 0,
    'commits-unticked': 0,
    'never-worked': 0,
    'needs-probe': 0,
  }
  const flagCounts: Record<string, number> = {}
  for (const row of rows) {
    verdictCounts[row.verdict] += 1
    for (const flag of row.flags) flagCounts[flag] = (flagCounts[flag] ?? 0) + 1
  }

  return {
    fetchedAt: snapshot.fetchedAt,
    repo: snapshot.repo,
    totalOpen: rows.length,
    rows,
    verdictCounts,
    flagCounts,
  }
}

/**
 * Render the census as markdown.
 *
 * Every count prints `n of N`. That is not decoration: "4 false accusations vs
 * a delivered client doc" is the enterprise's standing lesson that sampled
 * absence is not evidence, and a bare count is an absence claim with the
 * denominator hidden.
 */
export function renderReport(census: Census, now: string): string {
  const N = census.totalOpen
  const pct = (n: number) => (N === 0 ? '0%' : `${Math.round((n / N) * 100)}%`)
  const lines: string[] = []

  lines.push(`# Backlog census: ${census.repo}`)
  lines.push('')
  lines.push(`Snapshot fetched: ${census.fetchedAt}`)
  lines.push(`Classified at: ${now}`)
  lines.push(`Open issues examined: **${N}**`)
  lines.push('')
  lines.push('Every number below is computed by `scripts/backlog/classify.ts` from the')
  lines.push('snapshot named above. No number here was produced by a model.')
  lines.push('')
  lines.push('## Verdicts')
  lines.push('')
  lines.push('| Verdict | n of N | share | meaning |')
  lines.push('| --- | --- | --- | --- |')
  for (const [verdict, meaning] of Object.entries(VERDICT_MEANING) as [Verdict, string][]) {
    const n = census.verdictCounts[verdict]
    lines.push(`| \`${verdict}\` | ${n} of ${N} | ${pct(n)} | ${meaning} |`)
  }
  lines.push('')
  lines.push('## Flags (independent; an issue can carry several)')
  lines.push('')
  lines.push('| Flag | n of N | share |')
  lines.push('| --- | --- | --- |')
  for (const flag of Object.keys(census.flagCounts).sort()) {
    const n = census.flagCounts[flag]
    lines.push(`| \`${flag}\` | ${n} of ${N} | ${pct(n)} |`)
  }
  lines.push('')
  lines.push('## Rows')
  lines.push('')
  lines.push('| # | verdict | because | ACs | comments | idle | flags |')
  lines.push('| --- | --- | --- | --- | --- | --- | --- |')
  for (const row of [...census.rows].sort(sortForReport)) {
    const acs = row.acsTotal === 0 ? 'none' : `${row.acsChecked}/${row.acsTotal}`
    lines.push(
      `| #${row.number} | \`${row.verdict}\` | ${row.because} | ${acs} | ${row.commentCount} | ${row.idleDays}d | ${row.flags.join(' ')} |`
    )
  }
  lines.push('')
  lines.push('## What this census does NOT decide')
  lines.push('')
  lines.push('- **Is the defect still real?** Not decidable from issue metadata. Rows land')
  lines.push('  in `needs-probe`; they are never guessed.')
  lines.push('- **Does it matter to the business or to a client?** Captain call.')
  lines.push('- **Closing anything.** This tool is read-only and closes nothing.')
  return lines.join('\n')
}

const VERDICT_MEANING: Record<Verdict, string> = {
  'autofile-duplicate': 'workflow re-filed a finding already open under a lower number',
  'gate-held': 'a CI gate reopened it; satisfy the gate, do not just close it',
  'close-acs-met': 'every acceptance criterion is ticked and nothing holds it open',
  'tick-blocked': 'a merged PR links it but acceptance criteria are still unticked',
  'commits-unticked': 'merged commits name it but criteria are unticked: reconcile, do not assume',
  'never-worked': 'filed and never revisited: no comments, no merged commit, filed long ago',
  'needs-probe': 'no rule decides this; needs a probe or a judgment, with a citation',
}

const VERDICT_ORDER: Verdict[] = [
  'close-acs-met',
  'autofile-duplicate',
  'gate-held',
  'tick-blocked',
  'commits-unticked',
  'never-worked',
  'needs-probe',
]

function sortForReport(a: CensusRow, b: CensusRow): number {
  const byVerdict = VERDICT_ORDER.indexOf(a.verdict) - VERDICT_ORDER.indexOf(b.verdict)
  return byVerdict !== 0 ? byVerdict : a.number - b.number
}
