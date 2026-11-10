/**
 * Rework measure: the fetch half, and the CLI door.
 *
 * Links each fix PR to the feature PR(s) it repairs and reports, per feature
 * merged since a date, how many fixes followed, how strong each link is, how
 * soon the first one came, and how many defects were caught before merge.
 * Read-only: `gh` reads merged PRs, `git` reads diffs and blame. Nothing is
 * written anywhere except stdout and an optional snapshot file.
 *
 * Split like scripts/backlog-census.ts: this half needs credentials, a network
 * and a full clone; the linker (scripts/rework/link.ts) and the report
 * (scripts/rework/report.ts) are pure and gated by tests/rework-link.test.ts.
 *
 * Usage:
 *   npm run rework                                     # since 2026-09-01
 *   npm run rework -- --since 2026-10-01
 *   npm run rework -- --json
 *   npm run rework -- --snapshot out.json              # keep the raw snapshot
 *   npm run rework -- --from snap.json                 # re-measure, no network
 *   npm run rework -- --from snap.json --sessions map.json
 *   npm run rework -- --built-by path/to/built-by.jsonl
 *
 * `--sessions` takes a JSON object of feature PR number -> session type (for
 * example "auto-build" or "other") and adds a per-session-type table. That map
 * is built from local Claude Code transcripts by a local-only tool; transcript
 * access deliberately does not live in this repo.
 *
 * `--built-by` (default `~/.claude/ss-board/built-by.jsonl`, skipped when the
 * file is absent) is the per-PR record the PostToolUse hook
 * `.claude/hooks/bash-log.mjs` writes on every `gh pr create`. A PR it names
 * is labelled from it; any other PR falls back to `--sessions`.
 *
 * Blame runs against `origin/main` in the local clone, so `git fetch` first.
 */

import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import {
  kindOf,
  parseBuiltBy,
  type BuiltByRecord,
  type PrRecord,
  type Snapshot,
} from './rework/link'
import { measure, processLabels, renderReport } from './rework/report'

const REPO = process.env.REWORK_REPO ?? 'venturecrane/ss-console'
const DEFAULT_SINCE = '2026-09-01'
/** Earlier PRs are read so a fix in the window can link to an older feature. */
const LOOKBACK_DAYS = 60
/** Generated files: blame on them attributes lines to whoever last regenerated. */
const NO_BLAME = /(^|\/)(package-lock\.json|[^/]*\.lock|pnpm-lock\.yaml)$/
const PRS_QUERY = `
query($q:String!,$cursor:String){
  search(query:$q,type:ISSUE,first:50,after:$cursor){
    issueCount
    pageInfo{ hasNextPage endCursor }
    nodes{ ... on PullRequest {
      number title headRefName body createdAt mergedAt
      mergeCommit{ oid }
      files(first:100){ nodes{ path } }
      commits(first:100){ nodes{ commit{ messageHeadline messageBody } } }
      closingIssuesReferences(first:10){ nodes{ number } }
    } }
  }
}`
function run(cmd: string, args: string[]): string {
  return execFileSync(cmd, args, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 })
}

/** Parse, never cast: a shape change at GitHub must fail here, by name. */
function obj(v: unknown, what: string): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v))
    throw new Error(`expected object: ${what}`)
  return v as Record<string, unknown>
}
function arr(v: unknown, what: string): unknown[] {
  if (!Array.isArray(v)) throw new Error(`expected array: ${what}`)
  return v
}
function str(v: unknown, what: string): string {
  if (typeof v !== 'string') throw new Error(`expected string: ${what}`)
  return v
}

interface Fetched {
  record: PrRecord
  mergeSha: string | null
}

function nodes(v: unknown, what: string): unknown[] {
  return arr(obj(v, what).nodes, `${what}.nodes`)
}

