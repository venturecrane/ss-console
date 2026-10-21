#!/usr/bin/env node
/**
 * PreToolUse guard: the primary checkout is read-only for agent sessions.
 *
 * Sessions work in isolated worktrees under .claude/worktrees/ (EnterWorktree).
 * This hook rejects Edit/Write/NotebookEdit calls that target the primary
 * checkout, so shipped-and-forgotten residue can never accumulate there again
 * (the 46-dirty-file / 87-behind state found 2026-07-06). Everything under
 * .claude/ is exempt: the worktrees themselves live there, along with
 * handoff.md, session markers, and local settings.
 *
 * Scope: only this repository. Writes to other repos or non-repo paths pass.
 * Escape hatch (Captain only): SS_ALLOW_PRIMARY_WRITES=1.
 *
 * Failure posture: parse/lookup errors fail OPEN. This is workflow hygiene,
 * not a safety gate; a broken guard must not brick every session's edits, and
 * a bypass shows up as a dirty tree in the next /sos briefing. The one
 * exception is the guard never getting to decide: settings.json registers it
 * with onFailure "block", so a script that cannot start (node missing, file
 * gone, syntax error) or hits its timeout blocks the edit instead of passing
 * it silently. Errors the script catches still exit 0.
 *
 * Exit 2 blocks the tool call and surfaces stderr to the model.
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

/** Root of the PRIMARY checkout owning `dir` (worktrees resolve to their parent repo). */
function primaryRootOf(dir) {
  const out = execFileSync('git', ['-C', dir, 'rev-parse', '--git-common-dir'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim()
  const commonDir = path.isAbsolute(out) ? out : path.resolve(dir, out)
  return fs.realpathSync(path.dirname(commonDir))
}

/** Deepest existing directory at or above `p` (Write targets may not exist yet). */
function nearestExistingDir(p) {
  let dir = p
  while (!fs.existsSync(dir)) {
    const parent = path.dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
  return fs.statSync(dir).isDirectory() ? dir : path.dirname(dir)
}

try {
  if (process.env.SS_ALLOW_PRIMARY_WRITES === '1') process.exit(0)

  const payload = JSON.parse(fs.readFileSync(0, 'utf8'))
  const target = payload?.tool_input?.file_path ?? payload?.tool_input?.notebook_path
  if (typeof target !== 'string' || target.length === 0) process.exit(0)

  const cwd = payload.cwd || process.cwd()
  const abs = path.resolve(cwd, target)
  const anchor = nearestExistingDir(abs)
  if (!anchor) process.exit(0)

  let targetRoot
  try {
    targetRoot = primaryRootOf(anchor)
  } catch {
    process.exit(0) // target is not inside any git repo
  }

  let sessionRoot
  try {
    sessionRoot = primaryRootOf(process.env.CLAUDE_PROJECT_DIR || cwd)
  } catch {
    process.exit(0)
  }

  if (targetRoot !== sessionRoot) process.exit(0) // other repos are out of scope

  // Rebuild the target path on realpath'd ancestors so symlinked prefixes
  // (e.g. /tmp -> /private/tmp on macOS) compare correctly against the root.
  const real = path.join(fs.realpathSync(anchor), path.relative(anchor, abs))
  const rel = path.relative(targetRoot, real)
  if (rel.startsWith('..')) process.exit(0) // outside the primary root
  if (rel === '.claude' || rel.startsWith(`.claude${path.sep}`)) process.exit(0)

  process.stderr.write(
    `worktree-guard: "${rel}" is in the PRIMARY checkout, which is read-only for agent sessions. ` +
      'All repo mutations happen in an isolated worktree: call EnterWorktree, then re-apply this ' +
      "change against the worktree path. See CLAUDE.md 'Worktree discipline'. " +
      '(Captain-only escape hatch: SS_ALLOW_PRIMARY_WRITES=1.)\n'
  )
  process.exit(2)
} catch {
  process.exit(0) // fail open (see header)
}
