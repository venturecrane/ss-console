/**
 * Backlog census: the fetch half, and the CLI door.
 *
 * Split from scripts/backlog/classify.ts on purpose. This half talks to GitHub
 * and to git, so it can only run with credentials and a network. The classifier
 * is pure, so its correctness is gated by tests/backlog-classify.test.ts on
 * every merge. If the two halves lived in one file, the rules could only ever be
 * audited by hand against live data, which is the situation this replaces.
 *
 * Usage:
 *   npx tsx scripts/backlog-census.ts                      # fetch, classify, print
 *   npx tsx scripts/backlog-census.ts --json               # census as JSON
 *   npx tsx scripts/backlog-census.ts --snapshot out.json  # keep the raw snapshot
 *   npx tsx scripts/backlog-census.ts --from snap.json     # reclassify, no network
 *   npx tsx scripts/backlog-census.ts --from s.json --now 2026-09-30T00:00:00Z
 *
 * `--from` is the reproducibility door: hand someone the snapshot and they get
 * the same census back, which is what makes it arguable rather than merely
 * asserted. That only holds because `--from` classifies at the snapshot's own
 * `fetchedAt` rather than at today's wall clock -- see censusClock() in
 * scripts/backlog/classify.ts for the seven-day measurement that says why, and
 * pass `--now` when you deliberately want a different clock.
 *
 * @see scripts/backlog/classify.ts - the rules, and why each exists
 */

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import {
  censusClock,
  classify,
  renderReport,
  type IssueRecord,
  type Snapshot,
} from './backlog/classify'

const REPO = process.env.BACKLOG_REPO ?? 'venturecrane/ss-console'
/** How far back to read merged PRs and commits. Beyond this, links are assumed cold. */
const HISTORY_DAYS = 120

function gh(args: string[]): string {
  return execFileSync('gh', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
}

/**
 * Parse, never cast. A shape change at GitHub must fail here with a named
 * field, not surface later as a silently-empty column that reads as "clean".
 */
function expectArray(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`expected an array for ${what}, got ${typeof value}`)
  return value
}

function expectObject(value: unknown, what: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`expected an object for ${what}, got ${typeof value}`)
  }
  return value as Record<string, unknown>
}

function str(value: unknown, what: string): string {
  if (typeof value !== 'string') throw new Error(`expected a string for ${what}`)
  return value
}

const ISSUES_QUERY = `
query($owner:String!,$repo:String!,$cursor:String){
  repository(owner:$owner,name:$repo){
    issues(states:OPEN,first:50,orderBy:{field:CREATED_AT,direction:DESC},after:$cursor){
      pageInfo{ hasNextPage endCursor }
      nodes{
        number title body createdAt updatedAt
        author{ login }
        labels(first:20){ nodes{ name } }
        comments{ totalCount }
        timelineItems(itemTypes:[REOPENED_EVENT,CLOSED_EVENT],first:30){
          nodes{
            __typename
            ... on ReopenedEvent { actor{ login } }
            ... on ClosedEvent { __typename }
          }
        }
      }
    }
  }
}`

const PRS_QUERY = `
query($owner:String!,$repo:String!,$cursor:String){
  repository(owner:$owner,name:$repo){
    pullRequests(states:MERGED,first:100,orderBy:{field:UPDATED_AT,direction:DESC},after:$cursor){
      pageInfo{ hasNextPage endCursor }
      nodes{ number mergedAt closingIssuesReferences(first:10){ nodes{ number } } }
    }
  }
}`

function graphqlPage(query: string, cursor: string | null): Record<string, unknown> {
  const [owner, name] = REPO.split('/')
  const args = [
    'api',
    'graphql',
    '-F',
    `owner=${owner}`,
    '-F',
    `repo=${name}`,
    '-f',
    `query=${query}`,
  ]
  if (cursor) args.push('-F', `cursor=${cursor}`)
  const parsed = expectObject(JSON.parse(gh(args)), 'graphql response')
  return expectObject(parsed.data, 'graphql data')
}

