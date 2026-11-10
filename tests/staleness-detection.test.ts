/**
 * The primer keeps speaking, and it speaks about the right tree (Law 10).
 *
 * `reflex-primer.sh` is the always-on doctrine surface: its stdout is injected
 * into context on every user prompt. Two facts make it unusually dangerous to
 * extend, and both are the reason this file exists.
 *
 * FIRST: it runs `set -e`. The staleness block added for Law 10 stats files
 * that are routinely absent (`node_modules/.package-lock.json` is missing on
 * any fresh clone, which is precisely the case it exists to report). An
 * unguarded failure there does not degrade the primer, it DELETES it, and
 * `tests/doctrine-integrity.test.ts` cannot see that: it reads the script as
 * text and never executes it, so every law would still "appear in the primer"
 * while no law reached a single turn.
 *
 * SECOND: on `UserPromptSubmit`, exit code 2 erases the user's prompt outright.
 * A crash in a diagnostic block must never cost the Captain their message.
 *
 * So the load-bearing assertion here is not that the new signals work. It is
 * that every doctrine `primer_line` still reaches STDOUT, and the exit code is
 * still 0, under every malformed input and missing file we could construct.
 * The signals are tested too, but they are the smaller half.
 *
 * GIT_* IS STRIPPED FROM EVERY CHILD. `cwd` does not isolate a git subprocess:
 * `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` win over it, and git hooks
 * export them. The hook runs `git rev-parse`, `git rev-list`, and `git status`;
 * a fixture that omits the strip measures the real repository instead of the
 * fixture, which is the same class of bug Law 10 is about.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { parse as parseYaml } from 'yaml'
import { afterEach, describe, expect, it } from 'vitest'

import { readFileSync } from 'node:fs'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const HOOK = join(REPO_ROOT, '.claude', 'hooks', 'reflex-primer.sh')
const DOCTRINE = join(REPO_ROOT, 'docs', 'doctrine', 'agent-operating-doctrine.md')

/** Every INJECTED primer_line (tier primer/radar) in a doctrine document.
 *  Gate-tier laws are compressed to a pointer line since the 2026-08-01
 *  consolidation and are pinned by id in doctrine-integrity, not asserted
 *  line-by-line here. */
function injectedLines(doctrine: string): string[] {
  return [...doctrine.matchAll(/```yaml\n([\s\S]*?)```/g)]
    .map((m) => parseYaml(m[1]) as Record<string, unknown>)
    .filter(
      (d): d is { primer_line: string; tier: string } =>
        typeof d?.primer_line === 'string' && (d?.tier === 'primer' || d?.tier === 'radar')
    )
    .map((d) => d.primer_line)
}

const PRIMER_LINES: string[] = injectedLines(readFileSync(DOCTRINE, 'utf8'))

/**
 * The same laws as the doctrine on `origin/main`, when that ref exists here.
 *
 * The primer serves doctrine from `origin/main` whenever it parses, and that
 * is deliberate: a law merged to main must reach every session's NEXT turn,
 * and both the primary checkout and every worktree predate any given merge.
 * The consequence is that the WORKING TREE is legitimately ahead of what the
 * primer emits for the whole life of any branch that adds or edits a law.
 *
 * Asserting the primer's origin/main output against the working tree's law set
 * failed that expected state as if it were a crash, and did so ONLY on machines
 * that have an origin/main ref: CI runs `actions/checkout` at its shallow,
 * single-branch default, so `git show origin/main:` fails there, the primer
 * falls back to its heredoc, and the heredoc always matched. Green in CI, red
 * on every developer machine carrying an unmerged doctrine change, which is the
 * exact inversion of "works on my machine" and is worth more than the two lines
 * it takes to fix (Law 12: this check was answering a question about doctrine
 * freshness while claiming to answer one about crash resilience).
 */
const ORIGIN_PRIMER_LINES: string[] = (() => {
  try {
    return injectedLines(
      execFileSync('git', ['show', 'origin/main:docs/doctrine/agent-operating-doctrine.md'], {
        cwd: REPO_ROOT,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
      })
    )
  } catch {
    return PRIMER_LINES // no origin/main here (a CI checkout): the primer uses its heredoc
  }
})()

/** The primer labels origin/main-served output; anything else is the heredoc. */
function expectedLinesFor(out: string): string[] {
  return out.includes('(origin/main @') ? ORIGIN_PRIMER_LINES : PRIMER_LINES
}

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})

