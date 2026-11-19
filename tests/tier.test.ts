/**
 * `.claude/bin/tier`: how much build process a change gets, from its paths.
 *
 * Every case runs the real command on real changed-path sets taken from the
 * touch map's own answer key (tests/fixtures/touch-map/replay.json), so the
 * tier follows the classifier's pinned answers rather than invented paths.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(__dirname, '..')
const TIER = resolve(ROOT, '.claude/bin/tier')

const replay: { prs: { number: number; paths: string[] }[] } = JSON.parse(
  readFileSync(resolve(__dirname, 'fixtures/touch-map/replay.json'), 'utf8')
)
const pathsOf = (n: number): string[] => {
  const pr = replay.prs.find((p) => p.number === n)
  if (!pr) throw new Error(`#${n} missing from the replay`)
  return pr.paths
}

function tier(args: string[], input = '') {
  const r = spawnSync(TIER, args, { input, encoding: 'utf8', cwd: ROOT })
  return { status: r.status, stdout: r.stdout, stderr: r.stderr, lines: r.stdout.split('\n') }
}

function tierJson(args: string[], input = '') {
  const r = tier([...args, '--json'], input)
  expect(r.status).toBe(0)
  return JSON.parse(r.stdout) as {
    tier: string
    review: string | null
    checkReview: string | null
    classes: Record<string, string[]>
    releaseTooling: string[]
    checkChanges: string[]
  }
}

// From the replay: #2681 docs only, #3357 a test only, #3330 fleet tooling
// only, #3906 a client lane, #3910 skills, CLAUDE.md and a test (none).
const DOCS_ONLY = pathsOf(2681)
const TEST_ONLY = pathsOf(3357)
const FLEET_ONLY = pathsOf(3330)
const CLIENT_LANE = pathsOf(3906)
const NOT_CLIENT = pathsOf(3910)

describe('paths as an agent actually holds them', () => {
  it('an absolute path into the repo gets the same tier as its relative form', () => {
    const absolute = CLIENT_LANE.map((p) => resolve(ROOT, p))
    expect(tierJson(CLIENT_LANE).tier).toBe('fix')
    expect(tierJson(absolute).tier).toBe('fix')
  })

  it('a ./ path gets the same tier too', () => {
    expect(tierJson(CLIENT_LANE.map((p) => `./${p}`)).tier).toBe('fix')
  })

  it('release tooling beside a client lane takes the client tier and drops the review line', () => {
    const r = tierJson([...FLEET_ONLY, ...CLIENT_LANE])
    expect(r.tier).toBe('fix')
    expect(r.review).toBeNull()
    expect(r.releaseTooling.length).toBeGreaterThan(0)
  })

  it('a seat provisioning script is release tooling', () => {
    const r = tierJson(['operator/bin/reprovision.sh'])
    expect(r.tier).toBe('none')
    expect(r.review).not.toBeNull()
  })
})

describe('tier: decided from the paths', () => {
  it('a docs-only change is none, with no review line', () => {
    const r = tier(DOCS_ONLY)
    expect(r.status).toBe(0)
    expect(r.lines[0]).toBe('tier: none')
    expect(r.stdout).not.toMatch(/^review:/m)
  })

  it('a test-only change is none', () => {
    expect(tier(TEST_ONLY).lines[0]).toBe('tier: none')
  })

  it('a fleet tooling change is none plus the bug review line', () => {
    const r = tier(FLEET_ONLY)
    expect(r.lines[0]).toBe('tier: none')
    expect(r.lines[1]).toMatch(/^review: bug review required/)
    const j = tierJson(FLEET_ONLY)
    expect(j.releaseTooling).toEqual(FLEET_ONLY)
  })

  it('a client-lane change is fix, and names its class and paths', () => {
    const r = tier(CLIENT_LANE)
    expect(r.lines[0]).toBe('tier: fix')
    const j = tierJson(CLIENT_LANE)
    expect(j.tier).toBe('fix')
    expect(Object.keys(j.classes).length).toBeGreaterThan(0)
    for (const list of Object.values(j.classes))
      for (const p of list) expect(CLIENT_LANE).toContain(p)
  })

  it('the same change with --new is capability', () => {
    expect(tier([...CLIENT_LANE, '--new']).lines[0]).toBe('tier: capability')
    expect(tierJson(['--new', ...CLIENT_LANE]).tier).toBe('capability')
  })

  it('a non-client change stays none even with --new', () => {
    expect(tierJson([...NOT_CLIENT, '--new']).tier).toBe('none')
  })

  it('a mix takes the highest tier', () => {
    expect(tierJson([...DOCS_ONLY, ...TEST_ONLY, ...FLEET_ONLY, ...CLIENT_LANE]).tier).toBe('fix')
    const j = tierJson([...DOCS_ONLY, ...FLEET_ONLY, ...CLIENT_LANE, '--new'])
    expect(j.tier).toBe('capability')
    expect(j.review).toBeNull()
  })

  it('reads paths from stdin when no paths are given', () => {
    const j = tierJson([], CLIENT_LANE.join('\n') + '\n')
    expect(j.tier).toBe('fix')
    expect(tierJson([], DOCS_ONLY.join('\n')).tier).toBe('none')
  })

  it('no paths at all exits 2 with a usage line', () => {
    const r = tier([], '')
    expect(r.status).toBe(2)
    expect(r.stderr).toMatch(/^usage: tier/)
    expect(tier(['--new'], '\n\n').status).toBe(2)
  })
})

// The checks themselves. #2726 changed a CI path list and its workflow; the
// rest are files in the tree today, one per entry in the list.
const CHECK_PATHS = [
  '.github/workflows/verify.yml',
  '.github/operator-substrate-paths.txt',
  'operator/bin/goldens/allowed-instrument-changes.json',
  'scripts/sprawl/concepts.ts',
  'scripts/sprawl-audit.ts',
  'tests/sprawl-ratchet.test.ts',
  'tests/text-pin-ratchet.test.ts',
  'tests/operator-module-size.test.ts',
  'operator/bin/tests/test_function_size_ratchet.py',
  'operator/bin/pyright-ratchet.py',
  '.husky/pre-push',
  '.claude/hooks/worktree-guard.mjs',
  '.lintstagedrc.json',
  '.github/CODEOWNERS',
  'eslint.config.js',
  'scripts/runtime-ac-proof.mjs',
  'scripts/sprawl/literal-baseline.json',
  'scripts/sprawl/text-pin-baseline.json',
  'operator/contracts/operator-module-size.json',
  'operator/contracts/operator-function-size.json',
  'operator/contracts/operator-shell-size.json',
  'operator/contracts/operator-pyright-baseline.json',
  'operator/contracts/dated-entry-baseline.json',
  'operator/contracts/gate-coverage-snapshot.json',
  'operator/contracts/gate-coverage-snapshot.overlay.json',
  'operator/bin/lib/golden_diff.py',
  'operator/runners/jobs/tests/goldens_kit.py',
  'operator/workspace_broker/tests/ledger_golden_kit.py',
  'operator/hermes-smd-overlay/tests/decisions/goldens/allowed-changes.json',
  'operator/runners/jobs/tests/goldens/wire.allowed-changes.json',
  'operator/runners/jobs/tests/host_cross_version_allowed.json',
  'operator/workspace_broker/tests/goldens/ledgers/allowed-changes.json',
]

// Ordinary files beside the checks: tests, docs, contracts that are not a
// ratchet's numbers, and golden snapshots themselves (their changes are what
// the allowed lists record).
const NOT_CHECKS = [
  'tests/tier.test.ts',
  'tests/operator-entrypoint-supervisor.test.ts',
  'docs/handbook/building-the-platform.md',
  'README.md',
  'operator/contracts/job-lanes.yaml',
  'operator/contracts/seat-config-callers.json',
  'operator/runners/jobs/tests/test_lane_goldens.py',
  'operator/hermes-smd-overlay/tests/decisions/goldens/existing/test_act_delivery.json',
  'scripts/tier.mjs',
]
const CHECK_LINE =
  'review: this changes a check; show what it caught before still fails (replay a past failure)'

describe('a change to a check gets its own review line', () => {
  it('every path in the list is real', () => {
    for (const p of CHECK_PATHS) expect(existsSync(resolve(ROOT, p)), p).toBe(true)
  })

  it('every check path is named, and the check line prints', () => {
    // One call for all of them (matching is per path); a call per path costs
    // a node start each and outran the test timeout.
    expect(tierJson(CHECK_PATHS).checkChanges).toEqual(CHECK_PATHS)
    expect(tier(CHECK_PATHS).lines).toContain(CHECK_LINE)
    expect(tier(['.husky/pre-push']).lines).toContain(CHECK_LINE)
  })

  it('#2726, a CI path list and its workflow, prints it', () => {
    const j = tierJson(pathsOf(2726))
    expect(j.checkReview).not.toBeNull()
    expect(j.checkChanges).toEqual(
      expect.arrayContaining([
        '.github/operator-substrate-paths.txt',
        '.github/workflows/operator-substrate.yml',
      ])
    )
  })

  it('an ordinary test, doc, contract or golden snapshot does not', () => {
    for (const p of NOT_CHECKS) expect(existsSync(resolve(ROOT, p)), p).toBe(true)
    const j = tierJson(NOT_CHECKS)
    expect(j.checkChanges).toEqual([])
    expect(j.checkReview).toBeNull()
  })

  it('a docs, test or client-lane change does not', () => {
    for (const set of [DOCS_ONLY, TEST_ONLY, CLIENT_LANE, FLEET_ONLY]) {
      const j = tierJson(set)
      expect(j.checkReview).toBeNull()
      expect(j.checkChanges).toEqual([])
      expect(tier(set).lines).not.toContain(CHECK_LINE)
    }
  })

  it('beside release tooling both review lines print', () => {
    const r = tier([...FLEET_ONLY, '.husky/pre-push'])
    expect(r.lines[0]).toBe('tier: none')
    expect(r.lines[1]).toMatch(/^review: bug review required/)
    expect(r.lines[2]).toBe(CHECK_LINE)
  })

  it('a release workflow is both release tooling and a check', () => {
    const j = tierJson(['.github/workflows/deploy.yml'])
    expect(j.review).not.toBeNull()
    expect(j.checkReview).not.toBeNull()
  })

  it('beside a client lane the check line still prints, though the release line drops', () => {
    const j = tierJson([...CLIENT_LANE, ...FLEET_ONLY, '.github/workflows/verify.yml'])
    expect(j.tier).toBe('fix')
    expect(j.review).toBeNull()
    expect(j.checkReview).not.toBeNull()
    expect(j.checkChanges).toEqual(['.github/workflows/verify.yml'])
  })
})
