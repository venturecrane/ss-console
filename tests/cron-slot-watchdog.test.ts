/**
 * The workflow half of the cron-slot watchdog.
 *
 * `operator/bin/reconcile-wakes.py` has a three-value exit contract that is
 * unit-tested on the python side: 0 clean, 1 finding, 3 HOLD. The workflow step
 * consuming it is where such contracts get thrown away (ss#2309: the sibling
 * reconciler's step aborted under `bash -e` on the findings exit, before the
 * report printed and before `status` reached GITHUB_OUTPUT -- mute in exactly
 * the case the control exists for). And the issue step carries the ss#2582
 * rolling-issue discipline: ONE issue found by a constant series marker,
 * rewritten in place, because daily misses accrue and a findings-derived key
 * files duplicates.
 *
 * These execute the REAL step bodies extracted from the YAML, under the shell
 * GitHub uses, against `python3` / `gh` stubs. Asserting on the bodies' text
 * would only restate the wiring; running them is what can fail.
 */
import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'cron-slot-watchdog.yml')

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

const RECONCILE = "Reconcile every seat's slot grid"
const HOLD = 'Fail loudly when nothing could be evaluated'
const ISSUE = 'Report the missing slots on ONE issue, updated in place'

describe('the watchdog step reports what the reconciler found', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const runStep = (body: string, code: number, seed?: string) => {
    dir = mkdtempSync(join(tmpdir(), 'step-wakes-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    writeFileSync(
      join(bin, 'python3'),
      `#!/usr/bin/env bash\necho "MISSING: deadline-miss-escalator slot with neither wake row"\nexit ${code}\n`
    )
    chmodSync(join(bin, 'python3'), 0o755)
    if (seed !== undefined) writeFileSync(join(dir, 'wakes.txt'), seed)
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

  it('prints the report and records status=1 when a slot went missing', () => {
    const run = runStep(stepBody(RECONCILE), 1)
    expect(run.stdout).toContain('MISSING: deadline-miss-escalator')
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

  it('still fails the run when the watchdog itself broke (exit 2)', () => {
    const run = runStep(stepBody(RECONCILE), 2)
    expect(run.failed).toBe(true)
    expect(run.stdout).toContain('MISSING:')
  })

  it('the HOLD step fails the run and says why', () => {
    const run = runStep(
      stepBody(HOLD),
      0,
      'HOLD: fleet_status read failed (auth); no seat can be partitioned\n'
    )
    // Red, not green: a control that could not run has not passed.
    expect(run.failed).toBe(true)
    expect(run.summary).toContain('Cron-slot watchdog: HOLD')
    expect(run.summary).toContain('fleet_status read failed')
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
    expect(run.stdout).not.toContain('MISSING:')
    expect(run.outputs).not.toContain('status=1')
  })
})

describe('the watchdog keeps ONE rolling issue (ss#2582 discipline)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const SERIES = 'cron-slot-watchdog'
  const DIGEST = 'abcd1234abcd1234'

  const report = (digest: string = DIGEST): string =>
    `FIND  pilot-smokeball slots=12 covered=11 missing=1\n\n` +
    `reconcile-series: ${SERIES}\nreconcile-findings: ${digest}\n`

  const runIssueStep = (
    body: string,
    {
      openIssues,
      seed,
      existingBody,
    }: { openIssues: unknown[]; seed?: string; existingBody?: string }
  ) => {
    dir = mkdtempSync(join(tmpdir(), 'step-wakes-issue-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    writeFileSync(join(dir, 'issues.json'), JSON.stringify(openIssues))
    writeFileSync(join(dir, 'existing-body.txt'), existingBody ?? '')
    writeFileSync(
      join(bin, 'gh'),
      '#!/usr/bin/env bash\n' +
        'echo "$@" >> "$GH_LOG"\n' +
        'if [ "$1" = "issue" ] && [ "$2" = "list" ]; then cat "$GH_ISSUES"; fi\n' +
        'if [ "$1" = "issue" ] && [ "$2" = "view" ]; then cat "$GH_EXISTING_BODY"; fi\n' +
        'exit 0\n'
    )
    chmodSync(join(bin, 'gh'), 0o755)
    writeFileSync(join(dir, 'wakes.txt'), seed ?? report())
    const log = join(dir, 'gh.log')
    writeFileSync(log, '')
    writeFileSync(join(dir, 'step.sh'), body)
    let failed = false
    try {
      execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: {
          ...process.env,
          PATH: `${bin}:${process.env.PATH ?? ''}`,
          GH_LOG: log,
          GH_ISSUES: join(dir, 'issues.json'),
          GH_EXISTING_BODY: join(dir, 'existing-body.txt'),
          GH_TOKEN: 'stub',
          REPO: 'venturecrane/ss-console',
          RUN_URL: 'https://example.invalid/run/1',
        },
      })
    } catch {
      failed = true
    }
    const read = (name: string) => {
      try {
        return readFileSync(join(dir as string, name), 'utf-8')
      } catch {
        return ''
      }
    }
    return { failed, gh: read('gh.log'), body: read('body.md'), comment: read('comment.md') }
  }

  it('creates the rolling issue when none carries the series marker', () => {
    const run = runIssueStep(stepBody(ISSUE), {
      openIssues: [{ number: 2100, body: 'an unrelated P1' }],
    })
    expect(run.failed).toBe(false)
    expect(run.gh).toContain('issue create')
    // Carried in the body so every later run can recognise this issue as its own.
    expect(run.body).toContain(`reconcile-series: ${SERIES}`)
  })

  it('updates the existing issue in place instead of filing a copy', () => {
    const run = runIssueStep(stepBody(ISSUE), {
      openIssues: [{ number: 2600, body: `rolling\nreconcile-series: ${SERIES}\n` }],
      existingBody: `rolling\nreconcile-series: ${SERIES}\nreconcile-findings: ${DIGEST}\n`,
    })
    expect(run.failed).toBe(false)
    expect(run.gh).not.toContain('issue create')
    expect(run.gh).toContain('issue edit 2600')
    // Digest unchanged: body refreshed, no comment.
    expect(run.gh).not.toContain('issue comment')
  })

  it('comments only when the finding set moved', () => {
    const run = runIssueStep(stepBody(ISSUE), {
      openIssues: [{ number: 2600, body: `rolling\nreconcile-series: ${SERIES}\n` }],
      existingBody: `rolling\nreconcile-series: ${SERIES}\nreconcile-findings: 1111111111111111\n`,
    })
    expect(run.gh).toContain('issue edit 2600')
    expect(run.gh).toContain('issue comment 2600')
    expect(run.comment).toContain(DIGEST)
  })

  it('fails loudly when a findings report carries no series marker', () => {
    const run = runIssueStep(stepBody(ISSUE), {
      openIssues: [],
      seed: 'FIND  pilot-smokeball missing=1\n',
    })
    expect(run.failed).toBe(true)
    expect(run.gh).not.toContain('issue create')
  })
})
