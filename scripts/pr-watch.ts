/**
 * pr-watch.ts -- watch PRs until they merge, saying every state change and
 * every stall. Run it through `.claude/bin/pr-watch`.
 *
 *   .claude/bin/pr-watch <pr> [<pr> ...] [--interval 60] [--max-hours 4] [--stall-minutes 30]
 *
 * One line per PR whenever what it shows changes: merged, closed, a check
 * failing, checks running, its place in the merge queue, or "green, not
 * queued". A PR whose line has not changed for --stall-minutes while it is
 * neither running checks nor in the merge queue gets one STALLED line, so a
 * stuck PR is never silent. Exits 0 when every PR is merged or closed, 1 at
 * --max-hours with a STILL OPEN line per PR, 2 on bad arguments.
 *
 * Why it exists (2026-11-06): two watchers that waited only for "merged" sat
 * silent through a failed lint check overnight and a broken main for hours.
 * And in this repo the merge queue takes a PR directly, so `autoMergeRequest`
 * reads null on a queued PR; this reads `mergeQueueEntry`.
 *
 * Env: SS_PRWATCH_GH  override the `gh` binary (tests); called as
 *                     `<gh> api graphql -f query=<q>` and must print the JSON.
 */

import { execFileSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

export type CheckState = 'pass' | 'fail' | 'running' | 'skip'

export interface PrSnapshot {
  number: number
  state: 'OPEN' | 'MERGED' | 'CLOSED'
  isDraft: boolean
  queue: { position: number; state: string } | null
  checks: { name: string; state: CheckState }[]
}

const FAIL = new Set([
  'FAILURE',
  'TIMED_OUT',
  'CANCELLED',
  'ACTION_REQUIRED',
  'STARTUP_FAILURE',
  'ERROR',
])
const SKIP = new Set(['SKIPPED', 'NEUTRAL', 'STALE'])

interface RawContext {
  name?: string
  context?: string
  status?: string
  conclusion?: string | null
  state?: string
}

/** One rollup context (a CheckRun or a commit StatusContext) as a check state. */
export function checkState(node: RawContext): CheckState {
  if (node.state !== undefined) {
    if (node.state === 'SUCCESS') return 'pass'
    if (node.state === 'PENDING' || node.state === 'EXPECTED') return 'running'
    return 'fail'
  }
  if (node.status !== 'COMPLETED') return 'running'
  const c = node.conclusion ?? ''
  if (c === 'SUCCESS') return 'pass'
  if (SKIP.has(c)) return 'skip'
  if (FAIL.has(c)) return 'fail'
  return 'fail' // an unknown conclusion is reported, never read as green
}

/** What a PR shows, as the line printed when it changes. */
export function describe(pr: PrSnapshot): string {
  if (pr.state !== 'OPEN') return pr.state
  const failing = pr.checks
    .filter((c) => c.state === 'fail')
    .map((c) => c.name)
    .sort()
  const running = pr.checks.filter((c) => c.state === 'running').length
  const parts: string[] = [pr.isDraft ? 'draft' : 'open']
  if (failing.length) parts.push(`FAILING ${failing.join(', ')}`)
  // No count: a line per finished check would wake a Monitor every minute for nothing.
  if (running) parts.push('checks running')
  if (pr.queue) parts.push(`queued #${pr.queue.position} ${pr.queue.state}`)
  else if (!failing.length && !running) parts.push(pr.isDraft ? 'green' : 'green, not queued')
  return parts.join(' | ')
}

/** True when a PR is open and nothing about it is moving: no check running, not in the queue. */
export function idle(pr: PrSnapshot): boolean {
  return pr.state === 'OPEN' && !pr.queue && !pr.checks.some((c) => c.state === 'running')
}

const QUERY = (numbers: number[]): string =>
  `{repository(owner:"venturecrane",name:"ss-console"){${numbers
    .map(
      (n) =>
        `p${n}:pullRequest(number:${n}){number state isDraft mergeQueueEntry{position state} ` +
        `commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{__typename ` +
        `... on CheckRun{name status conclusion} ... on StatusContext{context state}}}}}}}}`
    )
    .join(' ')}}}`

interface RawPr {
  number: number
  state: PrSnapshot['state']
  isDraft: boolean
  mergeQueueEntry: { position: number; state: string } | null
  commits: {
    nodes: { commit: { statusCheckRollup: { contexts: { nodes: RawContext[] } } | null } }[]
  }
}

