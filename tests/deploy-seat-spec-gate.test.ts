/**
 * The publish job's seat-spec signing is inert until it is enabled.
 *
 * GitHub auto-creates an environment a job references on its first run, with
 * no branch protection. An ungated `environment: seat-spec` would therefore
 * create an UNPROTECTED environment named for the signing key, and any secret
 * later added to it would be readable from any branch's workflow. So the
 * reference, and the key itself, are gated on vars.SEAT_SPEC_ENABLED: until the
 * protected environment exists and the variable is set, the job names no
 * environment and publishes customer.yaml exactly as before.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const DEPLOY = readFileSync(join(REPO_ROOT, '.github', 'workflows', 'deploy.yml'), 'utf8')

/** The publish-customer-configs job's text, up to the next top-level job. */
function publishJob(source: string): string {
  const start = source.indexOf('\n  publish-customer-configs:\n')
  expect(start).toBeGreaterThan(-1)
  const rest = source.slice(start + 1)
  const next = rest.slice(1).search(/\n {2}[a-z][a-z0-9-]*:\n/)
  return next === -1 ? rest : rest.slice(0, next + 1)
}

const GATE = "vars.SEAT_SPEC_ENABLED == 'true'"

function violations(job: string): string[] {
  const out: string[] = []
  const lines = job.split('\n').filter((l) => !/^\s*#/.test(l))
  for (const line of lines) {
    if (/^\s+environment:/.test(line) && !line.includes(GATE)) out.push(`ungated: ${line.trim()}`)
    if (line.includes('secrets.SEAT_SPEC_SIGNING_KEY') && !line.includes(GATE)) {
      out.push(`ungated: ${line.trim()}`)
    }
  }
  if (!lines.some((l) => l.includes('secrets.SEAT_SPEC_SIGNING_KEY')))
    out.push('no signing key wired')
  return out
}

describe('deploy.yml publish-customer-configs: seat-spec signing is gated', () => {
  it('names the seat-spec environment and reads the key only when SEAT_SPEC_ENABLED is true', () => {
    expect(violations(publishJob(DEPLOY))).toEqual([])
  })

  it('the check can fail: an ungated environment or key is caught', () => {
    const job = publishJob(DEPLOY)
    const ungatedEnv = job.replace(/environment: .*/, 'environment: seat-spec')
    expect(violations(ungatedEnv).some((v) => v.includes('environment: seat-spec'))).toBe(true)
    const ungatedKey = job.replace(
      /SEAT_SPEC_SIGNING_KEY: .*/,
      'SEAT_SPEC_SIGNING_KEY: ${{ secrets.SEAT_SPEC_SIGNING_KEY }}'
    )
    expect(violations(ungatedKey).length).toBeGreaterThan(0)
  })

  it('no other job in deploy.yml reads the signing key', () => {
    const outside = DEPLOY.replace(publishJob(DEPLOY), '')
    expect(outside).not.toContain('SEAT_SPEC_SIGNING_KEY')
  })
})
