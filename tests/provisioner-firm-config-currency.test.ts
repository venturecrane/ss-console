/**
 * provision-customer.sh refuses to upload a firm config that differs from
 * engagements origin/main (2026-09-24).
 *
 * A reprovision from the shared ~/dev/engagements checkout, which was behind
 * main, put ashton-price's chronology tiers back on claude-opus-5 forty minutes
 * after engagements#142 moved them to claude-opus-5-5, and exited zero. The
 * guard compares the local file with `origin/main:<path>` by content.
 *
 * The function is sentinel-delimited and driven VERBATIM against throwaway git
 * repos (a bare origin plus a clone). Every git spawn strips GIT_* from the
 * environment: under the pre-push hook GIT_DIR/GIT_INDEX_FILE would otherwise
 * point these commands at the repo being pushed.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/provision-customer.sh', import.meta.url))
const LIB = fileURLToPath(new URL('../operator/bin/lib/firm-config-currency.sh', import.meta.url))
const src = readFileSync(SCRIPT, 'utf8')
const lib = readFileSync(LIB, 'utf8')

function block(): string {
  const start = lib.indexOf('# >>> firm-config-currency')
  const end = lib.indexOf('# <<< firm-config-currency')
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return lib.slice(start, end)
}

const cleanEnv = (): NodeJS.ProcessEnv =>
  Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_')))

function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', args, { cwd, env: cleanEnv(), encoding: 'utf8' })
}

const scratch: string[] = []
afterAll(() => scratch.forEach((d) => rmSync(d, { recursive: true, force: true })))

const REL = 'operator/customers/acme/medchron/firm.yaml'
const MAIN = 'models:\n  tiers:\n    composition: claude-opus-5-5\n'
const STALE = 'models:\n  tiers:\n    composition: claude-opus-5\n'

/** A clone whose origin/main carries MAIN at `mainRel`; the working file at
 * REL holds `local`. */
function scene(local: string | null, mainRel: string = REL): string {
  const root = mkdtempSync(join(tmpdir(), 'firm-currency-'))
  scratch.push(root)
  const origin = join(root, 'origin.git')
  const seed = join(root, 'seed')
  const clone = join(root, 'clone')
  git(root, 'init', '-q', '--bare', '-b', 'main', origin)
  git(root, 'clone', '-q', origin, seed)
  git(seed, 'config', 'user.email', 't@t')
  git(seed, 'config', 'user.name', 't')
  mkdirSync(join(seed, mainRel, '..'), { recursive: true })
  writeFileSync(join(seed, mainRel), MAIN)
  git(seed, 'add', '.')
  git(seed, 'commit', '-q', '-m', 'main')
  git(seed, 'push', '-q', 'origin', 'HEAD:main')
  git(root, 'clone', '-q', origin, clone)
  mkdirSync(join(clone, REL, '..'), { recursive: true })
  if (local !== null) writeFileSync(join(clone, REL), local)
  return clone
}

type Guard = 'assert_firm_config_is_main' | 'assert_engagements_checkout_present'

function run(
  clone: string,
  extraEnv: Record<string, string> = {},
  fn: Guard = 'assert_firm_config_is_main'
): { code: number; out: string } {
  const script = `
set -u
log() { echo "LOG: $*"; }
die() { echo "DIE: $*"; exit 3; }
SLUG=acme
MEDCHRON_FIRM_YAML="${join(clone, REL)}"
${block()}
${fn}
echo PASSED
`
  try {
    const out = execFileSync('bash', ['-c', script], {
      env: { ...cleanEnv(), ...extraEnv },
      encoding: 'utf8',
    })
    return { code: 0, out }
  } catch (e) {
    const err = e as { status: number; stdout: string }
    return { code: err.status, out: err.stdout }
  }
}

