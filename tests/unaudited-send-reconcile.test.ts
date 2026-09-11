/**
 * ss#2309. The sibling of ss#2307, in the control that guards audit
 * completeness for the ss#2258 P0 — a message leaving an Operator mailbox with
 * no audit row.
 *
 * `operator/bin/reconcile-sends.py:367` returns `1 if any(r.is_finding ...)`,
 * and its exit-code contract is deliberate and unit-tested on the python side:
 * 0 is clean OR a hold, 1 is a finding. The WORKFLOW STEP consuming it threw
 * that contract away. GitHub runs `run:` bodies under `bash -e`, so the
 * reconciler exiting 1 — the findings code, the only non-zero path that is not
 * an error — aborted the step before `cat reconcile.txt` and before `status`
 * reached GITHUB_OUTPUT, leaving `Open an issue when a send has no audit record`
 * (gated on `status == '1'`) unreachable. A finding would produce a red run with
 * no report and no issue: mute in precisely the case the control exists for.
 *
 * These tests execute the REAL step body extracted from the YAML, under the
 * shell GitHub uses, against a `python3` stub that exits the findings code.
 * Asserting on the body's text would only restate the fix; running it is what
 * can fail.
 */
import { execFileSync } from 'node:child_process'
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const WORKFLOW = join(REPO_ROOT, '.github', 'workflows', 'unaudited-send-reconcile.yml')

describe('the reconcile step reports what the reconciler found (ss#2309)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  /** The `run:` body of the reconcile step, dedented, exactly as GitHub runs it. */
  const stepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf('      - name: Reconcile every inbox')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  /**
   * Run a step body the way the runner does: `bash -e <file>`, with `python3`
   * replaced by a stub on PATH that prints a report and exits `code`.
   */
  const runStep = (body: string, code: number) => {
    dir = mkdtempSync(join(tmpdir(), 'step-2309-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    writeFileSync(
      join(bin, 'python3'),
      `#!/usr/bin/env bash\necho "UNAUDITED: ops@smdurgan.com 1 send with no audit row"\nexit ${code}\n`
    )
    chmodSync(join(bin, 'python3'), 0o755)
    const outputs = join(dir, 'github_output')
    writeFileSync(outputs, '')
    writeFileSync(join(dir, 'step.sh'), body)
    let stdout: string
    let failed = false
    try {
      stdout = execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: { ...process.env, GITHUB_OUTPUT: outputs, PATH: `${bin}:${process.env.PATH ?? ''}` },
      })
    } catch (err) {
      failed = true
      stdout = String((err as { stdout?: string }).stdout ?? '')
    }
    return { stdout, failed, outputs: readFileSync(outputs, 'utf-8') }
  }

  it('prints the report and records status=1 when a send has no audit row', () => {
    const run = runStep(stepBody(), 1)
    // Without this the finding exists only in an exit code nobody can read.
    expect(run.stdout).toContain('UNAUDITED: ops@smdurgan.com 1 send with no audit row')
    // The issue-opening step is gated on exactly this.
    expect(run.outputs).toContain('status=1')
    // A finding is not an error: the next step handles it, so the step succeeds.
    expect(run.failed).toBe(false)
  })

  it('passes the report through untouched on a clean run', () => {
    const run = runStep(stepBody(), 0)
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('UNAUDITED: ops@smdurgan.com 1 send with no audit row')
    expect(run.outputs).toContain('status=0')
  })

  it('records a hold (exit 2) and leaves the failing to the last step', () => {
    // ss#2386 review: a hold reddens the run, but not HERE — the issue step must
    // still get its turn on a run that both held and found. The hold lines are
    // collected now and failed on at the end of the job.
    const run = runStep(stepBody(), 2)
    expect(run.failed).toBe(false)
    expect(run.outputs).toContain('status=2')
  })

  it('still fails the run when the reconciler itself broke (exit 3)', () => {
    const run = runStep(stepBody(), 3)
    expect(run.failed).toBe(true)
    // Even a broken control must not be silent — the output still reaches the log.
    expect(run.stdout).toContain('UNAUDITED: ops@smdurgan.com 1 send with no audit row')
  })

  /**
   * The falsifier. Reintroduce the pre-fix body and confirm this harness
   * reports the defect — otherwise the tests above are green for a reason
   * nobody has established.
   */
  it('detects the regression if the `|| STATUS=$?` guard is removed', () => {
    const regressed = stepBody().replace(/ \|\| STATUS=\$\?/, '\n          STATUS=$?')
    expect(regressed).not.toEqual(stepBody())
    const run = runStep(regressed, 1)
    expect(run.stdout).not.toContain('UNAUDITED')
    expect(run.outputs).not.toContain('status=1')
  })
})

