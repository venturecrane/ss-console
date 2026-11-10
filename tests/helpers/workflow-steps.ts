/**
 * A GitHub Actions workflow read as parsed YAML, so a test can execute a step's
 * `run:` body exactly as the runner does, or check the wiring structurally,
 * instead of asserting on the workflow's wording.
 */
import { readFileSync } from 'node:fs'

import { parse } from 'yaml'

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

const str = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined)

type WorkflowStep = {
  job: string
  name?: string
  uses?: string
  run?: string
  if?: string
  env: Record<string, unknown>
  with: Record<string, unknown>
}

function parsedWorkflow(workflowPath: string): Record<string, unknown> {
  const doc: unknown = parse(readFileSync(workflowPath, 'utf8'))
  if (!isRecord(doc)) throw new Error(`${workflowPath} is not a YAML mapping`)
  return doc
}

/** The `on:` block: trigger name to its configuration. */
export function workflowTriggers(workflowPath: string): Record<string, unknown> {
  const on = parsedWorkflow(workflowPath).on
  return isRecord(on) ? on : {}
}

/** Each job's key and display name; a required context is the name, or the key when unnamed. */
export function workflowJobs(workflowPath: string): { key: string; name?: string }[] {
  const jobs = parsedWorkflow(workflowPath).jobs
  return Object.entries(isRecord(jobs) ? jobs : {}).map(([key, def]) => ({
    key,
    name: isRecord(def) ? str(def.name) : undefined,
  }))
}

/** Every step of every job, in file order. */
export function workflowSteps(workflowPath: string): WorkflowStep[] {
  const jobs = parsedWorkflow(workflowPath).jobs
  const out: WorkflowStep[] = []
  for (const [job, def] of Object.entries(isRecord(jobs) ? jobs : {})) {
    const steps = isRecord(def) && Array.isArray(def.steps) ? def.steps : []
    for (const step of steps) {
      if (!isRecord(step)) continue
      out.push({
        job,
        name: str(step.name),
        uses: str(step.uses),
        run: str(step.run),
        if: str(step.if),
        env: isRecord(step.env) ? step.env : {},
        with: isRecord(step.with) ? step.with : {},
      })
    }
  }
  return out
}

/** Every named step that has a `run:` body, in file order, keyed by step name. */
export function workflowStepRuns(workflowPath: string): Map<string, string> {
  const runs = new Map<string, string>()
  for (const s of workflowSteps(workflowPath)) if (s.name && s.run) runs.set(s.name, s.run)
  return runs
}

/** The `run:` body of the step named `stepName`; throws when there is none. */
export function workflowStepRun(workflowPath: string, stepName: string): string {
  const run = workflowStepRuns(workflowPath).get(stepName)
  if (run === undefined) throw new Error(`no step "${stepName}" with a run body in ${workflowPath}`)
  return run
}

/** The step named `stepName`; throws when there is none. */
export function workflowStep(workflowPath: string, stepName: string): WorkflowStep {
  const step = workflowSteps(workflowPath).find((s) => s.name === stepName)
  if (!step) throw new Error(`no step "${stepName}" in ${workflowPath}`)
  return step
}
