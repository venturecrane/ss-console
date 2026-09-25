#!/usr/bin/env node
/**
 * Session board -- the live shared state 4-8 concurrent sessions lack.
 *
 * Why (2026-08-01 autopsy): two sessions built ADR 0083 blind to each other;
 * two reprovisioned the same seat within seconds; the repo-visibility answer
 * was re-derived per session for five hours. Peers were visible once at
 * session start (session-peers.sh) and then went dark. This board keeps them
 * visible EVERY TURN via reflex-primer.
 *
 * Design (per the 2026-08-01 critique consensus):
 *   - One JSON record per WORKTREE (one session per worktree is the model;
 *     the key is derivable identically by the agent-side helper and the
 *     hook-side primer, with no session-id discovery problem).
 *   - Atomic writes: temp + rename (APFS rename is atomic).
 *   - Liveness: the primer refreshes its own record's `updated` each turn,
 *     so age is the primary signal. Records older than MAX_AGE_H are pruned.
 *     Pid DEATH is treated as evidence (prune); pid LIFE is not trusted
 *     (macOS reuses pids).
 *   - NO collision matcher: two sessions rarely type the same focus string,
 *     so string-equality detects only coordinated (safe) cases. The primer
 *     prints every peer's mission/focus line; the model judges overlap --
 *     the evidence is in context every turn.
 *
 * CLI:
 *   board.mjs refresh <worktree-root>   touch own record, print own [mission] line
 *   board.mjs peers   <worktree-root>   print peer lines, prune the dead
 *   board.mjs set     <worktree-root> --mission "..." [--focus "..."]
 *                                       [--pid N] [--session ID]
 *
 * Env: SS_BOARD_DIR (default ~/.claude/ss-board). Exit 0 always.
 */
import { readFileSync, writeFileSync, readdirSync, renameSync, mkdirSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { execSync } from 'node:child_process'

const MAX_AGE_H = 24

function boardDir() {
  return process.env.SS_BOARD_DIR || join(homedir(), '.claude', 'ss-board')
}

/** Same derivation on the agent side and the hook side: key by worktree. */
function keyFor(root) {
  let h = 5381
  for (const c of root) h = ((h * 33) ^ c.charCodeAt(0)) >>> 0
  return `wt-${h.toString(16)}.json`
}

function readRecord(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return null
  }
}

function writeRecord(path, record) {
  const tmp = path + '.tmp.' + process.pid
  writeFileSync(tmp, JSON.stringify(record))
  renameSync(tmp, path) // atomic on APFS
}

function pidDead(pid) {
  if (!pid) return false // absence of a pid is not evidence of death
  try {
    process.kill(pid, 0)
    return false // alive or EPERM -- either way, not provably dead
  } catch (e) {
    return e.code === 'ESRCH'
  }
}

function branchOf(root) {
  try {
    return execSync(`git -C "${root}" rev-parse --abbrev-ref HEAD`, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch {
    return ''
  }
}

function focusSuffix(rec) {
  return rec.focus ? ` (focus: ${rec.focus})` : ''
}

function cmdSet(own, root, rest) {
  const args = {}
  for (let i = 0; i < rest.length; i += 2) args[rest[i]?.replace(/^--/, '')] = rest[i + 1]
  const rec = readRecord(own) || {}
  writeRecord(own, {
    ...rec,
    worktree: root,
    branch: branchOf(root) || rec.branch || '',
    pid: args.pid ? Number(args.pid) : rec.pid,
    session_id: args.session || rec.session_id,
    mission: args.mission ?? rec.mission ?? '',
    focus: args.focus ?? rec.focus ?? '',
    updated: new Date().toISOString(),
  })
}

function cmdRefresh(own) {
  const rec = readRecord(own)
  if (!rec) return
  writeRecord(own, { ...rec, updated: new Date().toISOString() })
  if (rec.mission) {
    process.stdout.write(`[mission] ${rec.mission}${focusSuffix(rec)} -- if the next action does not serve this, stop.\n`)
  }
}

/** Stale by age, unparseable age, or a provably dead pid. */
function isStale(rec) {
  const ageH = (Date.now() - Date.parse(rec.updated || 0)) / 3600000
  return !Number.isFinite(ageH) || ageH > MAX_AGE_H || pidDead(rec.pid)
}

function prune(path) {
  try {
    unlinkSync(path)
  } catch {
    /* concurrent prune -- fine */
  }
}

/** One peer's board line, or null when the record is absent or pruned. */
function peerLine(path) {
  const rec = readRecord(path)
  if (!rec) return null
  if (isStale(rec)) {
    prune(path)
    return null
  }
  const name = (rec.worktree || '?').split('/').pop()
  const what = rec.mission || '(no mission set)'
  return `  ${name}${rec.branch ? ` [${rec.branch}]` : ''}: ${what}${focusSuffix(rec)}`
}

function cmdPeers(dir, own) {
  const lines = []
  for (const f of readdirSync(dir)) {
    if (!f.startsWith('wt-') || !f.endsWith('.json')) continue
    const path = join(dir, f)
    if (path === own) continue
    const line = peerLine(path)
    if (line) lines.push(line)
  }
  if (lines.length > 0) {
    process.stdout.write(
      `[board] Live peer sessions -- if your work overlaps one of these, stop and surface it before building:\n${lines.join('\n')}\n`,
    )
  }
}

function main() {
  const [cmd, root, ...rest] = process.argv.slice(2)
  if (!cmd || !root) return
  const dir = boardDir()
  mkdirSync(dir, { recursive: true })
  const own = join(dir, keyFor(root))

  if (cmd === 'set') cmdSet(own, root, rest)
  else if (cmd === 'refresh') cmdRefresh(own)
  else if (cmd === 'peers') cmdPeers(dir, own)
}

try {
  main()
} catch {
  /* the board is a diagnostic; it must never break a hook or a turn */
}
process.exit(0)