/** Parse one GraphQL response into snapshots. Throws on a PR the response lacks. */
export function parse(json: string, numbers: number[]): PrSnapshot[] {
  const body = JSON.parse(json) as { data?: { repository?: Record<string, RawPr | null> } }
  const repo = body.data?.repository
  if (!repo) throw new Error('response has no repository')
  return numbers.map((n) => {
    const raw = repo[`p${n}`]
    if (!raw) throw new Error(`PR #${n} not found`)
    const nodes = raw.commits.nodes[0]?.commit.statusCheckRollup?.contexts.nodes ?? []
    return {
      number: raw.number,
      state: raw.state,
      isDraft: raw.isDraft,
      queue: raw.mergeQueueEntry,
      checks: nodes.map((c) => ({ name: c.name ?? c.context ?? '?', state: checkState(c) })),
    }
  })
}

function fetchAll(numbers: number[]): PrSnapshot[] {
  const gh = process.env.SS_PRWATCH_GH ?? 'gh'
  const out = execFileSync(gh, ['api', 'graphql', '-f', `query=${QUERY(numbers)}`], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  return parse(out, numbers)
}

function stamp(): string {
  return new Date().toLocaleTimeString('en-US', {
    timeZone: 'America/Phoenix',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const FLAGS = new Set(['--interval', '--max-hours', '--stall-minutes'])

function flag(argv: string[], name: string, fallback: number): number {
  const i = argv.indexOf(name)
  if (i < 0) return fallback
  const v = Number(argv[i + 1])
  if (!Number.isFinite(v) || v <= 0) throw new Error(`${name} needs a positive number`)
  return v
}

interface Options {
  numbers: number[]
  interval: number
  maxHours: number
  stallMin: number
}

/** The command line, or a message saying what is wrong with it. */
function options(argv: string[]): Options | string {
  try {
    const numbers = argv
      .filter((a, i) => !FLAGS.has(a) && !FLAGS.has(argv[i - 1] ?? ''))
      .map(Number)
    if (!numbers.length || numbers.some((n) => !Number.isInteger(n) || n <= 0)) {
      return 'usage: pr-watch <pr> [<pr> ...] [--interval 60] [--max-hours 4] [--stall-minutes 30]'
    }
    return {
      numbers,
      interval: flag(argv, '--interval', 60),
      maxHours: flag(argv, '--max-hours', 4),
      stallMin: flag(argv, '--stall-minutes', 30),
    }
  } catch (e) {
    return `pr-watch: ${(e as Error).message}`
  }
}

async function main(argv: string[]): Promise<number> {
  const opts = options(argv)
  if (typeof opts === 'string') {
    console.error(opts)
    return 2
  }
  const { numbers, interval, maxHours, stallMin } = opts
  const last = new Map<number, { line: string; since: number; stalled: boolean }>()
  const deadline = Date.now() + maxHours * 3_600_000
  for (;;) {
    let snaps: PrSnapshot[] = []
    try {
      snaps = fetchAll(numbers)
    } catch (e) {
      // A failed read is said, never treated as "nothing changed".
      console.log(`${stamp()} could not read the PRs: ${(e as Error).message.split('\n')[0]}`)
    }
    const now = Date.now()
    for (const pr of snaps) {
      const line = describe(pr)
      const prev = last.get(pr.number)
      if (!prev || prev.line !== line) {
        console.log(`${stamp()} #${pr.number} ${line}`)
        last.set(pr.number, { line, since: now, stalled: false })
      } else if (!prev.stalled && idle(pr) && now - prev.since >= stallMin * 60_000) {
        const mins = Math.round((now - prev.since) / 60_000)
        console.log(`${stamp()} #${pr.number} STALLED ${mins} min: ${line}`)
        prev.stalled = true
      }
    }
    if (snaps.length && snaps.every((p) => p.state !== 'OPEN')) {
      console.log('all merged or closed')
      return 0
    }
    if (now >= deadline) {
      for (const pr of snaps.filter((p) => p.state === 'OPEN')) {
        console.log(`STILL OPEN #${pr.number}: ${describe(pr)}`)
      }
      return 1
    }
    await new Promise((r) => setTimeout(r, interval * 1000))
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main(process.argv.slice(2)).then((code) => process.exit(code))
}
