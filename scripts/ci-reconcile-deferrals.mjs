#!/usr/bin/env node
/**
 * Overdue runtime deferrals: the `enforce_by` dates nothing reads after merge.
 *
 * Why this exists (wave 2, 2026-11-09). The merge gate
 * (scripts/runtime-ac-proof.mjs) accepts a `(runtime)` row marked `deferred`
 * when it names `owner: <who>; enforce_by: YYYY-MM-DD` and the date has not
 * passed. After merge nothing reads that date again: the gate only ever sees
 * the PR it is running on, and the template and docs are prose. A deferral is a
 * promise with a deadline and no one watching the deadline.
 *
 * This lister reads every PR merged in the last 120 days, parses its runtime
 * rows with the merge gate's own parser, and lists each deferral whose
 * enforce_by has now passed, unless a LATER merged PR references the PR's
 * number and carries a `met` runtime row (the switch-on, proven).
 *
 * Overdue is computed with the gate's own deferralReason(): a row valid on the
 * day it merged and invalid today has had its date pass. No second copy of the
 * owner and date grammar exists here.
 *
 * Each finding is marked with the PR's touch class (scripts/lib/touch-map.mjs).
 * Only rows on PRs that touched a client surface are work owed to a client;
 * the rest are process items. The rolling issue says so and carries the
 * `process-deferral` label only, never `client`: the obligation register
 * imports client-labelled issues, and a process deferral is not a client
 * obligation.
 *
 * Replay mode (--replay-unmet): before 2026-11-09 the gate let "not met" rows
 * through. With this flag, an unmet row, or a deferral with no date, is read as
 * a deferral due on its PR's merge date, which is how #3852's two rows surface.
 *
 * Exit codes follow the reconcilers: 0 nothing overdue, 2 findings, 1 the
 * control could not evaluate (a search window at GitHub's 1000-result cap, an
 * unreadable API). "Could not look" never reads as "nothing overdue".
 *
 * Usage: node scripts/ci-reconcile-deferrals.mjs [--days 120] [--replay-unmet] [--json]
 * Env:   GH_TOKEN, GITHUB_REPOSITORY (owner/repo; falls back to the gh default)
 */

import { spawnSync } from 'node:child_process'
import { classifyPaths } from './lib/touch-map.mjs'
import { runtimeRows, deferralReason } from './runtime-ac-proof.mjs'

/** GitHub's search API returns at most this many results per query. */
export const SEARCH_CAP = 1000

/** Days per search window; at about 16 merges a day this stays near half the cap. */
export const WINDOW_DAYS = 30

const DAY_MS = 86_400_000

/** The date a deferral's reason names as passed, when that is why it fails. */
const PASSED_RE = /enforce_by (\d{4}-\d{2}-\d{2}) has passed/

/**
 * Date windows covering [from, to], each WINDOW_DAYS long, as YYYY-MM-DD pairs.
 * @param {Date} from
 * @param {Date} to
 */
export function windows(from, to, days = WINDOW_DAYS) {
  const out = []
  let start = new Date(Date.UTC(from.getUTCFullYear(), from.getUTCMonth(), from.getUTCDate()))
  while (start <= to) {
    const end = new Date(Math.min(start.getTime() + (days - 1) * DAY_MS, to.getTime()))
    out.push([start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)])
    start = new Date(start.getTime() + days * DAY_MS)
  }
  return out
}

/**
 * The overdue deferrals in one PR body.
 * @param {string} body
 * @param {string} mergedAt - ISO timestamp
 * @param {Date} now
 * @param {{ replayUnmet?: boolean }} [opts]
 * @returns {{ ac: string, enforceBy: string, replayed: boolean }[]}
 */
