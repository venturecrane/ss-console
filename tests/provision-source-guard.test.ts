/**
 * The build source must be the code you think it is (ss #2095).
 *
 * `provision-customer.sh` derives `REPO_ROOT` from its own location, so the
 * image is built from whichever checkout you invoked. That is easy to state and
 * easy to forget: runbooks, muscle memory, and shell history all point at
 * `~/dev/ss-console/operator/bin/reprovision.sh`, while an agent's verified work
 * usually sits in a worktree under `.claude/worktrees/`.
 *
 * WHAT IT COST (2026-07-31). The primary checkout sat two commits behind
 * origin/main carrying thirty staged entries that reverted a whole merged
 * programme, with `OVERLAY_REF` still on the previous pin. A reprovision in that
 * state builds an image containing none of the work, pins the wrong overlay, and
 * exits zero. Every observation taken afterwards is a true statement about the
 * wrong artifact — worse than a failure, because a failure gets investigated and
 * a green run gets believed.
 *
 * Each refusal arm is DRIVEN here rather than read, because a guard nobody has
 * watched refuse is a guard nobody knows the shape of.
 *
 * The guard runs before the script's R2-credential checks, so a fixture that
 * gets past it dies on a different, identifiable message. That distinction is
 * what lets the pass-through case assert "the guard allowed this" without
 * needing R2, `aws`, or `pbpaste`.
 *
 * GIT_* IS STRIPPED FROM EVERY CHILD. `cwd` does not isolate a git subprocess:
 * `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` win over it, and git hooks
 * export them. A fixture that omits this initialises and commits into the REAL
 * repository — which is how a branch got its index clobbered the same week this
 * guard was written.
 */
import { execFileSync } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SLUG = 'probe-seat'

/**
 * Env with every `GIT_*` and every `R2_*` removed.
 *
 * `GIT_*` for the reason in the header. `R2_*` for two reasons: the developer
 * shell often carries real credentials (via direnv or an `infisical run`
 * wrapper), which would let a fixture past the credential check and — worse —
 * run a provisioning script holding live credentials; and the R2 check is the
 * sentinel these tests use to mean "the guard allowed this", so it has to fail
 * deterministically rather than depending on whose shell is running.
 */
function cleanEnv(extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...Object.fromEntries(
      Object.entries({ ...process.env, HUSKY: '0' }).filter(
        ([k]) => !k.startsWith('GIT_') && !k.startsWith('R2_')
      )
    ),
    ...extra,
  }
}

function git(dir: string, ...args: string[]): void {
  execFileSync('git', ['-C', dir, ...args], { env: cleanEnv(), stdio: 'pipe' })
}

const PIN = 'a'.repeat(40)

/**
 * A checkout shaped like this repo, far enough for the guard to run: the real
 * script, a Dockerfile carrying a pin, a matching pair manifest, and a customer
 * directory. Nothing beyond the guard's reach is built.
 */
function makeCheckout(): { root: string; script: string } {
  const root = mkdtempSync(join(tmpdir(), 'provision-source-'))
  mkdirSync(join(root, 'operator', 'bin'), { recursive: true })
  mkdirSync(join(root, 'operator', 'templates'), { recursive: true })
  mkdirSync(join(root, 'operator', 'contracts'), { recursive: true })
  mkdirSync(join(root, 'operator', 'customers', SLUG), { recursive: true })

  cpSync(
    join(REPO_ROOT, 'operator', 'bin', 'provision-customer.sh'),
    join(root, 'operator', 'bin', 'provision-customer.sh')
  )
  // The guard itself lives in lib/build-source-guard.sh since the fleet release
  // (2026-10-19), shared with `fleet build`; provisioning sources it.
  mkdirSync(join(root, 'operator', 'bin', 'lib'), { recursive: true })
  cpSync(
    join(REPO_ROOT, 'operator', 'bin', 'lib', 'build-source-guard.sh'),
    join(root, 'operator', 'bin', 'lib', 'build-source-guard.sh')
  )
  writeFileSync(join(root, 'operator', 'templates', 'Dockerfile'), `ARG OVERLAY_REF="${PIN}"\n`)
  writeFileSync(
    join(root, 'operator', 'contracts', 'overlay-pairs.json'),
    `${JSON.stringify({ overlayRef: PIN, pairs: [] }, null, 2)}\n`
  )
  writeFileSync(
    join(root, 'operator', 'customers', SLUG, 'customer.yaml'),
    `customer_id: ${SLUG}\n`
  )

  git(root, 'init', '--quiet', '--initial-branch=main')
  git(root, 'config', 'user.email', 'probe@example.invalid')
  git(root, 'config', 'user.name', 'probe')
  git(root, 'add', '-A')
  git(root, 'commit', '--quiet', '-m', 'baseline')
  // A local `origin/main` so the behind-check has something to compare against
  // without any network.
  git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')

  return { root, script: join(root, 'operator', 'bin', 'provision-customer.sh') }
}

/** Run the script and return combined output plus whether it exited zero. */
function run(script: string, env: Record<string, string> = {}): { out: string; ok: boolean } {
  try {
    const out = execFileSync('bash', [script, SLUG], {
      env: cleanEnv(env),
      encoding: 'utf8',
      stdio: 'pipe',
    })
    return { out, ok: true }
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string }
    return { out: `${e.stdout ?? ''}${e.stderr ?? ''}`, ok: false }
  }
}