function readIssues(): Omit<IssueRecord, 'linkedByMergedPr' | 'namingCommits'>[] {
  const out: Omit<IssueRecord, 'linkedByMergedPr' | 'namingCommits'>[] = []
  let cursor: string | null = null
  for (;;) {
    const data = graphqlPage(ISSUES_QUERY, cursor)
    const conn = expectObject(expectObject(data.repository, 'repository').issues, 'issues')
    for (const raw of expectArray(conn.nodes, 'issue nodes')) {
      const n = expectObject(raw, 'issue')
      const timeline = expectArray(expectObject(n.timelineItems, 'timelineItems').nodes, 'timeline')
      const reopenedBy: string[] = []
      let everClosed = false
      for (const evRaw of timeline) {
        const ev = expectObject(evRaw, 'timeline event')
        if (ev.__typename === 'ClosedEvent') everClosed = true
        if (ev.__typename === 'ReopenedEvent') {
          const actor = ev.actor === null ? null : expectObject(ev.actor, 'reopen actor')
          reopenedBy.push(actor === null ? 'unknown' : str(actor.login, 'actor.login'))
        }
      }
      const author = n.author === null ? null : expectObject(n.author, 'author')
      out.push({
        number: Number(n.number),
        title: str(n.title, 'title'),
        body: typeof n.body === 'string' ? n.body : '',
        authorLogin: author === null ? 'ghost' : str(author.login, 'author.login'),
        createdAt: str(n.createdAt, 'createdAt'),
        updatedAt: str(n.updatedAt, 'updatedAt'),
        labels: expectArray(expectObject(n.labels, 'labels').nodes, 'label nodes').map((l) =>
          str(expectObject(l, 'label').name, 'label.name')
        ),
        commentCount: Number(expectObject(n.comments, 'comments').totalCount),
        reopenedBy,
        everClosed,
      })
    }
    const page = expectObject(conn.pageInfo, 'pageInfo')
    if (page.hasNextPage !== true) break
    cursor = str(page.endCursor, 'endCursor')
  }
  return out
}

/** issue number -> PR numbers that GitHub itself considers closing references. */
function readPrLinks(cutoffIso: string): Map<number, number[]> {
  const links = new Map<number, number[]>()
  let cursor: string | null = null
  for (;;) {
    const data = graphqlPage(PRS_QUERY, cursor)
    const conn = expectObject(
      expectObject(data.repository, 'repository').pullRequests,
      'pullRequests'
    )
    let reachedCutoff = false
    for (const raw of expectArray(conn.nodes, 'pr nodes')) {
      const pr = expectObject(raw, 'pr')
      if (str(pr.mergedAt, 'mergedAt') < cutoffIso) reachedCutoff = true
      const refs = expectArray(
        expectObject(pr.closingIssuesReferences, 'closingIssuesReferences').nodes,
        'closing refs'
      )
      for (const refRaw of refs) {
        const issueNumber = Number(expectObject(refRaw, 'closing ref').number)
        links.set(issueNumber, [...(links.get(issueNumber) ?? []), Number(pr.number)])
      }
    }
    const page = expectObject(conn.pageInfo, 'pageInfo')
    if (reachedCutoff || page.hasNextPage !== true) break
    cursor = str(page.endCursor, 'endCursor')
  }
  return links
}

/**
 * issue number -> merged commit SHAs whose SUBJECT names it.
 *
 * The trailing ` (#NNNN)` is the squash-merge PR number, not an issue, and is
 * stripped before matching. `ss#NNNN` is this enterprise's cross-venture
 * notation for an issue in this repo; GitHub does not read it as a link, which
 * is precisely why it must be matched here and cannot be inferred from PR
 * linkage.
 */