function cleanEnv(extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...Object.fromEntries(
      Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
    ),
    ...extra,
  }
}

function git(dir: string, ...args: string[]): void {
  execFileSync('git', ['-C', dir, ...args], { env: cleanEnv(), stdio: 'pipe' })
}

interface TreeOpts {
  /** Versions written into package-lock.json */
  lock?: Record<string, string>
  /** Versions written into node_modules/.package-lock.json. Omit for no record. */
  installed?: Record<string, string> | null
  /** Create node_modules at all. */
  nodeModules?: boolean
  /** Write a package-lock.json at all. */
  lockfile?: boolean
  /** Make package-lock.json appear newer than the install record. */
  lockNewer?: boolean
  /** Extra commits on origin/main beyond HEAD. */
  aheadBy?: number
  /** Paths written in the baseline commit, so a later write is a change, not an add. */
  basePaths?: string[]
  /** Paths the FIRST commit ahead on origin/main writes (default extra-0.txt). */
  aheadPaths?: string[]
  /** Paths a commit on HEAD's own side writes after the split (a branch's own edit). */
  branchPaths?: string[]
}

function makeTree(opts: TreeOpts = {}): string {
  const {
    lock = { astro: '7.1.3' },
    installed = { astro: '7.1.3' },
    nodeModules = true,
    lockfile = true,
    lockNewer = false,
    aheadPaths,
    branchPaths,
    basePaths,
  } = opts
  const aheadBy = opts.aheadBy ?? (aheadPaths ? 1 : 0)

  const root = mkdtempSync(join(tmpdir(), 'staleness-'))
  scratch.push(root)

  const pkgs = (v: Record<string, string>) =>
    Object.fromEntries(Object.entries(v).map(([n, ver]) => [`node_modules/${n}`, { version: ver }]))

  writeFileSync(join(root, 'package.json'), JSON.stringify({ name: 'fixture' }, null, 2))
  if (lockfile) {
    writeFileSync(
      join(root, 'package-lock.json'),
      JSON.stringify({ lockfileVersion: 3, packages: pkgs(lock) }, null, 2)
    )
  }
  if (nodeModules) {
    mkdirSync(join(root, 'node_modules'), { recursive: true })
    if (installed) {
      writeFileSync(
        join(root, 'node_modules', '.package-lock.json'),
        JSON.stringify({ lockfileVersion: 3, packages: pkgs(installed) }, null, 2)
      )
    }
  }

  for (const rel of basePaths ?? []) {
    mkdirSync(join(root, rel, '..'), { recursive: true })
    writeFileSync(join(root, rel), 'base')
  }

  git(root, 'init', '--quiet', '--initial-branch=main')
  git(root, 'config', 'user.email', 'probe@example.invalid')
  git(root, 'config', 'user.name', 'probe')
  git(root, 'add', '-A')
  git(root, 'commit', '--quiet', '-m', 'baseline')
  git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')

  const writePaths = (paths: string[], body: string) => {
    for (const rel of paths) {
      mkdirSync(join(root, rel, '..'), { recursive: true })
      writeFileSync(join(root, rel), body)
    }
  }
  for (let i = 0; i < aheadBy; i++) {
    writePaths(i === 0 && aheadPaths ? aheadPaths : [`extra-${i}.txt`], `ahead ${i}`)
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', `ahead ${i}`)
  }
  if (aheadBy > 0) {
    // origin/main carries the extra commits; HEAD is moved back behind them.
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'reset', '--hard', '--quiet', `HEAD~${aheadBy}`)
  }

  if (branchPaths) {
    writePaths(branchPaths, 'branch edit')
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', 'branch edit')
  }

  if (lockNewer && lockfile) {
    const future = new Date(Date.now() + 60_000)
    utimesSync(join(root, 'package-lock.json'), future, future)
  }
  return root
}

interface Payload {
  prompt?: string
  cwd?: string
  session_id?: string
  transcript_path?: string
}