/**
 * ss#2386. The second half of the reconciler's memory. The committed baseline
 * (`operator/bin/reconcile-sends-baseline.json`, unit-tested on the python side)
 * only quiets a find once its PR has merged; until then this step is what keeps
 * one finding from filing a second, third and fifth P1 — which is exactly what
 * happened: #2344, #2373, #2380, #2381 and #2382 are five copies of one find.
 *
 * The title carries the date, so no title-based check could ever have caught it.
 * The key is the fingerprint the reconciler prints and this step carries into
 * every issue body. Same discipline as the suite above: the REAL step body runs,
 * under the shell GitHub uses, against a `gh` stub, because asserting on the
 * body's text would only restate the fix.
 */
describe('the reconciler does not file the same finding twice (ss#2386)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  /** The `run:` body of the issue-opening step, dedented, as GitHub runs it. */
  const issueStepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf('      - name: Open an issue when a send has no audit record')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  const FINGERPRINT = 'c0ffee1234567890'

  /**
   * Run the issue step with `gh` stubbed: `issue list` prints `openIssues` (the
   * step redirects it into open-issues.json), and every invocation is logged so
   * the test can see whether an issue was created.
   */
  const runIssueStep = (
    body: string,
    { openIssues, report }: { openIssues: unknown[]; report?: string }
  ) => {
    dir = mkdtempSync(join(tmpdir(), 'step-2386-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    writeFileSync(join(dir, 'issues.json'), JSON.stringify(openIssues))
    writeFileSync(
      join(bin, 'gh'),
      '#!/usr/bin/env bash\n' +
        'echo "$@" >> "$GH_LOG"\n' +
        'if [ "$1" = "issue" ] && [ "$2" = "list" ]; then cat "$GH_ISSUES"; fi\n' +
        'exit 0\n'
    )
    chmodSync(join(bin, 'gh'), 0o755)
    writeFileSync(
      join(dir, 'reconcile.txt'),
      report ??
        `FIND  pilot-smokeball@agentmail.to [pilot-smokeball] sent=1 unaccounted=1\n\n` +
          `reconcile-fingerprint: ${FINGERPRINT}\n`
    )
    const log = join(dir, 'gh.log')
    writeFileSync(log, '')
    const summary = join(dir, 'summary.md')
    writeFileSync(summary, '')
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
          GH_TOKEN: 'stub',
          REPO: 'venturecrane/ss-console',
          RUN_URL: 'https://example.invalid/run/1',
          GITHUB_STEP_SUMMARY: summary,
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
    return { failed, gh: read('gh.log'), body: read('body.md'), summary: read('summary.md') }
  }

  it('files an issue when nothing open carries this fingerprint', () => {
    const run = runIssueStep(issueStepBody(), {
      openIssues: [{ number: 2100, body: 'an unrelated P1' }],
    })
    expect(run.failed).toBe(false)
    expect(run.gh).toContain('issue create')
    // Carried in the body so the NEXT run can recognise this issue as its own.
    expect(run.body).toContain(`reconcile-fingerprint: ${FINGERPRINT}`)
  })

  it('files nothing when the same finding is already open', () => {
    const run = runIssueStep(issueStepBody(), {
      openIssues: [
        { number: 2100, body: 'an unrelated P1' },
        { number: 2344, body: `report\nreconcile-fingerprint: ${FINGERPRINT}\n` },
      ],
    })
    expect(run.failed).toBe(false)
    expect(run.gh).not.toContain('issue create')
    // Silent to the issue tracker, never silent to the reader of the run.
    expect(run.summary).toContain('#2344')
  })

  it('still files when the finding grew, even with the old issue open', () => {
    // The dedupe must not become the silence. A new unaudited send changes the
    // fingerprint (proven on the python side), so it lands as a new issue.
    const run = runIssueStep(issueStepBody(), {
      openIssues: [{ number: 2344, body: 'reconcile-fingerprint: 1111111111111111' }],
    })
    expect(run.gh).toContain('issue create')
    expect(run.body).toContain(FINGERPRINT)
  })

  it('fails loudly when a findings report carries no fingerprint', () => {
    // The report shape changed under the step. Filing blind would restart the
    // flood, and skipping silently would drop a real finding.
    const run = runIssueStep(issueStepBody(), {
      openIssues: [],
      report: 'FIND  pilot-smokeball@agentmail.to unaccounted=1\n',
    })
    expect(run.failed).toBe(true)
    expect(run.gh).not.toContain('issue create')
  })

  /**
   * The falsifier. Disable the dedupe and confirm this harness reports the
   * duplicate — otherwise the test above is green for a reason nobody has
   * established.
   */
  it('detects the regression if the duplicate check is bypassed', () => {
    const regressed = issueStepBody().replace('if [ -n "${DUPLICATE}" ]; then', 'if false; then')
    expect(regressed).not.toEqual(issueStepBody())
    const run = runIssueStep(regressed, {
      openIssues: [{ number: 2344, body: `reconcile-fingerprint: ${FINGERPRINT}` }],
    })
    expect(run.gh).toContain('issue create')
  })
})

/**
 * ss#2386 review. A hold files no issue — ss#2258's rule that a control must not
 * page on its own blips is intact — but it must not report a pass either. An
 * unevaluated control that goes green is indistinguishable from a healthy one,
 * which is exactly the shape that let an inert period pass unnoticed for weeks.
 * Standardized with the sibling watchdogs shipped the same session
 * (control-probes.py #2395, reconcile-outcomes.py #2399).
 */
describe('a hold fails the run (ss#2386)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const holdStepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf('      - name: Fail the run when an inbox could not be evaluated')
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  /** Run the final step against a given holds.txt (undefined = file absent). */
  const runHoldStep = (holds: string | undefined) => {
    dir = mkdtempSync(join(tmpdir(), 'step-2386-hold-'))
    if (holds !== undefined) writeFileSync(join(dir, 'holds.txt'), holds)
    const summary = join(dir, 'summary.md')
    writeFileSync(summary, '')
    writeFileSync(join(dir, 'step.sh'), holdStepBody())
    let failed = false
    let stdout: string
    try {
      stdout = execFileSync('bash', ['-e', join(dir, 'step.sh')], {
        cwd: dir,
        encoding: 'utf-8',
        env: { ...process.env, GITHUB_STEP_SUMMARY: summary },
      })
    } catch (err) {
      failed = true
      stdout = String((err as { stdout?: string }).stdout ?? '')
    }
    return { failed, stdout, summary: readFileSync(summary, 'utf-8') }
  }

  it('fails the run when an inbox could not be evaluated', () => {
    const run = runHoldStep(
      'HOLD  pilot-smokeball@agentmail.to: audit_export read failed for pilot-smokeball\n'
    )
    expect(run.failed).toBe(true)
    // Red is not enough on its own: the reason has to be readable without
    // opening the log, and it must not read as a finding.
    expect(run.summary).toContain('audit_export read failed')
    expect(run.summary).toContain('No issue is filed for a hold')
  })

  it('fails on a missing-credential hold, the case the review named', () => {
    const run = runHoldStep('HOLD: AGENTMAIL_API_KEY unset (run under infisical)\n')
    expect(run.failed).toBe(true)
    expect(run.summary).toContain('AGENTMAIL_API_KEY unset')
  })

  it('passes when every inbox was evaluated', () => {
    const run = runHoldStep('')
    expect(run.failed).toBe(false)
    expect(run.stdout).toContain('Every inbox was evaluated')
  })

  it('does not itself break when the reconcile step never produced the file', () => {
    const run = runHoldStep(undefined)
    expect(run.failed).toBe(false)
  })
})