function readNamingCommits(sinceIso: string): Map<number, string[]> {
  const raw = execFileSync(
    'git',
    ['log', 'origin/main', `--since=${sinceIso}`, '--pretty=format:%H%x09%s'],
    { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }
  )
  const byIssue = new Map<number, string[]>()
  for (const line of raw.split('\n')) {
    if (!line.trim()) continue
    const tab = line.indexOf('\t')
    if (tab < 0) continue
    const sha = line.slice(0, tab)
    const subject = line.slice(tab + 1).replace(/\s*\(#\d+\)\s*$/, '')
    for (const m of subject.matchAll(/(?:ss)?#(\d{3,5})\b/g)) {
      const num = Number(m[1])
      byIssue.set(num, [...(byIssue.get(num) ?? []), sha])
    }
  }
  return byIssue
}

function fetchSnapshot(nowIso: string): Snapshot {
  const cutoff = new Date(Date.parse(nowIso) - HISTORY_DAYS * 86_400_000).toISOString()
  const issues = readIssues()
  const prLinks = readPrLinks(cutoff)
  const commits = readNamingCommits(cutoff)
  return {
    fetchedAt: nowIso,
    repo: REPO,
    issues: issues.map((i) => ({
      ...i,
      linkedByMergedPr: prLinks.get(i.number) ?? [],
      namingCommits: commits.get(i.number) ?? [],
    })),
  }
}

/**
 * Parse a snapshot from disk field by field.
 *
 * Not a cast. A snapshot is an external input like any other: an older file, a
 * hand-edited one, or one from a future schema must fail here naming the field,
 * rather than reach the classifier as `undefined` and be counted as a zero.
 * A column of zeros passes every summary check ever written.
 */
function parseIssue(raw: unknown, index: number): IssueRecord {
  const n = expectObject(raw, `snapshot.issues[${index}]`)
  const strings = (v: unknown, what: string): string[] =>
    expectArray(v, what).map((e, i) => str(e, `${what}[${i}]`))
  return {
    number: Number(n.number),
    title: str(n.title, `issues[${index}].title`),
    authorLogin: str(n.authorLogin, `issues[${index}].authorLogin`),
    createdAt: str(n.createdAt, `issues[${index}].createdAt`),
    updatedAt: str(n.updatedAt, `issues[${index}].updatedAt`),
    body: str(n.body, `issues[${index}].body`),
    labels: strings(n.labels, `issues[${index}].labels`),
    commentCount: Number(n.commentCount),
    reopenedBy: strings(n.reopenedBy, `issues[${index}].reopenedBy`),
    everClosed: n.everClosed === true,
    linkedByMergedPr: expectArray(n.linkedByMergedPr, `issues[${index}].linkedByMergedPr`).map(
      Number
    ),
    namingCommits: strings(n.namingCommits, `issues[${index}].namingCommits`),
  }
}

function loadSnapshot(path: string): Snapshot {
  const parsed = expectObject(JSON.parse(readFileSync(path, 'utf8')), 'snapshot')
  return {
    fetchedAt: str(parsed.fetchedAt, 'snapshot.fetchedAt'),
    repo: str(parsed.repo, 'snapshot.repo'),
    issues: expectArray(parsed.issues, 'snapshot.issues').map(parseIssue),
  }
}

function flagValue(argv: string[], name: string): string | null {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null
}

function main(): void {
  const argv = process.argv.slice(2)
  const wallClock = new Date().toISOString()
  const from = flagValue(argv, '--from')

  const snapshot = from ? loadSnapshot(from) : fetchSnapshot(wallClock)

  const snapshotOut = flagValue(argv, '--snapshot')
  if (snapshotOut) writeFileSync(snapshotOut, JSON.stringify(snapshot, null, 2))

  // Never `wallClock` directly: a reclassified snapshot must be judged against
  // the clock it was fetched at, or age-keyed rules move without the backlog.
  const now = censusClock(snapshot, {
    reclassified: from !== null,
    explicit: flagValue(argv, '--now'),
    wallClock,
  })
  const census = classify(snapshot, now)
  process.stdout.write(
    argv.includes('--json')
      ? `${JSON.stringify(census, null, 2)}\n`
      : `${renderReport(census, now)}\n`
  )
}

main()
