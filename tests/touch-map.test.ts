/**
 * The touch map (scripts/lib/touch-map.mjs): which changed paths reach a client.
 *
 * The fixture is a replay of every PR merged 2026-09-01 to 2026-11-09 (plus
 * the closed #3882 and #3883), holding derived fields only: number, changed
 * paths, whether the body carried a `(runtime)` row, and the class the map
 * gave it when the fixture was cut. The repo is public, so no body and no
 * title is committed.
 *
 * Two guards. The named PRs pin both sides of the line the plan drew. And the
 * whole replay is re-classified on every run, so a glob change that moves any
 * of 1,122 PRs across the line shows up as a diff to re-cut deliberately, not
 * as silent drift.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { classifyPath, classifyPaths } from '../scripts/lib/touch-map.mjs'

interface ReplayPr {
  number: number
  class: 'send-path' | 'client-lane' | 'seat' | 'none'
  hasRuntimeRow: boolean
  paths: string[]
}

const replay: { prs: ReplayPr[] } = JSON.parse(
  readFileSync(resolve(__dirname, 'fixtures/touch-map/replay.json'), 'utf8')
)
const byNumber = new Map(replay.prs.map((p) => [p.number, p]))

describe('touch map: the replay pins both sides of the line', () => {
  // #3723 sat inside the "#3722-3726" range the plan named, but it changes the
  // plan gate, the primer and doctrine: no client request runs through it, so
  // it is correctly `none` and is not in this list.
  const CLIENT_SURFACE = [3711, 3717, 3722, 3724, 3725, 3726, 3093, 3882, 3883, 3906, 3852]
  const NOT_CLIENT = [3723, 3909, 3910, 3912, 3913]

  for (const n of CLIENT_SURFACE) {
    it(`#${n} is a client surface`, () => {
      const pr = byNumber.get(n)
      expect(pr, `#${n} missing from the replay`).toBeDefined()
      expect(classifyPaths(pr!.paths).clientSurface).toBe(true)
    })
  }

  for (const n of NOT_CLIENT) {
    it(`#${n} is not a client surface`, () => {
      const pr = byNumber.get(n)
      expect(pr, `#${n} missing from the replay`).toBeDefined()
      expect(classifyPaths(pr!.paths).cls).toBe('none')
    })
  }

  it('no docs-only PR is a client surface', () => {
    const docsOnly = replay.prs.filter(
      (p) => p.paths.length > 0 && p.paths.every((x) => /^docs\//.test(x) || /^[^/]+\.md$/.test(x))
    )
    expect(docsOnly.length).toBeGreaterThan(10)
    expect(
      docsOnly.filter((p) => classifyPaths(p.paths).clientSurface).map((p) => p.number)
    ).toEqual([])
  })

  it('no dependency-bump PR is a client surface', () => {
    const bumps = replay.prs.filter(
      (p) =>
        p.paths.length > 0 &&
        p.paths.every((x) =>
          /(^|\/)(package(-lock)?\.json|requirements[^/]*\.txt|\.github\/workflows\/[^/]+)$/.test(x)
        )
    )
    expect(bumps.length).toBeGreaterThan(10)
    expect(bumps.filter((p) => classifyPaths(p.paths).clientSurface).map((p) => p.number)).toEqual(
      []
    )
  })

  it('every replayed PR still gets the class the fixture was cut with', () => {
    const moved = replay.prs
      .filter((p) => classifyPaths(p.paths).cls !== p.class)
      .map((p) => `#${p.number}: ${p.class} -> ${classifyPaths(p.paths).cls}`)
    expect(moved).toEqual([])
  })
})

describe('touch map: single paths', () => {
  it.each([
    ['operator/hermes-smd-overlay/plugins/hermes-smd-reply/notice.py', 'send-path'],
    ['operator/workspace_broker/reply_binding.py', 'send-path'],
    ['operator/connectors/msgraph-mail/msgraph_mail_connector/send.py', 'send-path'],
    ['operator/contracts/send-render.yaml', 'send-path'],
    ['operator/runners/jobs/jobs/drafting/run.py', 'client-lane'],
    ['operator/connectors/smokeball/smokeball_connector/page_finder.py', 'client-lane'],
    ['operator/skills/matter-inbox-router/SKILL.md', 'client-lane'],
    ['operator/workspace_broker/verbs.py', 'client-lane'],
    ['operator/hermes-smd-overlay/shared/heartbeat.py', 'client-lane'],
    ['operator/templates/Dockerfile', 'seat'],
    ['operator/hermes-smd-overlay/spec_applier/applier.py', 'seat'],
    ['operator/customers/pilot-smokeball/customer.yaml', 'seat'],
    ['operator/bin/provision-customer.sh', 'seat'],
    ['operator/bin/lib/stage-records-inputs.sh', 'seat'],
    ['operator/customers/_template/customer.yaml', 'none'],
    ['operator/bin/lib/fleet/build.py', 'none'],
    ['operator/runners/medchron/tests/test_records_run.py', 'none'],
    ['operator/runners/medchron/tests/records_testkit.py', 'none'],
    ['operator/connectors/_contract/tests/fixtures/facts/x.json', 'none'],
    ['operator/hermes-smd-overlay/tests/test_library_accept.py', 'none'],
    ['scripts/runtime-ac-proof.mjs', 'none'],
    ['src/pages/portal/index.astro', 'none'],
    ['docs/handbook/operator-platform.md', 'none'],
    ['.claude/hooks/plan-premise-gate.mjs', 'none'],
  ])('%s is %s', (path, cls) => {
    expect(classifyPath(path)).toBe(cls)
  })

  it('the most specific class wins for a mixed PR', () => {
    expect(
      classifyPaths([
        'operator/templates/Dockerfile',
        'operator/runners/jobs/jobs/x.py',
        'operator/workspace_broker/transmit_verbs.py',
      ]).cls
    ).toBe('send-path')
    expect(classifyPaths(['operator/templates/Dockerfile', 'docs/x.md']).cls).toBe('seat')
  })

  it('a test-only change to a lane is not a client surface', () => {
    expect(
      classifyPaths(['operator/runners/medchron/tests/test_records_run.py']).clientSurface
    ).toBe(false)
  })
})
