#!/usr/bin/env node
/**
 * bash-log.mjs -- PostToolUse hook on Bash. LOG-ONLY: it never blocks, never
 * prints to the agent, and exits 0 on every path.
 *
 * Two logs, both decided in wave 2 (2026-11-09) as measurements that must run
 * on real traffic before anything is allowed to act on them:
 *
 *  1. LONG CALLS (Q8). A Bash call whose `duration_ms` is over 240 seconds is
 *     appended to $SS_BOARD_DIR/long-cmd.log with the command's first 80
 *     characters, whether it ran in the background, and whether it ran inside
 *     a subagent (`agent_id` present). The replay that motivated it: 630 of
 *     48,924 Bash calls ran over 270s, 181 of them `until` loops and 80
 *     `sleep`s, each one holding a long context idle. Nothing is denied until
 *     this log, and a cache-cost comparison, show that a deny saves more than
 *     it misfires. No `duration_ms` in the payload means no entry: a guess
 *     would be a number this log did not observe.
 *
 *  2. WHAT BUILT EACH PR. On a successful `gh pr create`, the PR URL is read
 *     from the tool's stdout and one line is appended to
 *     $SS_BOARD_DIR/built-by.jsonl:
 *       {ts, pr, repo, session_id, agent, auto_build, wired, plan, critique}
 *     Each flag is read from the session's transcripts, never from the agent's
 *     account: a Skill call (or a typed /command) for auto-build, wired and
 *     critique, and an ExitPlanMode call for plan. The scan covers the main
 *     transcript and every subagent transcript of the session, because the
 *     lead invokes /auto-build and a builder subagent opens the PR. A
 *     subagent transcript is read at most once: its flags are cached in a
 *     marker under $SS_BOARD_DIR/built-by-scan/<session>/. `npm run rework`
 *     reads this file to label each feature PR by the process that built it.
 *     Nothing goes into a PR body; the repo is public.
 *
 * Registered without `onFailure: block`, deliberately: a log that fails must
 * cost nothing. tests/hooks-registered.test.ts pins that.
 *
 * @see tests/bash-log.test.ts
 */

