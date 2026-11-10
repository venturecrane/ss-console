#!/usr/bin/env node
/**
 * /eos check J: what this session shipped to a client surface, and whether it
 * is READY.
 *
 * Why this exists (wave 2, 2026-11-09). The merge gate's touch rule
 * (scripts/runtime-ac-proof.mjs, checkTouchRule) REPORTS a client-surface PR
 * that carries no `(runtime)` row and never blocks on it: blocking at merge
 * time bought boilerplate deferrals in the replay. Enforcement of that half
 * moves to session close, where the question is whole: of the PRs this session
 * merged, which reach a client, and is each one READY? The document-library
 * session merged a client-lane build with no runtime row and closed green; the
 * next session could not tell the feature had never been switched on.
 *
 * What a machine can see, and nothing else: the merged PRs (by the session's
 * `Claude-Session:` trailer on main, or by author and merge date), their
 * changed paths (classified by scripts/lib/touch-map.mjs), and the runtime rows
 * in their bodies (parsed by scripts/runtime-ac-proof.mjs, whose `met` rows the
 * merge gate already resolved against the verify ledger).
 *
 * The rule: a client-surface PR is READY only when it carries at least one
 * `(runtime)` row and every runtime row is `met`. No row, or any row deferred,
 * n/a or unmet, lists the PR under "Shipped, not READY", which the handoff
 * carries verbatim.
 *
 * It NEVER blocks the close. It always exits 0. When it cannot read what it
 * needs, it says so in its output ("could not evaluate"), because "could not
 * look" must never read as "nothing to report".
 *
 * Usage:
 *   node scripts/eos-ready.mjs --session <session id, e.g. session_01ABC...>
 *   node scripts/eos-ready.mjs --since 2026-11-09T00:00:00Z     (author @me)
 *   node scripts/eos-ready.mjs --pr 3906,3909                    (replay)
 *   add --json for machine output.
 */

import { spawnSync } from 'node:child_process'
import { classifyPaths } from './lib/touch-map.mjs'
import { runtimeRows, naReason } from './runtime-ac-proof.mjs'

/**
 * The PR numbers whose squash commits carry this session's trailer.
 * @param {string} logText - `git log --format=%x1e%s%n%b` output
 * @param {string} session - the session id, or any unique substring of the trailer URL
 * @returns {number[]} ascending, de-duplicated
 */