export function overdueRows(body, mergedAt, now, opts = {}) {
  const merged = new Date(mergedAt)
  const mergedDay = mergedAt.slice(0, 10)
  const out = []
  for (const r of runtimeRows(body)) {
    if (r.kind === 'deferred') {
      const atMerge = deferralReason(r.row, merged)
      const today = deferralReason(r.row, now)
      const passed = today?.match(PASSED_RE)?.[1]
      if (atMerge === null && passed) {
        out.push({ ac: r.ac, enforceBy: passed, replayed: false })
        continue
      }
      if (opts.replayUnmet && atMerge !== null && !atMerge.includes('has passed')) {
        if (mergedDay < now.toISOString().slice(0, 10)) {
          out.push({ ac: r.ac, enforceBy: mergedDay, replayed: true })
        }
      }
    } else if (opts.replayUnmet && r.kind === 'unmet') {
      if (mergedDay < now.toISOString().slice(0, 10)) {
        out.push({ ac: r.ac, enforceBy: mergedDay, replayed: true })
      }
    }
  }
  return out
}

/**
 * Whether a later merged PR discharges `number`: it references `#number`,
 * carries at least one `met` runtime row, and, when the deferring PR touched a
 * client surface, touches one too. The last clause is from the replay: #3909, a
 * CI-only PR, cited #3852 as an example and carried a met row of its own, which
 * would otherwise have "discharged" #3852's two client-lane rows.
 * @param {{ number: number, mergedAt: string }} pr
 * @param {{ number: number, body: string, mergedAt: string }[]} prs
 * @param {(p: { number: number }) => string} clsOf - the touch class of a PR
 * @returns {number | null} the discharging PR, or null
 */
export function dischargedBy(pr, prs, clsOf = (p) => p.cls ?? 'none') {
  const ownClient = clsOf(pr) !== 'none'
  for (const p of prs) {
    if (p.number === pr.number || p.mergedAt <= pr.mergedAt) continue
    const refs = [...String(p.body ?? '').matchAll(/(?:^|[^\w/])#(\d+)\b/g)].map((m) =>
      Number(m[1])
    )
    if (!refs.includes(pr.number)) continue
    if (!runtimeRows(p.body).some((r) => r.kind === 'met')) continue
    if (ownClient && clsOf(p) === 'none') continue
    return p.number
  }
  return null
}

/**
 * Every overdue, undischarged deferral among `prs`, each with its touch class.
 * @param {{ number: number, body: string, mergedAt: string, cls?: string }[]} prs
 * @param {Date} now
 * @param {{ replayUnmet?: boolean, clsOf?: (p: { number: number }) => string }} [opts]
 */
export function findOverdue(prs, now, opts = {}) {
  const clsOf = opts.clsOf ?? ((p) => p.cls ?? 'none')
  const findings = []
  let discharged = 0
  for (const p of prs) {
    const rows = overdueRows(p.body ?? '', p.mergedAt, now, opts)
    if (rows.length === 0) continue
    if (dischargedBy(p, prs, clsOf) !== null) {
      discharged++
      continue
    }
    findings.push({ number: p.number, mergedAt: p.mergedAt, cls: clsOf(p), rows })
  }
  return { findings, discharged }
}

/**
 * The issue body text: each PR with its class and overdue rows.
 * @param {{ number: number, mergedAt: string, cls?: string, rows: { ac: string, enforceBy: string, replayed: boolean }[] }[]} findings
 * @param {{ scanned: number, days: number, discharged: number, replayUnmet?: boolean }} meta
 */
export function formatReport(findings, meta) {
  const rows = findings.reduce((n, f) => n + f.rows.length, 0)
  const client = findings.filter((f) => f.cls && f.cls !== 'none')
  const lines = [
    `Runtime deferrals past their enforce_by: ${rows} row(s) on ${findings.length} PR(s), ` +
      `${client.length} of them on a client surface. Read ${meta.scanned} PRs merged in the last ${meta.days} days; ` +
      `${meta.discharged} overdue PR(s) were discharged by a later PR with a met runtime row.` +
      (meta.replayUnmet ? ' REPLAY MODE: unmet rows read as deferred to their merge date.' : ''),
    '',
    'Only rows on PRs that touched a client surface (send-path, client-lane, seat) are work owed to a client;',
    'the rest are process items. This issue is never labelled `client` and the register does not import it.',
    'To clear a row: prove it in a new PR whose body references the PR number and carries the row as `met`',
    'with a crane_verify ID, or set a new `enforce_by` in the original PR body.',
    '',
  ]
  for (const f of findings) {
    lines.push(`#${f.number} (merged ${f.mergedAt.slice(0, 10)}, ${f.cls ?? 'unclassified'})`)
    for (const r of f.rows) {
      lines.push(`  - due ${r.enforceBy}${r.replayed ? ' (replayed unmet)' : ''}: ${r.ac}`)
    }
  }
  return lines.join('\n')
}

function gh(args) {
  const r = spawnSync('gh', args, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 })
  if (r.status !== 0)
    throw new Error(`gh ${args.slice(0, 3).join(' ')}: ${(r.stderr || '').trim()}`)
  return r.stdout
}