import {
  appendFileSync,
  createReadStream,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { basename, dirname, join } from 'node:path'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'

export const LONG_MS = 240_000
const CMD_CHARS = 80
const SKILLS = { 'auto-build': 'auto_build', wired: 'wired', critique: 'critique' }
const PR_CREATE = /(^|[\s;&|(])gh\s+pr\s+create\b/
const PR_URL = /https:\/\/github\.com\/([\w.-]+\/[\w.-]+)\/pull\/(\d+)/

function boardDir() {
  return process.env.SS_BOARD_DIR || join(homedir(), '.claude', 'ss-board')
}

function append(file, row) {
  const dir = boardDir()
  mkdirSync(dir, { recursive: true })
  appendFileSync(join(dir, file), `${JSON.stringify(row)}\n`)
}

/** The Bash tool's stdout, whatever shape the response arrives in. */
export function stdoutOf(response) {
  if (typeof response === 'string') return response
  if (response && typeof response === 'object') {
    if (typeof response.stdout === 'string') return response.stdout
    if (Array.isArray(response.content)) {
      return response.content.map((c) => (typeof c?.text === 'string' ? c.text : '')).join('\n')
    }
  }
  return ''
}

/** The long-call row, or null when the call was not observed to be long. */
export function longCallRow(payload, now = new Date()) {
  const ms = payload?.duration_ms
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= LONG_MS) return null
  const input = payload.tool_input ?? {}
  const cmd = typeof input.command === 'string' ? input.command : ''
  return {
    ts: now.toISOString(),
    session_id: typeof payload.session_id === 'string' ? payload.session_id : null,
    duration_ms: ms,
    cmd: cmd.slice(0, CMD_CHARS),
    run_in_background: input.run_in_background === true,
    subagent: typeof payload.agent_id === 'string' && payload.agent_id.length > 0,
  }
}

/** {repo, pr} for a successful `gh pr create`, else null. */
export function createdPr(payload) {
  const cmd = payload?.tool_input?.command
  if (typeof cmd !== 'string' || !PR_CREATE.test(cmd)) return null
  if (payload.tool_response?.interrupted === true) return null
  const m = PR_URL.exec(stdoutOf(payload.tool_response))
  return m ? { repo: m[1], pr: Number(m[2]) } : null
}

const LINE_NEEDLES = ['"name":"Skill"', '<command-name>/', '"name":"ExitPlanMode"']

function parseLine(line) {
  if (!LINE_NEEDLES.some((n) => line.includes(n))) return null
  try {
    return JSON.parse(line)
  } catch {
    return null
  }
}

/** A Skill or ExitPlanMode tool call. Mutates `flags`. */
function scanToolUse(c, flags) {
  if (c.name === 'ExitPlanMode') flags.plan = true
  if (c.name !== 'Skill' || typeof c.input?.skill !== 'string') return
  const key = SKILLS[c.input.skill.split(':').pop()]
  if (key) flags[key] = true
}

/** A /command typed by the Captain arrives as user text, not a Skill call. */
function scanTyped(text, flags) {
  for (const [name, key] of Object.entries(SKILLS)) {
    if (text.includes(`<command-name>/${name}</command-name>`)) flags[key] = true
  }
}

/** Flags one transcript line contributes. Mutates `flags`. */
export function scanLine(line, flags) {
  const o = parseLine(line)
  const content = o?.message?.content
  const isUser = o?.type === 'user'
  if (typeof content === 'string') {
    if (isUser) scanTyped(content, flags)
    return
  }
  if (!Array.isArray(content)) return
  for (const c of content) {
    if (c?.type === 'tool_use') scanToolUse(c, flags)
    else if (isUser && c?.type === 'text' && typeof c.text === 'string') scanTyped(c.text, flags)
  }
}

export function emptyFlags() {
  return { auto_build: false, wired: false, plan: false, critique: false }
}

async function scanFile(path, flags) {
  if (!existsSync(path)) return
  const rl = createInterface({ input: createReadStream(path), crlfDelay: Infinity })
  for await (const line of rl) scanLine(line, flags)
}

/**
 * The session's main transcript and its subagents directory, from the payload.
 * Inside a subagent the transcript path may be the subagent's own file
 * (<project>/<session>/subagents/agent-<id>.jsonl) or the main one; both are
 * resolved to the same pair.
 */
export function sessionPaths(transcriptPath, sessionId) {
  if (typeof transcriptPath !== 'string' || !transcriptPath) return null
  const sid = typeof sessionId === 'string' && sessionId ? sessionId : null
  let projectDir
  if (basename(dirname(transcriptPath)) === 'subagents') {
    projectDir = dirname(dirname(dirname(transcriptPath)))
  } else {
    projectDir = dirname(transcriptPath)
  }
  const session = sid ?? basename(transcriptPath).replace(/\.jsonl$/, '')
  return {
    main: join(projectDir, `${session}.jsonl`),
    subagents: join(projectDir, session, 'subagents'),
    session,
  }
}

/** OR together the flags of the main transcript and every subagent transcript. */
export async function sessionFlags(transcriptPath, sessionId) {
  const flags = emptyFlags()
  const paths = sessionPaths(transcriptPath, sessionId)
  if (!paths) return flags
  await scanFile(paths.main, flags)
  if (!existsSync(paths.subagents)) return flags
  const markers = join(boardDir(), 'built-by-scan', paths.session)
  mkdirSync(markers, { recursive: true })
  for (const name of readdirSync(paths.subagents)) {
    if (!name.endsWith('.jsonl')) continue
    const marker = join(markers, `${name}.json`)
    let own
    if (existsSync(marker)) {
      try {
        own = JSON.parse(readFileSync(marker, 'utf8'))
      } catch {
        own = null
      }
    }
    if (!own) {
      own = emptyFlags()
      await scanFile(join(paths.subagents, name), own)
      writeFileSync(marker, JSON.stringify(own))
    }
    for (const k of Object.keys(flags)) flags[k] = flags[k] || own[k] === true
  }
  return flags
}

export async function handle(payload, now = new Date()) {
  const long = longCallRow(payload, now)
  if (long) append('long-cmd.log', long)
  const created = createdPr(payload)
  if (created) {
    const flags = await sessionFlags(payload.transcript_path, payload.session_id)
    append('built-by.jsonl', {
      ts: now.toISOString(),
      pr: created.pr,
      repo: created.repo,
      session_id: typeof payload.session_id === 'string' ? payload.session_id : null,
      agent: typeof payload.agent_id === 'string' && payload.agent_id.length > 0,
      ...flags,
    })
  }
}

async function readStdin() {
  const chunks = []
  for await (const c of process.stdin) chunks.push(c)
  return Buffer.concat(chunks).toString('utf8')
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    await handle(JSON.parse((await readStdin()) || '{}'))
  } catch {
    /* log-only: never surface anything */
  }
  process.exit(0)
}