function parsePr(raw: unknown): Fetched {
  const n = obj(raw, 'pr')
  const what = `pr #${String(n.number)}`
  const commits = nodes(n.commits, `${what}.commits`).map((c) => {
    const o = obj(obj(c, `${what}.commit`).commit, `${what}.commit.commit`)
    const body = typeof o.messageBody === 'string' ? o.messageBody : ''
    return `${str(o.messageHeadline, 'messageHeadline')}\n${body}`
  })
  const merge = n.mergeCommit === null ? null : obj(n.mergeCommit, `${what}.mergeCommit`)
  return {
    record: {
      number: Number(n.number),
      title: str(n.title, `${what}.title`),
      headRefName: str(n.headRefName, `${what}.headRefName`),
      body: typeof n.body === 'string' ? n.body : '',
      createdAt: str(n.createdAt, `${what}.createdAt`),
      mergedAt: str(n.mergedAt, `${what}.mergedAt`),
      files: nodes(n.files, `${what}.files`).map((f) => str(obj(f, 'file').path, 'file.path')),
      commitMessages: commits,
      closesIssues: nodes(n.closingIssuesReferences, `${what}.closing`).map((i) =>
        Number(obj(i, 'closing ref').number)
      ),
      blamed: null,
    },
    mergeSha: merge === null ? null : str(merge.oid, `${what}.mergeCommit.oid`),
  }
}

function searchSlice(q: string): Fetched[] {
  const out: Fetched[] = []
  let cursor: string | null = null
  for (;;) {
    const args = ['api', 'graphql', '-f', `query=${PRS_QUERY}`, '-f', `q=${q}`]
    if (cursor) args.push('-f', `cursor=${cursor}`)
    const search = obj(
      obj(obj(JSON.parse(run('gh', args)), 'response').data, 'data').search,
      'search'
    )
    if (Number(search.issueCount) >= 1000)
      throw new Error(`${q} hit the 1000 search cap; narrow it`)
    out.push(...arr(search.nodes, 'search.nodes').map(parsePr))
    const page = obj(search.pageInfo, 'pageInfo')
    if (page.hasNextPage !== true) return out
    cursor = str(page.endCursor, 'endCursor')
  }
}

/** Merged PRs in [from, to), in ten-day slices to stay under search's 1000 cap. */
function fetchPrs(fromIso: string, toIso: string): Fetched[] {
  const out: Fetched[] = []
  const end = Date.parse(toIso)
  for (let t = Date.parse(fromIso); t < end; t += 10 * 86_400_000) {
    const a = new Date(t).toISOString().slice(0, 10)
    const b = new Date(Math.min(t + 10 * 86_400_000, end) - 86_400_000).toISOString().slice(0, 10)
    out.push(...searchSlice(`repo:${REPO} is:pr is:merged merged:${a}..${b}`))
  }
  return out
}

