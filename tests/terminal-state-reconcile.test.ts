/**
 * ss#2388. The workflow half of the terminal-state reconciler.
 *
 * `operator/bin/reconcile-outcomes.py` has a three-value exit contract that is
 * unit-tested on the python side: 0 clean, 1 finding, 3 HOLD. The workflow step
 * consuming it is where that contract gets thrown away, and it has been thrown
 * away here before: ss#2309 found the sibling reconciler's step aborting under
 * `bash -e` on the findings exit, before the report printed and before `status`
 * reached GITHUB_OUTPUT, so a finding produced a red run with no report and no
 * issue.
 *
 * The HOLD path is the one this control adds. Its whole point is that "nothing
 * was evaluated" must never render as a green check, so the test that matters is
 * the one that fails when the hold is swallowed.
 *
 * These execute the REAL step bodies extracted from the YAML, under the shell
 * GitHub uses, against a `python3` stub that exits a chosen code. Asserting on
 * the body's text would only restate the wiring; running it is what can fail.
 */
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'terminal-state-reconcile.yml')

/** The `run:` body of a named step, dedented, exactly as GitHub runs it. */
const stepBody = (name: string): string => {
  const source = readFileSync(WORKFLOW, 'utf8')
  const start = source.indexOf(`      - name: ${name}`)
  expect(start).toBeGreaterThan(-1)
  const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
  const lines: string[] = []
  for (const line of body.split('\n')) {
    if (line.trim() !== '' && !line.startsWith('          ')) break
    lines.push(line.slice(10))
  }
  return lines.join('\n')
}

describe('the terminal-state reconcile step reports what the reconciler found', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const runStep = (body: string, code: number, seed?: string) => {
    dir = mkdtempSync(join(tmpdir(), 'step-2388-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    writeFileSync(
      join(bin, 'python3'),
      `#!/usr/bin/env bash\necho "SILENT: pilot-smokeball REPLY_HELD held_without_notice"\nexit ${code}\n`
    )
    chmodSync(join(bin, 'python3'), 0o755)
    if (seed !== undefined) writeFileSync(join(dir, 'outcomes.txt'), seed)
    const outputs = join(dir, 'github_output')
    const summary = join(dir, 'github_step_summary')
    writeFileSync(outputs, '')
    writeFileSync(summary, '')
    writeFileSync(join(dir, 'step.sh'), body)
    let stdout: string
    let failed = false
    try {
      stdout = execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: {
          ...process.env,
          GITHUB_OUTPUT: outputs,
          GITHUB_STEP_SUMMARY: summary,
          PATH: `${bin}:${process.env.PATH ?? ''}`,
        },
      })
    } catch (err) {
      failed = true
      stdout = String((err as { stdout?: string }).stdout ?? '')
    }
    return {
      stdout,
      failed,
      outputs: readFileSync(outputs, 'utf-8'),
      summary: readFileSync(summary, 'utf-8'),
    }
  }

  const RECONCILE = 'Reconcile every seat'
  const HOLD = 'Fail loudly when nothing could be evaluated'

  it('prints the report and records status=1 when a run ended in silence', () => {
    const run = runStep(stepBody(RECONCILE), 1)
    expect(run.stdout).toContain('SILENT: pilot-smokeball REPLY_HELD held_without_notice')
    // The issue-opening step is gated on exactly this.
    expect(run.outputs).toContain('status=1')
    // A finding is not an error: the next step handles it.
    expect(run.failed).toBe(false)
  })

  it('records status=3 rather than dying, so the HOLD step can speak', () => {
    const run = runStep(stepBody(RECONCILE), 3)
    expect(run.outputs).toContain('status=3')
    expect(run.failed).toBe(false)
  })

  it('passes the report through untouched on a clean run', () => {
    const run = runStep(stepBody(RECONCILE), 0)
    expect(run.failed).toBe(false)
    expect(run.outputs).toContain('status=0')
  })

  it('still fails the run when the reconciler itself broke (exit 2)', () => {
    const run = runStep(stepBody(RECONCILE), 2)
    expect(run.failed).toBe(true)
    expect(run.stdout).toContain('SILENT:')
  })

  it('the HOLD step fails the run and says why', () => {
    const run = runStep(stepBody(HOLD), 0, 'HOLD  pilot-smokeball: audit_export read failed')
    // Red, not green: a control that could not run has not passed.
    expect(run.failed).toBe(true)
    expect(run.summary).toContain('Terminal-state reconciler: HOLD')
    expect(run.summary).toContain('audit_export read failed')
    expect(run.stdout).toContain('audit_export read failed')
  })

  /**
   * The falsifier. Reintroduce the ss#2309 shape and confirm this harness
   * reports it, otherwise the assertions above are green for a reason nobody
   * has established.
   */
  it('detects the regression if the `|| STATUS=$?` guard is removed', () => {
    const regressed = stepBody(RECONCILE).replace(/ \|\| STATUS=\$\?/, '\n          STATUS=$?')
    expect(regressed).not.toEqual(stepBody(RECONCILE))
    const run = runStep(regressed, 1)
    expect(run.stdout).not.toContain('SILENT:')
    expect(run.outputs).not.toContain('status=1')
  })
})
