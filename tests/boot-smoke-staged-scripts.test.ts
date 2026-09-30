/**
 * Boot smoke step 6d: every staged cron script is byte-identical to its skill
 * copy (2026-09-29).
 *
 * Hermes runs a cron pre_run only from <profile>/scripts/<skill>/, and the
 * overlay restages that copy only for skills with a live cron. On
 * pilot-smokeball the staged motion-calendar-tracker pre_run.py was the
 * pre-#3003 file (749 lines) beside an 892-line skill copy, so the wake line
 * carried no facts_digest, and six unscheduled trackers were stale. Nothing
 * checked it.
 *
 * The step is EXECUTED here, the way boot-smoke-collects-failures.test.ts runs
 * the whole script: a `fly` stub on PATH decodes the step's base64 program and
 * runs it with `sh` against a fixture tree (SMD_SMOKE_DATA_ROOT), so the program
 * under test is the one the seat runs. Every other ssh check answers 0.
 */

import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

const SCRIPT = resolve('operator/bin/boot-smoke-test.sh')
const SEAT_YAML = resolve('operator/customers/pilot-smokeball/customer.yaml')

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})

function tmp(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  scratch.push(dir)
  return dir
}

function authoredSeatMemoryMb(): number {
  const lines = readFileSync(SEAT_YAML, 'utf8').split('\n')
  let inMachine = false
  for (const line of lines) {
    if (/^machine:/.test(line)) {
      inMachine = true
      continue
    }
    if (!inMachine) continue
    if (/^[^ \t#]/.test(line)) break
    const m = /^\s+memory_mb:\s*(\d+)/.exec(line)
    if (m) return Number(m[1])
  }
  throw new Error(`no machine.memory_mb found in ${SEAT_YAML}`)
}

/** `fly` and `uv` stubs; `fly` runs ONLY the staged-scripts program for real. */
function stubDir(): string {
  const dir = tmp('boot-smoke-staged-stub-')
  const fly = `#!/usr/bin/env bash
for a in "$@"; do
  [ "$a" = "status" ] && {
    echo '{"Machines":[{"state":"started","config":{"guest":{"cpu_kind":"shared","cpus":1,"memory_mb":${authoredSeatMemoryMb()}}}}]}'
    exit 0
  }
done
cmd="\${@: -1}"
case "$cmd" in
  *"base64 -d"*)
    enc="\${cmd#*echo }"; enc="\${enc%% |*}"
    prog="$(printf '%s' "$enc" | base64 -d)"
    case "$prog" in
      "# staged-scripts-current"*) printf '%s' "$prog" | sh; exit $? ;;
    esac
    ;;
esac
exit 0
`
  writeFileSync(join(dir, 'fly'), fly)
  chmodSync(join(dir, 'fly'), 0o755)
  const uv = `#!/usr/bin/env bash
prog=""; file=""
for a in "$@"; do
  case "$a" in
    */customer.yaml) file="$a" ;;
    *machine*|*hermes_ref*) prog="$a" ;;
  esac
done
[ -n "$file" ] || exit 1
case "$prog" in
  *hermes_ref*) awk -F@ '/^hermes_ref:/{gsub(/[[:space:]]/,"",$2); print $2; exit}' "$file" ;;
  *memory_mb*)  awk '/^machine:/{m=1;next} m&&/memory_mb:/{print $2;exit} m&&/^[^ ]/{exit}' "$file" ;;
  *size*)       awk '/^machine:/{m=1;next} m&&/size:/{print $2;exit} m&&/^[^ ]/{exit}' "$file" ;;
  *)            exit 1 ;;
esac
`
  writeFileSync(join(dir, 'uv'), uv)
  chmodSync(join(dir, 'uv'), 0o755)
  return dir
}

function put(root: string, rel: string, text: string): string {
  const path = join(root, rel)
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, text)
  return path
}

function run(dataRoot: string): { out: string; code: number } {
  const dir = stubDir()
  try {
    const out = execFileSync('bash', [SCRIPT, 'pilot-smokeball'], {
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: [dir, '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(':'),
        SS_ENGAGEMENTS_DIR: dir,
        SMD_SMOKE_DATA_ROOT: dataRoot,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    return { out, code: 0 }
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; status?: number }
    return { out: `${e.stdout ?? ''}${e.stderr ?? ''}`, code: e.status ?? -1 }
  }
}

const CURRENT = 'print("pre_run, 892 lines")\n'
const STALE = 'print("pre_run, 749 lines")\n'

describe('boot smoke step 6d: staged scripts current', () => {
  it('a matched pair passes and the run is clean', () => {
    const root = tmp('staged-ok-')
    put(root, 'profiles/operator/skills/motion-calendar-tracker/pre_run.py', CURRENT)
    put(root, 'profiles/operator/scripts/motion-calendar-tracker/pre_run.py', CURRENT)
    const { out, code } = run(root)
    expect(out).toContain('PASS: staged-scripts-current (checked: 1 staged script(s))')
    expect(code).toBe(0)
  }, 90_000)

  it('a staged copy that differs from its skill copy fails and names the path', () => {
    const root = tmp('staged-stale-')
    put(root, 'profiles/operator/skills/motion-calendar-tracker/pre_run.py', CURRENT)
    const staged = put(root, 'profiles/operator/scripts/motion-calendar-tracker/pre_run.py', STALE)
    put(root, 'profiles/operator/skills/lien-ledger-tracker/pre_run.py', CURRENT)
    put(root, 'profiles/operator/scripts/lien-ledger-tracker/pre_run.py', CURRENT)
    const { out, code } = run(root)
    expect(out).toContain('FAIL: staged-scripts-current')
    expect(out).toContain(`stale: ${staged} differs from`)
    expect(out).not.toContain('scripts/lien-ledger-tracker/pre_run.py differs')
    expect(out).toContain('checks failed: 1')
    expect(code).not.toBe(0)
  }, 90_000)

  it('with no profile skill copy it compares against /opt/data/skills', () => {
    const root = tmp('staged-fallback-')
    put(root, 'skills/service-confirmation-watcher/pre_run.py', CURRENT)
    put(root, 'profiles/operator/scripts/service-confirmation-watcher/pre_run.py', CURRENT)
    expect(run(root).out).toContain('PASS: staged-scripts-current (checked: 1')
    put(root, 'skills/service-confirmation-watcher/pre_run.py', STALE)
    const stale = run(root)
    expect(stale.out).toContain('FAIL: staged-scripts-current')
    expect(stale.out).toContain(
      `differs from ${root}/skills/service-confirmation-watcher/pre_run.py`
    )
  }, 90_000)

  it('a staged script with no skill copy anywhere fails', () => {
    const root = tmp('staged-orphan-')
    const staged = put(root, 'profiles/operator/scripts/retired-tracker/pre_run.py', STALE)
    const { out, code } = run(root)
    expect(out).toContain(`stale: ${staged} has no skill copy`)
    expect(code).not.toBe(0)
  }, 90_000)
})
