/**
 * The overlay sync guard never eats someone else's uncommitted work.
 *
 * `sync-overlay-checkout.sh` exists because on 2026-08-22 the sibling
 * hermes-smd-overlay checkout was found 84 commits behind origin/main with four
 * dirty paths, while four of five live sessions were doing overlay work. It is
 * NOT a build risk (the image installs the overlay from remote git at the pinned
 * SHA, operator/templates/Dockerfile:907) — it is a READ hazard: an agent that
 * opens a file on disk there answers confidently from a stale revision.
 *
 * The load-bearing assertion in this file is the refusal, not the sync. A
 * fast-forward that runs on a dirty tree silently destroys whatever is
 * uncommitted in it, and uncommitted work in a SHARED checkout belongs to
 * whoever left it. On the day this hook was written, the dirty tree held 85
 * substantive lines that were not on origin/main. Had the hook synced it, that
 * work would have been gone with no reflog entry naming it.
 *
 * So: dirty means report and change NOTHING. That case is asserted by comparing
 * HEAD and the porcelain count either side of the run, which is the only check
 * that can actually fail if the refusal regresses.
 *
 * GIT_* IS STRIPPED FROM EVERY CHILD. `cwd` does not isolate a git subprocess:
 * GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE win over it, so a fixture that omits
 * the strip measures the real repository instead of the fixture.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const HOOK = join(REPO_ROOT, '.claude', 'hooks', 'sync-overlay-checkout.sh')

/** Env with every GIT_* removed, so a child git sees only its own cwd. */
function cleanEnv(overlayDir: string): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {}
  for (const [k, v] of Object.entries(process.env)) {
    if (!k.startsWith('GIT_')) env[k] = v
  }
  env.SS_OVERLAY_DIR = overlayDir
  return env
}

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', args, { cwd, env: cleanEnv(cwd), encoding: 'utf8' }).trim()
}

function runHook(overlayDir: string): { out: string; code: number } {
  try {
    const out = execFileSync('bash', [HOOK], {
      env: cleanEnv(overlayDir),
      encoding: 'utf8',
    })
    return { out, code: 0 }
  } catch (e) {
    const err = e as { stdout?: string; status?: number }
    return { out: err.stdout ?? '', code: err.status ?? 1 }
  }
}

/** An origin repo with 3 commits, plus a clone positioned 2 behind it. */
function makeFixture(): { dir: string; origin: string; clone: string } {
  const dir = mkdtempSync(join(tmpdir(), 'overlay-sync-'))
  const origin = join(dir, 'origin')
  execFileSync('git', ['init', '-q', '--initial-branch=main', origin], { env: cleanEnv(dir) })
  git(origin, 'config', 'user.email', 'test@example.com')
  git(origin, 'config', 'user.name', 'test')
  for (let i = 1; i <= 3; i++) {
    writeFileSync(join(origin, `f${i}.txt`), `content ${i}\n`)
    git(origin, 'add', '.')
    git(origin, 'commit', '-q', '-m', `commit ${i}`)
  }
  const clone = join(dir, 'clone')
  execFileSync('git', ['clone', '-q', origin, clone], { env: cleanEnv(dir) })
  git(clone, 'config', 'user.email', 'test@example.com')
  git(clone, 'config', 'user.name', 'test')
  git(clone, 'reset', '-q', '--hard', 'HEAD~2')
  return { dir, origin, clone }
}

let fixture: { dir: string; origin: string; clone: string } | null = null
afterEach(() => {
  if (fixture) rmSync(fixture.dir, { recursive: true, force: true })
  fixture = null
})

describe('sync-overlay-checkout.sh', () => {
  it('refuses to touch a dirty checkout, and says so', () => {
    fixture = makeFixture()
    const { clone } = fixture
    writeFileSync(join(clone, 'f1.txt'), 'SOMEONE UNCOMMITTED WORK\n')

    const headBefore = git(clone, 'rev-parse', 'HEAD')
    const dirtyBefore = git(clone, 'status', '--porcelain')
    const { out, code } = runHook(clone)
    const headAfter = git(clone, 'rev-parse', 'HEAD')
    const dirtyAfter = git(clone, 'status', '--porcelain')

    // The assertion that can actually fail if the refusal regresses.
    expect(headAfter).toBe(headBefore)
    expect(dirtyAfter).toBe(dirtyBefore)
    expect(git(clone, 'show', 'HEAD:f1.txt')).not.toContain('SOMEONE UNCOMMITTED')
    expect(out).toContain('DIRTY')
    expect(code).toBe(0)
  })

  it('fast-forwards a clean checkout that is behind main', () => {
    fixture = makeFixture()
    const { clone } = fixture
    expect(Number(git(clone, 'rev-list', '--count', 'HEAD..origin/main'))).toBeGreaterThan(0)

    const { out, code } = runHook(clone)

    expect(Number(git(clone, 'rev-list', '--count', 'HEAD..origin/main'))).toBe(0)
    expect(out).toContain('fast-forwarded')
    expect(code).toBe(0)
  })

  it('stays silent when the checkout is already current', () => {
    fixture = makeFixture()
    const { clone } = fixture
    git(clone, 'merge', '--ff-only', '-q', 'origin/main')

    const { out, code } = runHook(clone)

    expect(out.trim()).toBe('')
    expect(code).toBe(0)
  })

  it('warns about a stale feature branch without moving it', () => {
    fixture = makeFixture()
    const { clone } = fixture
    git(clone, 'checkout', '-q', '-b', 'someones-work')
    const headBefore = git(clone, 'rev-parse', 'HEAD')

    const { out, code } = runHook(clone)

    expect(git(clone, 'rev-parse', 'HEAD')).toBe(headBefore)
    expect(git(clone, 'branch', '--show-current')).toBe('someones-work')
    expect(out).toContain('behind')
    expect(code).toBe(0)
  })

  it('is silent and exits 0 when the overlay is not cloned here', () => {
    fixture = makeFixture()
    const { out, code } = runHook(join(fixture.dir, 'does-not-exist'))
    expect(out.trim()).toBe('')
    expect(code).toBe(0)
  })

  it('is registered as a SessionStart hook', async () => {
    const { readFileSync } = await import('node:fs')
    const settings = JSON.parse(
      readFileSync(join(REPO_ROOT, '.claude', 'settings.json'), 'utf8')
    ) as { hooks?: { SessionStart?: { hooks?: { command?: string }[] }[] } }
    const commands = (settings.hooks?.SessionStart ?? []).flatMap((g) =>
      (g.hooks ?? []).map((h) => h.command ?? '')
    )
    expect(commands.some((c) => c.includes('sync-overlay-checkout.sh'))).toBe(true)
  })
})
