/**
 * ss#2441. A caller that delegates to a reusable workflow must grant permissions.
 *
 * `Regression Claim-Origin` failed 2,059 out of 2,059 runs — every run from
 * 2026-05-07 onward, zero successes, zero claim-origin comments ever posted. The
 * caller declared no `permissions:` block, so it started at this repo's
 * read-only `default_workflow_permissions`; the called workflow's job in
 * crane-console declares `permissions: issues: write` because posting a comment
 * is its entire job; a called workflow may only be granted what the caller
 * already holds; GitHub refused the run at startup. `startup_failure`, zero
 * jobs, 404 on the logs endpoint, every time.
 *
 * It ran silently for 104 days because a `startup_failure` produces no job and
 * no check run, so the CI notification sink never emitted anything for it —
 * 6,214 events recorded for this repo in the same window, successes included,
 * and not one of them a Regression Claim-Origin run. The only evidence a dead
 * check leaves is the ABSENCE of its output, and nobody counts absences.
 *
 * So the guard cannot be "watch for red". It has to be structural, at merge
 * time: a delegating caller with no `permissions:` block is refused here, on the
 * file, before it can spend another 104 days proving nothing. The falsifier is
 * the pre-fix `regression-claim-origin.yml`, reconstructed inline below — this
 * test fails against it, which is the only reason to believe it would fail
 * against the next one.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const WORKFLOW_DIR = fileURLToPath(new URL('../.github/workflows', import.meta.url))

/**
 * True when the workflow has at least one job that IS a reusable-workflow call.
 *
 * A job-level `uses:` is a job property, so it sits at exactly four spaces under
 * the two-space job key. A step-level `uses:` is a list item inside `steps:` and
 * always carries a `- `. Matching the job-property indent is what separates
 * "this workflow delegates" from "this workflow runs actions" — and every
 * workflow in this repo runs actions, so getting that wrong would either demand
 * a permissions block from all of them or from none.
 */
const delegatesToReusableWorkflow = (source: string): boolean => /^ {4}uses:/m.test(source)

/** True when the workflow declares a `permissions:` key at workflow or job level. */
const declaresPermissions = (source: string): boolean => /^ {0,4}permissions:/m.test(source)

const workflowFiles = readdirSync(WORKFLOW_DIR).filter((f) => /\.ya?ml$/.test(f))
const delegatingFiles = workflowFiles.filter((f) =>
  delegatesToReusableWorkflow(readFileSync(join(WORKFLOW_DIR, f), 'utf8'))
)

describe('workflows that call a reusable workflow declare their permissions', () => {
  it('finds workflow files to check at all', () => {
    // Without this, a bad path would empty the loop below and every assertion
    // would vacuously pass — the ss#2280 shape this whole file exists to refuse.
    expect(workflowFiles.length).toBeGreaterThan(10)
  })

  it('finds at least one delegating caller in this repo', () => {
    // The per-file assertions below are generated from this list. If the
    // detector silently stops matching — a YAML reformat, an indent change —
    // the loop empties and the suite goes green having checked nothing. That is
    // the same "absence reads as success" failure that hid ss#2441 for 104 days.
    expect(delegatingFiles).toContain('regression-claim-origin.yml')
  })

  for (const file of delegatingFiles) {
    const source = readFileSync(join(WORKFLOW_DIR, file), 'utf8')

    it(`${file} grants the permissions its called workflow needs`, () => {
      expect(
        declaresPermissions(source),
        `${file} calls a reusable workflow but declares no permissions:. This repo's ` +
          'default_workflow_permissions is read, a called workflow cannot be granted more ' +
          'than its caller holds, and the run is refused at startup — silently, because a ' +
          'startup_failure produces no check run for anything to alert on (ss#2441).'
      ).toBe(true)
    })
  }

  it('detects a delegating caller that declares nothing (the ss#2441 file, pre-fix)', () => {
    // The exact shape that produced 2,059 startup failures.
    const preFix = [
      'name: Regression Claim-Origin',
      '',
      'on:',
      '  issues:',
      '    types: [opened, labeled]',
      '',
      'jobs:',
      '  claim-origin:',
      "    if: contains(github.event.issue.labels.*.name, 'regression')",
      '    uses: venturecrane/crane-console/.github/workflows/regression-claim-origin-reusable.yml@abc123',
      '    secrets:',
      '      CRANE_RELAY_KEY: ${{ secrets.CRANE_RELAY_KEY }}',
      '',
    ].join('\n')

    expect(delegatesToReusableWorkflow(preFix)).toBe(true)
    expect(declaresPermissions(preFix)).toBe(false)
  })

  it('does not mistake a step-level action for a reusable-workflow call', () => {
    // If it did, it would demand a permissions block from every workflow in the
    // repo, the rule would be noise, and the real signal would be routed around.
    const stepsOnly = [
      'name: Verify',
      '',
      'on: [push]',
      '',
      'jobs:',
      '  check:',
      '    runs-on: ubuntu-latest',
      '    steps:',
      '      - uses: actions/checkout@v4',
      '      - run: npm ci',
      '',
    ].join('\n')

    expect(delegatesToReusableWorkflow(stepsOnly)).toBe(false)
  })
})