/** squash commit sha on origin/main -> PR number, from the `(#NNNN)` suffix. */
function shaToPr(): Map<string, number> {
  const map = new Map<string, number>()
  for (const line of run('git', ['log', 'origin/main', '--format=%H %s']).split('\n')) {
    const m = /^([0-9a-f]{40}) .*\(#(\d+)\)\s*$/.exec(line)
    if (m) map.set(m[1], Number(m[2]))
  }
  return map
}

/** file -> old-side line numbers the commit changes (an insertion blames its anchor line). */
function changedOldLines(diff: string): Map<string, number[]> {
  const out = new Map<string, number[]>()
  let file: string | null = null
  for (const line of diff.split('\n')) {
    if (line.startsWith('--- ')) {
      file = line === '--- /dev/null' ? null : line.slice(6)
      continue
    }
    const h = /^@@ -(\d+)(?:,(\d+))? /.exec(line)
    if (!h || file === null || NO_BLAME.test(file)) continue
    const start = Number(h[1])
    const count = h[2] === undefined ? 1 : Number(h[2])
    const lines =
      count > 0 ? Array.from({ length: count }, (_, i) => start + i) : start > 0 ? [start] : []
    out.set(file, [...(out.get(file) ?? []), ...lines])
  }
  return out
}

function blameFix(sha: string, prOf: Map<string, number>): Record<string, number> {
  const counts: Record<string, number> = {}
  const diff = run('git', ['diff', '-U0', '--no-color', '--no-renames', `${sha}^`, sha])
  for (const [file, lines] of changedOldLines(diff)) {
    if (lines.length === 0) continue
    const ranges = lines.flatMap((l) => ['-L', `${l},${l}`])
    let porcelain: string
    try {
      porcelain = run('git', ['blame', '--porcelain', '-w', ...ranges, `${sha}^`, '--', file])
    } catch {
      continue // file absent at the parent under this name; nothing to attribute
    }
    for (const m of porcelain.matchAll(/^([0-9a-f]{40}) \d+ \d+ (\d+)$/gm)) {
      const pr = prOf.get(m[1])
      if (pr !== undefined) counts[String(pr)] = (counts[String(pr)] ?? 0) + Number(m[2])
    }
  }
  return counts
}

function fetchSnapshot(since: string, nowIso: string): Snapshot {
  const from = new Date(Date.parse(since) - LOOKBACK_DAYS * 86_400_000).toISOString()
  const fetched = fetchPrs(from, new Date(Date.parse(nowIso) + 86_400_000).toISOString())
  const prOf = shaToPr()
  const onMain = new Set(prOf.keys())
  for (const f of fetched) {
    if (kindOf(f.record) !== 'fix' || f.record.mergedAt < since) continue
    if (f.mergeSha && onMain.has(f.mergeSha)) f.record.blamed = blameFix(f.mergeSha, prOf)
  }
  return { fetchedAt: nowIso, repo: REPO, since, prs: fetched.map((f) => f.record) }
}

function loadSnapshot(path: string): Snapshot {
  const s = obj(JSON.parse(readFileSync(path, 'utf8')), 'snapshot')
  const prs = arr(s.prs, 'snapshot.prs').map((raw, i) => {
    const p = obj(raw, `prs[${i}]`)
    const strings = (v: unknown, w: string) => arr(v, w).map((x) => str(x, w))
    const blamed = p.blamed === null ? null : obj(p.blamed, `prs[${i}].blamed`)
    return {
      number: Number(p.number),
      title: str(p.title, `prs[${i}].title`),
      headRefName: str(p.headRefName, `prs[${i}].headRefName`),
      body: str(p.body, `prs[${i}].body`),
      createdAt: str(p.createdAt, `prs[${i}].createdAt`),
      mergedAt: str(p.mergedAt, `prs[${i}].mergedAt`),
      files: strings(p.files, `prs[${i}].files`),
      commitMessages: strings(p.commitMessages, `prs[${i}].commitMessages`),
      closesIssues: arr(p.closesIssues, `prs[${i}].closesIssues`).map(Number),
      blamed:
        blamed === null
          ? null
          : Object.fromEntries(Object.entries(blamed).map(([k, v]) => [k, Number(v)])),
    }
  })
  return {
    fetchedAt: str(s.fetchedAt, 'fetchedAt'),
    repo: str(s.repo, 'repo'),
    since: str(s.since, 'since'),
    prs,
  }
}

function loadSessions(path: string): Map<number, string> {
  const o = obj(JSON.parse(readFileSync(path, 'utf8')), 'sessions map')
  return new Map(Object.entries(o).map(([k, v]) => [Number(k), str(v, `sessions[${k}]`)]))
}

const DEFAULT_BUILT_BY = join(homedir(), '.claude', 'ss-board', 'built-by.jsonl')

/** The built-by records, or null when the file is absent (nothing recorded yet). */
function loadBuiltBy(path: string): BuiltByRecord[] | null {
  if (!existsSync(path)) return null
  const { records, skipped } = parseBuiltBy(readFileSync(path, 'utf8'))
  if (skipped > 0)
    process.stderr.write(`built-by: skipped ${skipped} malformed line(s) in ${path}\n`)
  return records
}

function flag(argv: string[], name: string): string | null {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null
}

function main(): void {
  const argv = process.argv.slice(2)
  const from = flag(argv, '--from')
  const snapshot = from
    ? loadSnapshot(from)
    : fetchSnapshot(flag(argv, '--since') ?? DEFAULT_SINCE, new Date().toISOString())
  const out = flag(argv, '--snapshot')
  if (out) writeFileSync(out, JSON.stringify(snapshot, null, 2))
  const since = flag(argv, '--since')
  const sessionsPath = flag(argv, '--sessions')
  const labels = processLabels(
    loadBuiltBy(flag(argv, '--built-by') ?? DEFAULT_BUILT_BY),
    sessionsPath ? loadSessions(sessionsPath) : null,
    snapshot.repo
  )
  const m = measure(since && from ? { ...snapshot, since } : snapshot, labels)
  process.stdout.write(
    argv.includes('--json') ? `${JSON.stringify(m, null, 2)}\n` : `${renderReport(m)}\n`
  )
}

main()
