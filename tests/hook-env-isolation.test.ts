/**
 * The pre-push limits tests must not write the index of the repo being pushed.
 *
 * WHY. Git exports GIT_DIR and GIT_INDEX_FILE to every hook, and those win over
 * `cwd`. On 2026-11-21 `tests/operator-module-size.test.ts` built a scratch
 * repo with `git init` / `git add -A` in a temp dir while inheriting them from
 * .husky/pre-push, so the push replaced the pushing worktree's ~6,800-entry
 * index with one fixture file, and the census tests after it failed every
 * push. This test re-runs the hook's limits files the way the hook does, but
 * with GIT_DIR and GIT_INDEX_FILE pointing at a throwaway repo, and fails if
 * that repo's index changed. The children's own pass or fail is not asserted:
 * pointed at a one-file repo, a census is expected to fail.
 */
import { describe, it, expect } from 'vitest'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const REPO_ROOT = resolve(__dirname, '..')
const LIMITS_TESTS = [
  'tests/operator-module-size.test.ts',
  'tests/sprawl-ratchet.test.ts',
  'tests/text-pin-ratchet.test.ts',
]

const CLEAN_ENV = Object.fromEntries(
  Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
)

describe('limits tests under a hook environment', () => {
  it('leave the GIT_DIR / GIT_INDEX_FILE repo they inherit untouched', () => {
    const throwaway = mkdtempSync(join(tmpdir(), 'hook-env-sentinel-'))
    try {
      const git = (...args: string[]) =>
        execFileSync('git', ['-c', 'core.hooksPath=/dev/null', ...args], {
          cwd: throwaway,
          env: CLEAN_ENV,
          encoding: 'utf8',
        })
      git('init', '-q')
      writeFileSync(join(throwaway, 'sentinel.txt'), 'sentinel\n')
      git('add', 'sentinel.txt')
      const gitDir = join(throwaway, '.git')
      const indexFile = join(gitDir, 'index')
      const before = readFileSync(indexFile).toString('base64')

      const run = spawnSync(
        join(REPO_ROOT, 'node_modules', '.bin', 'vitest'),
        ['run', ...LIMITS_TESTS],
        {
          cwd: REPO_ROOT,
          env: { ...CLEAN_ENV, GIT_DIR: gitDir, GIT_INDEX_FILE: indexFile },
          encoding: 'utf8',
        }
      )
      // The children ran at all (a missing binary would make this vacuous).
      expect(run.error).toBeUndefined()
      expect(run.stdout + run.stderr).toMatch(/Test Files/)

      expect(git('ls-files').trim().split('\n')).toEqual(['sentinel.txt'])
      expect(readFileSync(indexFile).toString('base64')).toBe(before)
    } finally {
      rmSync(throwaway, { recursive: true, force: true })
    }
  }, 120_000)
})