/**
 * Public-repo body safety (outbound-quality track, phase 4). Client email
 * bodies exist only inside the runner's python process: the reconciler's
 * verdict dataclasses are structurally body-free and its stdout is
 * sentinel-tested python-side (operator/bin/tests/test_send_verify.py). The
 * workflow's half of that contract is that no step interpolates anything BEYOND
 * that leak-tested stdout into an issue body -- the only files ever catted into
 * a body are reconcile.txt and lists.txt, both produced by sentinel-tested
 * scripts. These tests pin the workflow half.
 */
describe('workflow steps never interpolate body content (phase 4 leak safety)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  /** Every `run:` body in the workflow, dedented, keyed by step name. */
  const allStepBodies = (): Map<string, string> => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const bodies = new Map<string, string>()
    const namePattern = /^ {6}- name: (.+)$/gm
    let match: RegExpExecArray | null
    while ((match = namePattern.exec(source)) !== null) {
      const runAt = source.indexOf('run: |', match.index)
      if (runAt === -1) continue
      const nextName = namePattern.lastIndex
      // From past the matched line: the match itself starts at the indent, so
      // searching from index+1 would find this step's own `- name:` again.
      const following = source.indexOf('- name:', match.index + match[0].length)
      if (following !== -1 && runAt > following) continue // step with no run body
      const body = source.slice(runAt + 'run: |\n'.length)
      const lines: string[] = []
      for (const line of body.split('\n')) {
        if (line.trim() !== '' && !line.startsWith('          ')) break
        lines.push(line.slice(10))
      }
      bodies.set(match[1], lines.join('\n'))
      namePattern.lastIndex = nextName
    }
    return bodies
  }

  it('the only files catted into an issue body are the sentinel-tested reports', () => {
    // The producers of these two files are leak-tested python-side
    // (test_send_verify.py walks render() and --json for a sentinel body;
    // check-agentmail-lists.py emits only repo-authored addresses). Any OTHER
    // `cat` inside a step that writes a body*.md is a new egress nobody
    // sentinel-tested, and this assertion is what makes adding one a red PR.
    const allowed = new Set(['reconcile.txt', 'lists.txt'])
    const bodies = allStepBodies()
    expect(bodies.size).toBeGreaterThanOrEqual(4)
    for (const [name, body] of bodies) {
      if (!/> *body[^\s]*\.md/.test(body)) continue
      for (const line of body.split('\n')) {
        const cat = line.match(/^\s*cat\s+([^\s|>&;]+)\s*$/)
        if (cat) {
          expect(allowed.has(cat[1]), `step "${name}" cats ${cat[1]} toward an issue body`).toBe(
            true
          )
        }
      }
    }
  })

  it('a body-hash finding leaves only the report files behind, hashes in stdout', () => {
    // Run the REAL reconcile step with a stub whose report is a phase-4 body
    // finding line (hashes only, the shape send_verify.render_lines emits).
    dir = mkdtempSync(join(tmpdir(), 'step-phase4-'))
    const bin = join(dir, 'bin')
    mkdirSync(bin, { recursive: true })
    const findingLine =
      'BODY_DIVERGED deadline-miss-escalator dispatch=2026-08-30T09:01:00+00:00 ' +
      'expected=35c6b9f66dceb6cf8f733d08689564e420e18eb40250d9435352617c027f36d6 ' +
      'actual=611a167854cd6d1c92d7fbcc4ca9b3887ab9cec955ef0e11d4a8dfa3f0596cc4'
    writeFileSync(join(bin, 'python3'), `#!/usr/bin/env bash\necho "${findingLine}"\nexit 1\n`)
    chmodSync(join(bin, 'python3'), 0o755)
    const outputs = join(dir, 'github_output')
    writeFileSync(outputs, '')
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf('      - name: Reconcile every inbox')
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    writeFileSync(join(dir, 'step.sh'), lines.join('\n'))
    execFileSync('bash', ['-e', join(dir, 'step.sh')], {
      cwd: dir,
      encoding: 'utf-8',
      env: { ...process.env, GITHUB_OUTPUT: outputs, PATH: `${bin}:${process.env.PATH ?? ''}` },
    })
    // The step's entire filesystem footprint: its two report files (plus its
    // own scaffolding). Bodies never had a file to land in.
    const written = readdirSync(dir).filter((f) => !['bin', 'step.sh', 'github_output'].includes(f))
    expect(written.sort()).toEqual(['holds.txt', 'reconcile.txt'])
    expect(readFileSync(join(dir, 'reconcile.txt'), 'utf-8')).toContain('BODY_DIVERGED')
    expect(readFileSync(outputs, 'utf-8')).toContain('status=1')
  })
})