function runHook(
  payload: Payload | string,
  opts: { cwd?: string; env?: Record<string, string> } = {}
): { out: string; code: number } {
  const body = typeof payload === 'string' ? payload : JSON.stringify(payload)
  try {
    const out = execFileSync('bash', [HOOK], {
      input: body,
      cwd: opts.cwd ?? REPO_ROOT,
      env: cleanEnv({
        TMPDIR: mkdtempSync(join(tmpdir(), 'staleness-tmp-')),
        // Hermetic board: the primer's board_block would otherwise read AND
        // PRUNE the real ~/.claude/ss-board from inside fixture repos --
        // found live 2026-08-01 when a verify run pruned the running
        // session's own record.
        SS_BOARD_DIR: mkdtempSync(join(tmpdir(), 'staleness-board-')),
        ...(opts.env ?? {}),
      }),
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    return { out, code: 0 }
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; status?: number }
    return { out: `${e.stdout ?? ''}${e.stderr ?? ''}`, code: e.status ?? -1 }
  }
}

/** The invariant that matters more than any signal. */
function expectLawsIntact(res: { out: string; code: number }): void {
  expect(res.code).toBe(0)
  const expected = expectedLinesFor(res.out)
  // Guard the guard: an empty expectation set would make every case below pass
  // no matter what the primer printed.
  expect(expected.length).toBeGreaterThanOrEqual(5)
  const missing = expected.filter((l) => !res.out.includes(l))
  expect(missing).toEqual([])
}

describe('the primer survives everything the staleness block can hit', () => {
  it('sanity: the doctrine actually yielded primer lines to check', () => {
    expect(PRIMER_LINES.length).toBeGreaterThanOrEqual(5)
  })

  it('emits every law when the tree is entirely healthy', () => {
    expectLawsIntact(runHook({ prompt: 'hi', cwd: makeTree(), session_id: 's1' }))
  })

  it('survives a tree with no node_modules', () => {
    expectLawsIntact(
      runHook({ prompt: 'hi', cwd: makeTree({ nodeModules: false }), session_id: 's2' })
    )
  })

  it('survives node_modules with no install record (the set -e trap)', () => {
    expectLawsIntact(
      runHook({ prompt: 'hi', cwd: makeTree({ installed: null }), session_id: 's3' })
    )
  })

  it('survives a tree with no package-lock.json', () => {
    expectLawsIntact(
      runHook({ prompt: 'hi', cwd: makeTree({ lockfile: false }), session_id: 's4' })
    )
  })

  it('survives a cwd that does not exist', () => {
    expectLawsIntact(runHook({ prompt: 'hi', cwd: '/nonexistent/path/xyz', session_id: 's5' }))
  })

  it('survives a cwd that is not a git repository', () => {
    const d = mkdtempSync(join(tmpdir(), 'staleness-nogit-'))
    scratch.push(d)
    expectLawsIntact(runHook({ prompt: 'hi', cwd: d, session_id: 's6' }))
  })

  it('survives a payload with no .cwd', () => {
    expectLawsIntact(runHook({ prompt: 'hi', session_id: 's7' }))
  })

  it('survives a payload with no .session_id', () => {
    expectLawsIntact(runHook({ prompt: 'hi', cwd: makeTree() }))
  })

  it('survives malformed JSON without erasing the prompt (exit must not be 2)', () => {
    const res = runHook('{not json at all')
    expect(res.code).not.toBe(2)
  })

  it('still exits 0 on an empty payload', () => {
    expect(runHook('{}').code).toBe(0)
  })
})

describe('signal: main moved under you', () => {
  it('reports when origin/main is ahead', () => {
    const res = runHook({ prompt: 'hi', cwd: makeTree({ aheadBy: 3 }), session_id: 'm1' })
    expectLawsIntact(res)
    expect(res.out).toContain('origin/main is 3 commit(s) ahead')
  })

  it('stays silent when HEAD is level with origin/main', () => {
    const res = runHook({ prompt: 'hi', cwd: makeTree(), session_id: 'm2' })
    expect(res.out).not.toContain('ahead of your HEAD')
  })
})

describe('signal: dependencies drifted from the lockfile', () => {
  it('reports real version drift', () => {
    const root = makeTree({
      lock: { astro: '7.1.3' },
      installed: { astro: '6.4.8' },
      lockNewer: true,
    })
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'd1' })
    expectLawsIntact(res)
    expect(res.out).toContain('dependencies are stale')
    expect(res.out).toContain('astro 6.4.8 vs 7.1.3')
  })

  /**
   * The false-positive guard, and the reason mtime alone is not enough.
   *
   * `git stash`, `git restore`, a rebase, and a branch switch all rewrite
   * package-lock.json without changing its content. Observed live on
   * 2026-07-31: a worktree read 156 hours stale by mtime with zero version
   * mismatches. Speaking there demands a 90-second reinstall for nothing, and
   * a line that is sometimes wrong and always loud teaches agents to skim past
   * every law above it.
   */
  it('stays silent when mtime is stale but content matches', () => {
    const root = makeTree({
      lock: { astro: '7.1.3' },
      installed: { astro: '7.1.3' },
      lockNewer: true,
    })
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'd2' })
    expectLawsIntact(res)
    expect(res.out).not.toContain('dependencies are stale')
  })

  /** Absences are optional platform-specific binaries, not drift. Counting
   *  them would make this fire in every checkout on earth, forever. */
  it('does not count packages absent from the install record as drift', () => {
    const root = makeTree({
      lock: { astro: '7.1.3', 'satteri-linux-x64': '0.9.5' },
      installed: { astro: '7.1.3' },
      lockNewer: true,
    })
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'd3' })
    expect(res.out).not.toContain('dependencies are stale')
  })

  it('names a missing node_modules rather than guessing', () => {
    const res = runHook({ prompt: 'hi', cwd: makeTree({ nodeModules: false }), session_id: 'd4' })
    expect(res.out).toContain('no node_modules')
  })

  it('reports a possible in-flight install rather than "never installed"', () => {
    const res = runHook({ prompt: 'hi', cwd: makeTree({ installed: null }), session_id: 'd5' })
    expect(res.out).toContain('install may be in flight')
    expect(res.out).not.toContain('no node_modules')
  })
})

