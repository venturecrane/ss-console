/**
 * Guard test for the sprint-worker completion signal (result.json).
 *
 * Why this exists (2026-08-19, the ten-orphan worktree cleanup): a stale
 * result.json from the #93 Forme WASM PDF spike sat TRACKED at the repo root
 * while the parallel-worker harness uses that same filename as its per-run
 * completion signal. Every sprint worker writes result.json to its worktree
 * root (.claude/agents/sprint-worker.md step 7) and the orchestrator reads it
 * for success/failure/crash detection (.claude/commands/orchestrate.md).
 *
 * The collision was silent and structural: because the file was tracked, each
 * worker's write registered as a modification to a tracked file, so every
 * finished worktree reported dirty. crane_worktree_doctor's safety gates then
 * correctly refused to remove any of them, and ten orphans accumulated across
 * two days until they were cleaned by hand. The gate was working; it was being
 * fed junk. Untracking the artifact and ignoring the path fixes the class.
 *
 * This is a repo-hygiene invariant, not a doctrine law: per-run state must
 * never be repo content, or the isolation machinery cannot tell finished work
 * from unfinished work.
 *
 * @see .claude/agents/sprint-worker.md
 * @see .claude/commands/orchestrate.md
 */
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'

const SIGNAL = 'result.json'

function git(args: string[]): { code: number; out: string } {
  try {
    return { code: 0, out: execFileSync('git', args, { encoding: 'utf8' }) }
  } catch (err) {
    const e = err as { status?: number; stdout?: string }
    return { code: e.status ?? 1, out: e.stdout ?? '' }
  }
}

describe('sprint-worker completion signal stays out of the repo', () => {
  it('result.json is not tracked at the repo root', () => {
    const { out } = git(['ls-files', '--', SIGNAL])
    expect(
      out.trim(),
      `${SIGNAL} is tracked again. A tracked signal file makes every finished ` +
        `sprint-worker worktree read as dirty, which blocks automatic orphan ` +
        `cleanup. Remove it with: git rm --cached ${SIGNAL}`
    ).toBe('')
  })

  it('a root result.json is gitignored, so a worker write never dirties the tree', () => {
    // check-ignore exits 0 when the path IS ignored, 1 when it is not.
    const { code } = git(['check-ignore', '--quiet', '--', SIGNAL])
    expect(
      code,
      `${SIGNAL} is no longer ignored. Restore the /result.json entry in ` +
        `.gitignore, or parallel sprint workers will leave every worktree dirty.`
    ).toBe(0)
  })
})