/**
 * PR #2651 review finding 1. The lists check keeps ONE rolling issue (the
 * ss#2582 discipline): a findings-derived dedupe key re-files the whole report
 * as a "new" dated issue the day one address joins the set. The step must find
 * its issue by the CONSTANT series marker, rewrite the body in place, and
 * comment only when the content digest moved. Real step body, stubbed gh.
 */
describe('the lists check keeps one rolling issue (ss#2582 discipline)', () => {
  let dir: string | undefined

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true })
    dir = undefined
  })

  const SERIES = 'agentmail-lists'
  const DIGEST = 'feedc0de12345678'

  const listsStepBody = (): string => {
    const source = readFileSync(WORKFLOW, 'utf8')
    const start = source.indexOf(
      '      - name: Report suppressed rostered recipients on ONE issue, updated in place'
    )
    expect(start).toBeGreaterThan(-1)
    const body = source.slice(source.indexOf('run: |', start) + 'run: |\n'.length)
    const lines: string[] = []
    for (const line of body.split('\n')) {
      if (line.trim() !== '' && !line.startsWith('          ')) break
      lines.push(line.slice(10))
    }
    return lines.join('\n')
  }

  const report = (digest: string = DIGEST): string =>
    `FIND  pilot-smokeball@agentmail.to [pilot-smokeball] rostered=4 list-findings=1\n\n` +
    `reconcile-series: ${SERIES}\nreconcile-findings: ${digest}\n`

  const runListsStep = (
    body: string,
    {
      openIssues,
      seed,
      existingBody,
    }: { openIssues: unknown[]; seed?: string; existingBody?: string }
  ) => {
    dir = mkdtempSync(join(tmpdir(), 'step-lists-'))
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
    writeFileSync(join(dir, 'lists.txt'), seed ?? report())
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
    return {
      failed,
      gh: read('gh.log'),
      body: read('body-lists.md'),
      comment: read('comment-lists.md'),
    }
  }

  it('creates the rolling issue when none carries the series marker', () => {
    const run = runListsStep(listsStepBody(), {
      openIssues: [{ number: 2100, body: 'an unrelated P1' }],
    })
    expect(run.failed).toBe(false)
    expect(run.gh).toContain('issue create')
    expect(run.body).toContain(`reconcile-series: ${SERIES}`)
  })

  it('a GROWN finding set updates the SAME issue instead of filing a copy', () => {
    // The exact regression the review named: yesterday's issue is open with
    // yesterday's digest, today one new suppressed address changed the digest.
    // A findings-derived key would miss the open issue and create a duplicate.
    const run = runListsStep(listsStepBody(), {
      openIssues: [{ number: 2700, body: `rolling\nreconcile-series: ${SERIES}\n` }],
      existingBody: `rolling\nreconcile-series: ${SERIES}\nreconcile-findings: 1111111111111111\n`,
    })
    expect(run.failed).toBe(false)
    expect(run.gh).not.toContain('issue create')
    expect(run.gh).toContain('issue edit 2700')
    // The set moved, so the history lands as a comment on the same issue.
    expect(run.gh).toContain('issue comment 2700')
    expect(run.comment).toContain(DIGEST)
  })

  it('an unchanged finding set refreshes the body with no comment', () => {
    const run = runListsStep(listsStepBody(), {
      openIssues: [{ number: 2700, body: `rolling\nreconcile-series: ${SERIES}\n` }],
      existingBody: `rolling\nreconcile-series: ${SERIES}\nreconcile-findings: ${DIGEST}\n`,
    })
    expect(run.gh).toContain('issue edit 2700')
    expect(run.gh).not.toContain('issue comment')
  })

  it('fails loudly when a findings report carries no series marker', () => {
    const run = runListsStep(listsStepBody(), {
      openIssues: [],
      seed: 'FIND  pilot-smokeball@agentmail.to list-findings=1\n',
    })
    expect(run.failed).toBe(true)
    expect(run.gh).not.toContain('issue create')
  })
})
