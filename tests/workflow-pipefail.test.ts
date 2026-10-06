/**
 * A workflow step that pipes a gate into another command must run with
 * pipefail (code review 2026-10-06, N4).
 *
 * GitHub runs a `run:` block with `bash -e {0}` unless the step (or the job or
 * workflow defaults) names `shell: bash`, which switches to
 * `bash --noprofile --norc -eo pipefail {0}`. Without pipefail the status of
 * `gate | tee log` is tee's, so `if ./gitleaks ... | tee out; then` took the
 * success branch on a real leak: the Secret Detection job could not fail.
 *
 * The rule pinned here: any `run:` block containing a pipe into another
 * command either says `set -o pipefail` (or `set -eo pipefail` /
 * `set -euo pipefail`) or runs under an explicit `shell: bash`.
 *
 * What would make it false: a new step piping a gate's output through tee
 * (or anything else) with neither guard.
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
  defaults?: { run?: { shell?: string } }
}
interface Workflow {
  jobs?: Record<string, Job>
  defaults?: { run?: { shell?: string } }
}

/**
 * A top-level pipeline statement (`a | b`): not `||`, a comment, a quoted
 * literal bar, a pipe inside `$(...)` (its status is the assignment's, a
 * separate concern), or a `case` pattern alternation (`a | b)`).
 */
function pipesIntoACommand(run: string): boolean {
  return run
    .split('\n')
    .map((l) =>
      l
        .replace(/#.*$/, '')
        .replace(/'[^']*'|"[^"]*"/g, "''")
        .replace(/\$\([^()]*\)/g, '$()')
    )
    .filter((l) => !/^\s*[^(]*[^|]\|[^|][^(]*\)/.test(l))
    .some((l) => /[^|]\|[^|]/.test(l))
}

function hasPipefail(run: string, shell: string | undefined): boolean {
  if (shell && /^bash\b/.test(shell.trim()) && !shell.includes('{0}')) return true
  if (shell && /pipefail/.test(shell)) return true
  return /\bset\s+-[a-z]*o\s+pipefail\b|\bset\s+-o\s+pipefail\b/.test(run)
}

function unguardedSteps(file: string, wf: Workflow): string[] {
  const out: string[] = []
  for (const [jobId, job] of Object.entries(wf.jobs ?? {})) {
    for (const step of job.steps ?? []) {
      if (typeof step.run !== 'string' || !pipesIntoACommand(step.run)) continue
      const shell = step.shell ?? job.defaults?.run?.shell ?? wf.defaults?.run?.shell
      if (!hasPipefail(step.run, shell)) out.push(`${file}:${jobId}:${step.name ?? '(unnamed)'}`)
    }
  }
  return out
}

describe('workflow steps that pipe run with pipefail', () => {
  const files = readdirSync(WORKFLOWS).filter((f) => /\.ya?ml$/.test(f))

  it('finds the workflows (sanity)', () => {
    expect(files).toContain('security.yml')
  })

  it('every piping run block is guarded', () => {
    const offenders = files.flatMap((f) =>
      unguardedSteps(f, parse(readFileSync(join(WORKFLOWS, f), 'utf8')) as Workflow)
    )
    expect(offenders).toEqual([])
  })

  it('the check can fail: the pre-fix gitleaks step is caught', () => {
    const bad: Workflow = {
      jobs: {
        secrets: {
          steps: [
            {
              name: 'Run Gitleaks',
              run: 'if ./gitleaks git . 2>&1 | tee out.txt; then\n  :\nfi\n',
            },
          ],
        },
      },
    }
    expect(unguardedSteps('x.yml', bad)).toEqual(['x.yml:secrets:Run Gitleaks'])
    const fixed = structuredClone(bad)
    fixed.jobs!.secrets.steps![0].run = `set -o pipefail\n${bad.jobs!.secrets.steps![0].run}`
    expect(unguardedSteps('x.yml', fixed)).toEqual([])
    expect(pipesIntoACommand('a || b')).toBe(false)
    expect(pipesIntoACommand('X="$(sed -n p f | tail -1)"')).toBe(false)
    expect(pipesIntoACommand("    '' | 0000) ;;")).toBe(false)
    expect(pipesIntoACommand('npm audit --json | jq .')).toBe(true)
  })
})