const checkouts: string[] = []
afterEach(() => {
  while (checkouts.length) rmSync(checkouts.pop()!, { recursive: true, force: true })
})

function checkout(): { root: string; script: string } {
  const made = makeCheckout()
  checkouts.push(made.root)
  return made
}

describe('fleet build runs the same guard, executed directly', () => {
  it('refuses a dirty tree and passes a clean one', () => {
    const { root } = checkout()
    const guard = join(root, 'operator', 'bin', 'lib', 'build-source-guard.sh')
    const clean = (() => {
      try {
        execFileSync('bash', [guard, root], { env: cleanEnv(), stdio: 'pipe' })
        return true
      } catch {
        return false
      }
    })()
    expect(clean).toBe(true)
    writeFileSync(join(root, 'stray.txt'), 'x')
    let refused = false
    try {
      execFileSync('bash', [guard, root], { env: cleanEnv(), stdio: 'pipe' })
    } catch {
      refused = true
    }
    expect(refused).toBe(true)
  })
})

describe('provision refuses a build source that is not what you think it is', () => {
  it('prints the resolved build source and pin on every run', () => {
    const { root, script } = checkout()
    const { out } = run(script)
    expect(out).toContain(`Build source: ${root}`)
    expect(out).toContain(`Overlay pin: ${PIN}`)
  })

  it('a clean, current checkout gets PAST the guard', () => {
    // Proves the refusals below are the guard and not the fixture. Past the
    // guard the script dies on R2 credentials, which is a different failure.
    const { script } = checkout()
    const { out } = run(script)
    expect(out).toContain('R2_ENDPOINT_URL not set')
    expect(out).not.toContain('uncommitted changes')
    expect(out).not.toContain('behind origin/main')
  })

  it('a .claude/ session marker alone does NOT trip the dirty check', () => {
    // The guard's first real use refused a rebuild over a lone
    // `parallel-isolation-required-<uuid>` marker (#2101). `.claude/` is in
    // .dockerignore and no COPY names it, so it cannot reach the image — and a
    // guard that trips on ordinary working conditions trains people to reach
    // for the bypass by reflex, which is worse than no guard at all.
    const { root, script } = checkout()
    mkdirSync(join(root, '.claude'), { recursive: true })
    writeFileSync(join(root, '.claude', 'parallel-isolation-required-abc123'), '')
    const { out } = run(script)
    expect(out).not.toContain('uncommitted changes')
    expect(out).toContain('R2_ENDPOINT_URL not set') // reached the next step
  })

  it('still refuses an untracked file OUTSIDE .claude/', () => {
    // The exclusion must stay narrow. An untracked source file genuinely can
    // change the image, so only `.claude/` is forgiven.
    const { root, script } = checkout()
    writeFileSync(join(root, 'operator', 'templates', 'stowaway.sh'), 'echo hi\n')
    const { out, ok } = run(script)
    expect(ok).toBe(false)
    expect(out).toContain('uncommitted changes')
    expect(out).toContain('stowaway.sh')
  })

  it('refuses a dirty checkout, naming the files', () => {
    const { root, script } = checkout()
    writeFileSync(join(root, 'operator', 'templates', 'entrypoint.sh'), 'echo drift\n')
    const { out, ok } = run(script)
    expect(ok).toBe(false)
    expect(out).toContain('uncommitted changes')
    expect(out).toContain('entrypoint.sh')
    expect(out).not.toContain('R2_ENDPOINT_URL not set')
  })

  it('refuses a checkout behind origin/main, naming the gap', () => {
    const { root, script } = checkout()
    writeFileSync(join(root, 'operator', 'customers', SLUG, 'customer.yaml'), 'customer_id: x\n')
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', 'ahead')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'reset', '--hard', '--quiet', 'HEAD~1')

    const { out, ok } = run(script)
    expect(ok).toBe(false)
    expect(out).toContain('behind origin/main')
    expect(out).toContain('1 commit(s)')
    expect(out).not.toContain('R2_ENDPOINT_URL not set')
  })

  it('refuses an OVERLAY_REF that disagrees with the pair manifest, naming both', () => {
    const { root, script } = checkout()
    const other = 'b'.repeat(40)
    writeFileSync(join(root, 'operator', 'templates', 'Dockerfile'), `ARG OVERLAY_REF="${other}"\n`)
    git(root, 'add', '-A')
    git(root, 'commit', '--quiet', '-m', 'drift the pin')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')

    const { out, ok } = run(script)
    expect(ok).toBe(false)
    expect(out).toContain('OVERLAY_REF mismatch')
    expect(out).toContain(other)
    expect(out).toContain(PIN)
  })

  it('SS_ALLOW_DIVERGENT_SOURCE=1 bypasses, and says so loudly', () => {
    // The escape hatch must exist — an operator sometimes means to build
    // exactly these bytes — but it must never be quiet about it.
    const { root, script } = checkout()
    writeFileSync(join(root, 'operator', 'templates', 'entrypoint.sh'), 'echo drift\n')
    const { out } = run(script, { SS_ALLOW_DIVERGENT_SOURCE: '1' })
    expect(out).toContain('SS_ALLOW_DIVERGENT_SOURCE=1')
    expect(out).toContain('bypassed BY REQUEST')
    expect(out).toContain('R2_ENDPOINT_URL not set')
  })
})