describe('it measures the tree the session is in, not the launch directory', () => {
  /**
   * Invariant 2. CLAUDE_PROJECT_DIR is pinned at launch and does not follow
   * EnterWorktree. Preferring it would give a worktree session a verdict about
   * the primary: this law's own failure mode, reintroduced by its enforcement,
   * wearing doctrine's authority.
   */
  it('prefers payload .cwd over CLAUDE_PROJECT_DIR and over process cwd', () => {
    const drifted = makeTree({
      lock: { astro: '7.1.3' },
      installed: { astro: '6.4.8' },
      lockNewer: true,
    })
    const healthy = makeTree()

    const res = runHook(
      { prompt: 'hi', cwd: drifted, session_id: 'r1' },
      { cwd: healthy, env: { CLAUDE_PROJECT_DIR: healthy } }
    )
    expect(res.out).toContain('dependencies are stale')
  })

  it('falls back to the process cwd when .cwd is absent', () => {
    const drifted = makeTree({
      lock: { astro: '7.1.3' },
      installed: { astro: '6.4.8' },
      lockNewer: true,
    })
    const res = runHook({ prompt: 'hi', session_id: 'r2' }, { cwd: drifted })
    expect(res.out).toContain('dependencies are stale')
  })
})

describe('signal: the working tree moved since you were briefed', () => {
  it('is silent on the first turn (it is establishing the baseline)', () => {
    const root = makeTree()
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-base-'))
    scratch.push(tmp)
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'w1' }, { env: { TMPDIR: tmp } })
    expect(res.out).not.toContain('working tree changed')
  })

  it('reports on a later turn once the dirty count has changed', () => {
    const root = makeTree()
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-base-'))
    scratch.push(tmp)

    runHook({ prompt: 'hi', cwd: root, session_id: 'w2' }, { env: { TMPDIR: tmp } })
    writeFileSync(join(root, 'appeared.txt'), 'new')
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'w2' }, { env: { TMPDIR: tmp } })

    expectLawsIntact(res)
    expect(res.out).toContain('working tree changed since session start')
  })

  it('stays silent when the tree has not moved', () => {
    const root = makeTree()
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-base-'))
    scratch.push(tmp)
    runHook({ prompt: 'hi', cwd: root, session_id: 'w3' }, { env: { TMPDIR: tmp } })
    const res = runHook({ prompt: 'hi', cwd: root, session_id: 'w3' }, { env: { TMPDIR: tmp } })
    expect(res.out).not.toContain('working tree changed')
  })
})