function repoName() {
  if (process.env.GITHUB_REPOSITORY) return process.env.GITHUB_REPOSITORY
  return gh(['repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner']).trim()
}

/** Merged PRs in [from, to], every window asserted under the search cap. */
function fetchMerged(repo, from, to) {
  const byNumber = new Map()
  for (const [a, b] of windows(from, to)) {
    const out = gh([
      'api',
      '-X',
      'GET',
      'search/issues',
      '--paginate',
      '-f',
      `q=repo:${repo} is:pr is:merged merged:${a}..${b}`,
      '-f',
      'per_page=100',
      '--jq',
      '{total: .total_count, items: [.items[] | {number, body, mergedAt: .pull_request.merged_at}]}',
    ])
    let total = 0
    let seen = 0
    for (const line of out.split('\n').filter(Boolean)) {
      const page = JSON.parse(line)
      total = page.total
      for (const it of page.items) {
        seen++
        byNumber.set(it.number, {
          number: it.number,
          body: it.body ?? '',
          mergedAt: it.mergedAt ?? '',
        })
      }
    }
    if (total >= SEARCH_CAP) {
      throw new Error(
        `window ${a}..${b} holds ${total} PRs, at the ${SEARCH_CAP}-result search cap; shorten WINDOW_DAYS`
      )
    }
    if (seen < total) throw new Error(`window ${a}..${b}: read ${seen} of ${total} PRs`)
  }
  return [...byNumber.values()]
    .filter((p) => p.mergedAt)
    .sort((x, y) => (x.mergedAt < y.mergedAt ? -1 : 1))
}

function pathsOf(repo, number) {
  return gh([
    'api',
    '--paginate',
    `repos/${repo}/pulls/${number}/files?per_page=100`,
    '--jq',
    '.[].filename',
  ])
    .split('\n')
    .filter(Boolean)
}

function main() {
  const argv = process.argv.slice(2)
  const days = Number(argv[argv.indexOf('--days') + 1]) || 120
  const replayUnmet = argv.includes('--replay-unmet')
  const now = new Date()
  const repo = repoName()
  const prs = fetchMerged(repo, new Date(now.getTime() - days * DAY_MS), now)
  const memo = new Map()
  const clsOf = (p) => {
    if (!memo.has(p.number)) memo.set(p.number, classifyPaths(pathsOf(repo, p.number)).cls)
    return memo.get(p.number)
  }
  const { findings, discharged } = findOverdue(prs, now, { replayUnmet, clsOf })
  const meta = { scanned: prs.length, days, discharged, replayUnmet }
  if (argv.includes('--json')) {
    process.stdout.write(JSON.stringify({ ...meta, findings }, null, 2) + '\n')
  } else {
    process.stdout.write(formatReport(findings, meta) + '\n')
  }
  return findings.length > 0 ? 2 : 0
}

if (import.meta.url === `file://${process.argv[1]}`) {
  let code = 1
  try {
    code = main()
  } catch (e) {
    process.stdout.write(`COULD NOT EVALUATE: ${e instanceof Error ? e.message : String(e)}\n`)
  }
  process.exit(code)
}