export function prsFromLog(logText, session) {
  const needle = String(session ?? '').trim()
  if (needle.length < 8) return []
  const found = new Set()
  for (const commit of String(logText ?? '').split('\x1e')) {
    const [subject = '', ...rest] = commit.split('\n')
    const trailer = rest.some((l) => /^Claude-Session:/i.test(l.trim()) && l.includes(needle))
    if (!trailer) continue
    const n = subject.match(/\(#(\d+)\)\s*$/)?.[1]
    if (n) found.add(Number(n))
  }
  return [...found].sort((a, b) => a - b)
}

/**
 * Readiness of one PR from what a machine can see.
 * @param {{ number: number, title?: string, paths: string[], rows: { ac?: string, kind: string, naReasoned?: boolean }[] }} pr
 */
export function assess(pr) {
  const touch = classifyPaths(pr.paths)
  const out = {
    number: pr.number,
    title: pr.title ?? '',
    cls: touch.cls,
    clientSurface: touch.clientSurface,
    rows: pr.rows.map((r) => ({ ac: r.ac ?? '', kind: r.kind, naReasoned: !!r.naReasoned })),
    ready: !touch.clientSurface,
    why: /** @type {string[]} */ ([]),
  }
  if (!touch.clientSurface) return out
  if (pr.rows.length === 0) out.why.push('no (runtime) row')
  for (const r of pr.rows) {
    if (r.kind === 'met') continue
    const label = r.kind === 'na' ? (r.naReasoned ? 'n/a' : 'n/a with no reason') : r.kind
    out.why.push(`${label}: ${r.ac || '(unnamed row)'}`)
  }
  out.ready = out.why.length === 0
  return out
}

/** Runtime rows of a body, reduced to what assess() reads. */
export function rowsOf(body) {
  return runtimeRows(body).map((r) => ({
    ac: r.ac,
    kind: r.kind,
    naReasoned: r.kind === 'na' && naReason(r) !== null,
  }))
}

/**
 * The report text. `notReady` lines are the ones the handoff carries verbatim.
 * @param {ReturnType<typeof assess>[]} results
 * @param {{ source: string, errors?: string[] }} meta
 */
export function report(results, meta) {
  const lines = []
  const client = results.filter((r) => r.clientSurface)
  lines.push(
    `Check J (READY at close): ${results.length} PR(s) read via ${meta.source}, ${client.length} touch a client surface.`
  )
  for (const e of meta.errors ?? []) lines.push(`COULD NOT EVALUATE: ${e}`)
  for (const r of client) {
    const kinds = r.rows.length === 0 ? 'no runtime row' : r.rows.map((x) => x.kind).join(', ')
    lines.push(`  #${r.number} ${r.cls}: ${r.ready ? 'READY' : 'not READY'} (${kinds})`)
  }
  const notReady = client.filter((r) => !r.ready)
  lines.push('')
  if (notReady.length === 0) {
    lines.push(
      (meta.errors ?? []).length > 0
        ? 'Shipped, not READY: unknown (see COULD NOT EVALUATE above)'
        : 'Shipped, not READY: none'
    )
  } else {
    lines.push('Shipped, not READY:')
    for (const r of notReady) {
      lines.push(`- #${r.number} ${r.title} [${r.cls}]: ${r.why.join('; ')}`)
    }
  }
  return lines.join('\n')
}

function sh(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(' ')}: ${(r.stderr || '').trim()}`)
  return r.stdout
}

function parseArgs(argv) {
  const a = { json: false }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--json') a.json = true
    else if (k === '--session') a.session = argv[++i]
    else if (k === '--since') a.since = argv[++i]
    else if (k === '--pr') a.prs = argv[++i]
  }
  return a
}

/** PR numbers to read, and where they came from. */
function selectPrs(args, errors) {
  if (args.prs) {
    return { prs: args.prs.split(',').map(Number).filter(Boolean), source: '--pr' }
  }
  if (args.session) {
    try {
      sh('git', ['fetch', '-q', 'origin', 'main'])
    } catch (e) {
      errors.push(`git fetch origin main failed, reading the local origin/main (${e.message})`)
    }
    const since = args.since ?? '30 days ago'
    const log = sh('git', ['log', 'origin/main', `--since=${since}`, '--format=%x1e%s%n%b'])
    return {
      prs: prsFromLog(log, args.session),
      source: `the Claude-Session trailer on origin/main`,
    }
  }
  if (args.since) {
    const out = sh('gh', [
      'pr',
      'list',
      '--author',
      '@me',
      '--state',
      'merged',
      '--limit',
      '300',
      '--search',
      `merged:>=${args.since}`,
      '--json',
      'number',
    ])
    const prs = JSON.parse(out).map((p) => p.number)
    if (prs.length >= 300) errors.push('gh pr list hit its 300 limit; the list may be incomplete')
    return { prs, source: `gh pr list --author @me merged:>=${args.since}` }
  }
  throw new Error('give --session <id>, --since <iso date> or --pr <n,n>')
}

function readPr(n) {
  const view = JSON.parse(sh('gh', ['pr', 'view', String(n), '--json', 'number,title,body,state']))
  const paths = sh('gh', [
    'api',
    '--paginate',
    `repos/{owner}/{repo}/pulls/${n}/files?per_page=100`,
    '--jq',
    '.[].filename',
  ])
    .split('\n')
    .filter(Boolean)
  return { number: n, title: view.title, state: view.state, paths, rows: rowsOf(view.body ?? '') }
}

function main() {
  const args = parseArgs(process.argv.slice(2))
  const errors = []
  let results = []
  let source = 'nothing'
  try {
    const sel = selectPrs(args, errors)
    source = sel.source
    for (const n of sel.prs) {
      try {
        results.push(assess(readPr(n)))
      } catch (e) {
        errors.push(`#${n}: ${e.message}`)
      }
    }
  } catch (e) {
    errors.push(e.message)
  }
  if (args.json) {
    process.stdout.write(JSON.stringify({ source, errors, results }, null, 2) + '\n')
  } else {
    process.stdout.write(report(results, { source, errors }) + '\n')
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    main()
  } catch (e) {
    process.stdout.write(`COULD NOT EVALUATE: ${e instanceof Error ? e.message : String(e)}\n`)
  }
  process.exit(0)
}