describe('firm config currency guard', () => {
  it('passes when the local firm config equals engagements origin/main', () => {
    const r = run(scene(MAIN))
    expect(r.code).toBe(0)
    expect(r.out).toContain('Firm config matches engagements origin/main')
    expect(r.out).toContain('PASSED')
  })

  it('refuses when the checkout holds a stale firm config (the 09-24 revert)', () => {
    const r = run(scene(STALE))
    expect(r.code).toBe(3)
    expect(r.out).toContain('differs from engagements origin/main')
    expect(r.out).not.toContain('PASSED')
  })

  it('uploads a divergent file only when SS_ALLOW_DIVERGENT_SOURCE=1, and says so', () => {
    const r = run(scene(STALE), { SS_ALLOW_DIVERGENT_SOURCE: '1' })
    expect(r.code).toBe(0)
    expect(r.out).toContain('BY REQUEST')
  })

  // The three cannot-evaluate states (code review 2026-09-25, Law 2). Each one
  // used to warn and upload; each now dies naming its fix, and each still
  // yields to the escape hatch, which says so.
  function plainTree(): string {
    const root = mkdtempSync(join(tmpdir(), 'firm-currency-plain-'))
    scratch.push(root)
    mkdirSync(join(root, 'operator/customers/acme/medchron'), { recursive: true })
    writeFileSync(join(root, REL), STALE)
    return root
  }

  function unfetchable(): string {
    const clone = scene(MAIN)
    git(clone, 'remote', 'set-url', 'origin', join(clone, '..', 'gone.git'))
    return clone
  }

  const cannotEvaluate: Array<[string, () => string, string, string]> = [
    [
      'the engagements tree is not a git checkout',
      plainTree,
      'is not a git checkout',
      'Point SS_ENGAGEMENTS_DIR at a clone',
    ],
    [
      'fetching engagements origin fails',
      unfetchable,
      'fetching engagements origin failed',
      'gh auth status',
    ],
    [
      'the firm config is not on engagements origin/main',
      () => scene(MAIN, 'operator/customers/other/medchron/firm.yaml'),
      'is not on engagements origin/main',
      'Merge the firm config to engagements main first',
    ],
  ]

  for (const [state, make, reason, fix] of cannotEvaluate) {
    it(`refuses when ${state}, and names the fix`, () => {
      const r = run(make())
      expect(r.code).toBe(3)
      expect(r.out).toContain(reason)
      expect(r.out).toContain(fix)
      expect(r.out).toContain('SS_ALLOW_DIVERGENT_SOURCE=1')
      expect(r.out).not.toContain('PASSED')
    })

    it(`proceeds when ${state} only under SS_ALLOW_DIVERGENT_SOURCE=1, and says so`, () => {
      const r = run(make(), { SS_ALLOW_DIVERGENT_SOURCE: '1' })
      expect(r.code).toBe(0)
      expect(r.out).toContain('BY REQUEST')
      expect(r.out).toContain(reason)
    })
  }

  // One step earlier: with no engagements checkout on the machine running the
  // reprovision, the provisioner cannot know whether this seat authors a firm
  // config at all. pilot-smokeball authors none, so the old path logged "No
  // medchron firm config" and carried on: cannot-evaluate read as not-authored.
  // It dies now, and the message says to clone the repo.
  it('refuses a missing engagements checkout and names the fix', () => {
    const root = mkdtempSync(join(tmpdir(), 'firm-currency-none-'))
    scratch.push(root)
    const r = run(join(root, 'no-engagements'), {}, 'assert_engagements_checkout_present')
    expect(r.code).toBe(3)
    expect(r.out).toContain('engagements checkout is missing')
    expect(r.out).toContain('Clone venturecrane/engagements')
    expect(r.out).toContain('SS_ENGAGEMENTS_DIR')
    expect(r.out).not.toContain('PASSED')
  })

  it('a present engagements checkout passes the presence check', () => {
    const r = run(scene(MAIN), {}, 'assert_engagements_checkout_present')
    expect(r.code).toBe(0)
    expect(r.out).toContain('PASSED')
  })

  it('the provisioner checks the checkout, then the firm config, before it is validated or uploaded', () => {
    // Order inside the condition is the contract: the presence check runs for
    // every seat, before `-f` can read a missing tree as "nothing authored".
    const gate = src.indexOf(
      'if . "${BIN_DIR}/lib/firm-config-currency.sh" && assert_engagements_checkout_present && [ -f "${MEDCHRON_FIRM_YAML}" ] && assert_firm_config_is_main; then'
    )
    expect(gate).toBeGreaterThan(-1)
    expect(gate).toBeLessThan(src.indexOf('# >>> medchron-firm-validate'))
  })
})
