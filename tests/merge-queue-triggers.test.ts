/**
 * Main merges through a merge queue (2026-10-21). The queue re-runs every
 * REQUIRED check on the `merge_group` event; a required check whose workflow
 * has no `merge_group` trigger never reports there, and every queued PR waits
 * at "Expected" until the queue times it out. Nothing merges.
 *
 * Falsifier: delete the `merge_group:` block from any workflow below and its
 * case goes red. Adding a required check means adding its workflow here.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parse } from 'yaml'

import {
  workflowJobs,
  workflowStep,
  workflowSteps,
  workflowTriggers,
} from './helpers/workflow-steps'

/** Required context -> the workflow that reports it. */
const REQUIRED: Record<string, string> = {
  'Typecheck, Lint, Format, Test': 'verify.yml',
  'Security Summary': 'security.yml',
  substrate: 'operator-substrate.yml',
  runner: 'operator-substrate.yml',
  overlay: 'operator-substrate.yml',
  'overlay-e2e': 'operator-substrate.yml',
  'Runtime ACs carry a verification ID': 'runtime-ac-proof.yml',
}

const workflow = (file: string): string => resolve(__dirname, '../.github/workflows', file)

/** Contexts reported by a job with no `name:`, so the context is the job key. */
const JOB_KEY_CONTEXTS = new Set(['substrate', 'runner', 'overlay', 'overlay-e2e'])

describe('workflows load at all', () => {
  // A workflow GitHub cannot parse runs on no event, so a required check in it
  // never reports, on a PR or in the queue: every merge freezes. On 2026-10-21
  // an unquoted `run: echo "Queue run: ..."` (a colon inside a plain scalar)
  // made runtime-ac-proof.yml invalid; a text match on the trigger passed it.
  const dir = resolve(__dirname, '../.github/workflows')
  for (const file of readdirSync(dir).filter((f) => /\.ya?ml$/.test(f))) {
    it(`${file} parses as YAML`, () => {
      expect(() => parse(readFileSync(resolve(dir, file), 'utf8'))).not.toThrow()
    })
  }
})

describe('merge queue: every required check reports on merge_group', () => {
  for (const [context, file] of Object.entries(REQUIRED)) {
    it(`${context} (${file})`, () => {
      const jobs = workflowJobs(workflow(file))
      const reports = JOB_KEY_CONTEXTS.has(context)
        ? jobs.some((j) => j.key === context && j.name === undefined)
        : jobs.some((j) => j.name === context)
      expect(reports, `no job in ${file} reports the context "${context}"`).toBe(true)
      expect(workflowTriggers(workflow(file)).merge_group).toEqual({
        types: ['checks_requested'],
      })
    })
  }

  it('every operator-substrate detect step diffs against the queue base (4 jobs)', () => {
    // Without merge_group.base_sha a queue run carries no base, the detect
    // step fails closed, and every suite runs (21 min) on every queued PR.
    const text = readFileSync(workflow('operator-substrate.yml'), 'utf8')
    expect(text.match(/merge_group\.base_sha \|\|/g)?.length).toBe(4)
  })

  it('the Semgrep baseline has a merge_group case (no baseline = a full audit that blocks the queue)', () => {
    const text = readFileSync(workflow('security.yml'), 'utf8')
    expect(text).toContain('QUEUE_BASE_SHA: ${{ github.event.merge_group.base_sha }}')
    expect(text).toMatch(/merge_group\)\n\s+baseline=\$\(git merge-base HEAD "\$QUEUE_BASE_SHA"/)
  })

  it('the runtime-AC gate reads the PR body only on pull_request, and passes a queue run', () => {
    const steps = workflowSteps(workflow('runtime-ac-proof.yml'))
    expect(steps.some((s) => s.if === "github.event_name == 'merge_group'")).toBe(true)
    expect(
      workflowStep(
        workflow('runtime-ac-proof.yml'),
        'Inspect the AC status table and resolve its IDs'
      ).if
    ).toBe("github.event_name == 'pull_request'")
  })
})