describe('signal: session tooling changed on main (Signal 1b)', () => {
  const SKILL = '.claude/skills/wired/SKILL.md'

  function hookIn(root: string, tmp: string, sid: string) {
    return runHook({ prompt: 'hi', cwd: root, session_id: sid }, { env: { TMPDIR: tmp } })
  }

  it('names a skills commit on origin/main that HEAD does not have', () => {
    const root = makeTree({ aheadPaths: [SKILL, 'src/a.ts'] })
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-tool-'))
    scratch.push(tmp)
    const res = hookIn(root, tmp, 't1')
    expectLawsIntact(res)
    expect(res.out).toContain('changed 1 skill/hook/settings path(s)')
    expect(res.out).toContain(SKILL)
    expect(res.out).not.toContain('src/a.ts')
    // The skill file is NEW on main here, so the line asks for a restart too.
    expect(res.out).toContain('restart the session')
    expect(res.out).toContain('(/wired)')
  })

  it('asks only for a merge when an existing skill changed, not a restart', () => {
    const root = makeTree({ basePaths: [SKILL], aheadPaths: [SKILL] })
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-tool-'))
    scratch.push(tmp)
    const res = hookIn(root, tmp, 't1b')
    expectLawsIntact(res)
    expect(res.out).toContain('merge origin/main to load them')
    expect(res.out).not.toContain('restart the session')
  })

  it('names at most three paths and counts the rest', () => {
    const paths = ['a', 'b', 'c', 'd', 'e'].map((n) => `.claude/hooks/${n}.mjs`)
    const res = hookIn(
      makeTree({ aheadPaths: [...paths, '.claude/settings.json'] }),
      mkdtempSync(join(tmpdir(), 'staleness-tool-')),
      't2'
    )
    expect(res.out).toContain('changed 6 skill/hook/settings path(s)')
    expect(res.out).toContain('+3 more')
    expect(res.out).not.toContain('d.mjs')
  })

  it('stays silent when origin/main is ahead only in src/', () => {
    const res = hookIn(
      makeTree({ aheadPaths: ['src/lib/x.ts'] }),
      mkdtempSync(join(tmpdir(), 'staleness-tool-')),
      't3'
    )
    expect(res.out).toContain('origin/main is 1 commit(s) ahead')
    expect(res.out).not.toContain('skill/hook/settings')
  })

  it("stays silent on the branch's OWN skill edit (three-dot diff, not two-dot)", () => {
    const res = hookIn(
      makeTree({ branchPaths: [SKILL] }),
      mkdtempSync(join(tmpdir(), 'staleness-tool-')),
      't4'
    )
    expect(res.out).not.toContain('skill/hook/settings')
  })

  it('speaks once per origin/main sha in a session, then again when main moves', () => {
    const root = makeTree({ aheadPaths: [SKILL] })
    const tmp = mkdtempSync(join(tmpdir(), 'staleness-tool-'))
    scratch.push(tmp)
    expect(hookIn(root, tmp, 't5').out).toContain('skill/hook/settings')
    expect(hookIn(root, tmp, 't5').out).not.toContain('skill/hook/settings')
    // A second session in the same tree has its own baseline.
    expect(hookIn(root, tmp, 't6').out).toContain('skill/hook/settings')
    // main moves again: the line returns once for the new sha.
    git(root, 'checkout', '--quiet', '-b', 'mainline', 'origin/main')
    writeFileSync(join(root, 'later.txt'), 'x')
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', 'later')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'checkout', '--quiet', 'main')
    expect(hookIn(root, tmp, 't5').out).toContain('skill/hook/settings')
    // Four primer runs plus git: over the 5s default under a full verify's load.
  }, 30_000)

  it('stays silent without a session id (no baseline to speak once against)', () => {
    const res = runHook({ prompt: 'hi', cwd: makeTree({ aheadPaths: [SKILL] }) })
    expectLawsIntact(res)
    expect(res.out).not.toContain('skill/hook/settings')
  })
})

describe('context size: logged, never printed', () => {
  it('appends one line to the board log and adds nothing to the primer output', () => {
    const board = mkdtempSync(join(tmpdir(), 'staleness-board-'))
    scratch.push(board)
    const t = join(board, 't.jsonl')
    writeFileSync(
      t,
      JSON.stringify({
        type: 'assistant',
        message: { usage: { input_tokens: 1, cache_read_input_tokens: 412_000 } },
      }) + '\n'
    )
    const res = runHook(
      { prompt: 'hi', cwd: makeTree(), session_id: 'c1', transcript_path: t },
      { env: { SS_BOARD_DIR: board } }
    )
    expectLawsIntact(res)
    expect(res.out).not.toContain('412001')
    const row = JSON.parse(readFileSync(join(board, 'context-size.log'), 'utf8').trim())
    expect(row).toMatchObject({ session_id: 'c1', tokens: 412_001, outcome: 'ok' })
  })
})
