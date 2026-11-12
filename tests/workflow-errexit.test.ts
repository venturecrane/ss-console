/**
 * A workflow step's `set` line must state the errexit mode the step really
 * runs under (ops-workspace-sweep, 2026-11-06, run 42224600144).
 *
 * GitHub runs a `run:` block as `bash -e {0}`, or `bash --noprofile --norc
 * -eo pipefail {0}` under `shell: bash`. Either way errexit is ON before the
 * first line runs, so `set -uo pipefail` or `set -o pipefail` does not turn it
 * off. A step written as if it did (one that keeps its own rc and reports a
 * failure after the fact) dies at the first failing statement instead. The
 * sweep did exactly that: its `ops-sweep | tee` pipeline exited nonzero, -e
 * aborted the step, and the FAIL line and the healthchecks /fail ping that
 * were the whole point of the step never ran. tests/workflow-pipefail.test.ts
 * makes this likelier, because pipefail turns a failed pipe stage into a
 * failed statement.
 *
 * The rule: under a shell that starts with errexit on, a block whose first
 * `set` line carries options either includes `e` in them (saying what is
 * true) or turns errexit off with `set +e` (saying what it relies on).
 *
 * What would make it false: a new step writing `set -uo pipefail` and relying
 * on reaching a later line after a failure, with no `set +e`.
 */
import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse } from 'yaml'

const WORKFLOWS = resolve('.github/workflows')

interface Step {
  name?: string
  run?: string
  shell?: string
}
interface Job {
  steps?: Step[]
  container?: unknown
  defaults?: { run?: { shell?: string } }
}
interface Workflow {
  jobs?: Record<string, Job>
  defaults?: { run?: { shell?: string } }
}

/** True when the shell GitHub will use starts the block with errexit on. */
function startsWithErrexit(shell: string | undefined): boolean {
  if (shell === undefined) return true
  const s = shell.trim()
  if (s === 'bash' || s === 'sh') return true
  return /^(ba)?sh\b.*\s-[a-z]*e[a-z]*\b/.test(s)
}

/** The flags of the first `set` line that carries options, or null. */
function firstSetFlags(run: string): string | null {
  for (const raw of run.split('\n')) {
    const line = raw.replace(/#.*$/, '').trim()
    const m = /^set\s+(-[a-zA-Z]+(?:\s+-?o\s+\w+)*|-o\s+\w+(?:\s+-[a-zA-Z]+)*)/.exec(line)
    if (m) return m[1]
  }
  return null
}

function misstatedErrexit(run: string, shell: string | undefined): boolean {
  if (!startsWithErrexit(shell)) return false
  const flags = firstSetFlags(run)
  if (flags === null) return false
  const shortFlags = flags
    .split(/\s+/)
    .filter((t) => /^-[a-zA-Z]+$/.test(t) && t !== '-o')
    .join('')
  if (/e/.test(shortFlags)) return false
  return !/^\s*set\s+\+[a-zA-Z]*e/m.test(run)
}

function offenders(file: string, wf: Workflow): string[] {
  const out: string[] = []
  for (const [jobId, job] of Object.entries(wf.jobs ?? {})) {
    for (const step of job.steps ?? []) {
      if (typeof step.run !== 'string') continue
      const shell = step.shell ?? job.defaults?.run?.shell ?? wf.defaults?.run?.shell
      if (misstatedErrexit(step.run, shell))
        out.push(`${file}:${jobId}:${step.name ?? '(unnamed)'}`)
    }
  }
  return out
}

describe('workflow set lines state the errexit mode the step runs under', () => {
  const files = readdirSync(WORKFLOWS).filter((f) => /\.ya?ml$/.test(f))

  it('finds the workflows (sanity)', () => {
    expect(files).toContain('ops-workspace-sweep.yml')
  })

  it('no step claims errexit is off without turning it off', () => {
    const found = files.flatMap((f) =>
      offenders(f, parse(readFileSync(join(WORKFLOWS, f), 'utf8')) as Workflow)
    )
    expect(found).toEqual([])
  })

  it('the check can fail: the pre-fix sweep step is caught', () => {
    const before = 'set -uo pipefail\nrc=0\nfly ssh console | tee sweep.txt\nexit "$rc"\n'
    expect(misstatedErrexit(before, undefined)).toBe(true)
    expect(misstatedErrexit(before, 'bash')).toBe(true)
    expect(misstatedErrexit('set -o pipefail\nfoo | bar\n', undefined)).toBe(true)
    expect(misstatedErrexit(`set -uo pipefail\nset +e\n${before}`, undefined)).toBe(false)
    expect(misstatedErrexit('set -euo pipefail\nfoo | bar\n', undefined)).toBe(false)
    expect(misstatedErrexit('set -eo pipefail\n', undefined)).toBe(false)
    expect(misstatedErrexit('echo hi\n', undefined)).toBe(false)
    expect(misstatedErrexit('set -uo pipefail\n', 'bash --noprofile --norc {0}')).toBe(false)
    expect(misstatedErrexit('set -uo pipefail\n', 'python')).toBe(false)
  })
})
