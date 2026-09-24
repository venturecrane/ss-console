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
const src = readFileSync(SCRIPT, 'utf8')

function block(): string {
  const start = src.indexOf('# >>> firm-config-currency')
  const end = src.indexOf('# <<< firm-config-currency')
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return src.slice(start, end)
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

/** A clone whose origin/main carries MAIN; the working file holds `local`. */
function scene(local: string | null): string {
  const root = mkdtempSync(join(tmpdir(), 'firm-currency-'))
  scratch.push(root)
  const origin = join(root, 'origin.git')
  const seed = join(root, 'seed')
  const clone = join(root, 'clone')
  git(root, 'init', '-q', '--bare', '-b', 'main', origin)
  git(root, 'clone', '-q', origin, seed)
  git(seed, 'config', 'user.email', 't@t')
  git(seed, 'config', 'user.name', 't')
  mkdirSync(join(seed, 'operator/customers/acme/medchron'), { recursive: true })
  writeFileSync(join(seed, REL), MAIN)
  git(seed, 'add', '.')
  git(seed, 'commit', '-q', '-m', 'main')
  git(seed, 'push', '-q', 'origin', 'HEAD:main')
  git(root, 'clone', '-q', origin, clone)
  if (local !== null) writeFileSync(join(clone, REL), local)
  return clone
}

function run(clone: string, extraEnv: Record<string, string> = {}): { code: number; out: string } {
  const script = `
set -u
log() { echo "LOG: $*"; }
die() { echo "DIE: $*"; exit 3; }
SLUG=acme
MEDCHRON_FIRM_YAML="${join(clone, REL)}"
${block()}
assert_firm_config_is_main
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

  it('warns and proceeds when the engagements tree is not a git checkout', () => {
    const root = mkdtempSync(join(tmpdir(), 'firm-currency-plain-'))
    scratch.push(root)
    mkdirSync(join(root, 'operator/customers/acme/medchron'), { recursive: true })
    writeFileSync(join(root, REL), STALE)
    const r = run(root)
    expect(r.code).toBe(0)
    expect(r.out).toContain('not a git checkout')
  })

  it('runs before the firm config is validated or uploaded', () => {
    const call = src.indexOf('  assert_firm_config_is_main\n')
    expect(call).toBeGreaterThan(-1)
    expect(call).toBeLessThan(src.indexOf('# >>> medchron-firm-validate'))
  })
})
