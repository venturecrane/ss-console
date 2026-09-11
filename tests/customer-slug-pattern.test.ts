/**
 * One customer-slug pattern, five places (#2285).
 *
 * The defect this file exists to prevent: four independently-authored slug
 * guards drifted apart, and the LOOSEST of them was the one that wrote. The
 * CI publisher and syncer accepted `^[a-z0-9-]+$` — which admits `-acme`,
 * `---`, and a single character — while the runtime demanded
 * `^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$`. So a malformed slug provisioned,
 * published to R2, projected into D1, and only then failed at seat boot as
 * what operator/adapter/namespace_assertion.py calls "a bootstrap-time
 * invariant failure" — the most expensive place to find out.
 *
 * The guard is deliberately shaped as a MATRIX, not as five separate literal
 * assertions. Asserting each file contains the right string catches an edited
 * literal but not an added `||` clause, a case-insensitive flag, or a second
 * guard layered on top. Running one candidate table through the patterns as
 * they are actually written and demanding identical verdicts catches any
 * divergence however it arrives.
 *
 * Patterns are EXTRACTED from source rather than restated here. A test that
 * carries its own copy of the pattern passes happily while the shipped guards
 * rot.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))

function read(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

/**
 * The canonical shape, stated once for documentation. It is NOT the thing
 * under test — every verdict below comes from a pattern lifted out of a
 * shipped file. If this constant and the extracted patterns disagree, the
 * extracted ones win and the matrix says so.
 */
const CANONICAL = '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$'

/** Pull the one bracketed pattern out of a line matched by `anchor`. */
function extractPattern(source: string, anchor: RegExp, label: string): string {
  const line = source.split('\n').find((l) => anchor.test(l))
  if (line === undefined) throw new Error(`${label}: no line matched ${String(anchor)}`)
  const m = /\^[^\s]*\$/.exec(line.replace(/\]\]\s*;.*$/, ''))
  if (m === null) throw new Error(`${label}: no anchored pattern in line: ${line.trim()}`)
  return m[0]
}

const PROVISIONER = extractPattern(
  read('../operator/bin/provision-customer.sh'),
  /if \[\[ ! "\$\{SLUG\}" =~ \^/,
  'provision-customer.sh'
)

const CI_PUBLISH = extractPattern(
  read('../scripts/ci-publish-customer-configs.sh'),
  /if \[\[ ! "\$slug" =~ \^/,
  'ci-publish-customer-configs.sh'
)

const CI_SYNC = extractPattern(
  read('../scripts/ci-sync-customer-configs.sh'),
  /if \[\[ ! "\$slug" =~ \^/,
  'ci-sync-customer-configs.sh'
)

/**
 * The R2 key guard wraps the slug in a fixed prefix/suffix. Extracted whole,
 * then applied to a composed key so it is exercised exactly as the publisher
 * exercises it.
 */
const R2_KEY = extractPattern(
  read('../scripts/ci-publish-customer-configs.sh'),
  /if \[\[ ! "\$key" =~ \^vaults\//,
  'ci-publish assert_config_key'
)

/** The runtime pattern, read out of the Python source. */
const RUNTIME = (() => {
  const src = read('../operator/adapter/namespace_assertion.py')
  const m = /_SLUG_PATTERN = re\.compile\(r"(\^[^"]*\$)"\)/.exec(src)
  if (m === null) throw new Error('namespace_assertion.py: _SLUG_PATTERN not found')
  return m[1]
})()

/**
 * Candidates. The first four are the slugs that exist in
 * `operator/customers/` today — a fix that rejects a live seat is worse than
 * the bug it fixes, so they lead the table. The rest are the edge cases that
 * split the four patterns apart in the #2285 audit.
 */
const CANDIDATES: readonly string[] = [
  // live seats
  'ashton-price',
  'pilot-smokeball',
  'scott',
  'smd-staging',
  // shape edges that MUST be accepted (2..40 chars). 39 and 40 both exceed the
  // provisioner's old 32-char ceiling — the divergence in the other direction.
  'ab',
  'a1',
  'a-b',
  'a'.repeat(39),
  'a'.repeat(40),
  // shape edges that MUST be rejected
  'a', // 1 char: accepted by the old CI guard, rejected at boot
  'acme-', // trailing dash: same
  '-acme', // leading dash: same
  '---', // all dashes: same
  'a'.repeat(41), // over the ceiling
  'ACME',
  'bad.slug',
  'a_b',
  'a/b',
]

/** Candidates no guard may accept. Pinned so unanimous-but-wrong still fails. */
const MUST_REJECT: readonly string[] = [
  'a',
  'acme-',
  '-acme',
  '---',
  'a'.repeat(41),
  'ACME',
  'bad.slug',
  'a_b',
  'a/b',
]

/** The four live seats, asserted separately so a regression names them. */
const LIVE_SLUGS: readonly string[] = ['ashton-price', 'pilot-smokeball', 'scott', 'smd-staging']

/**
 * Evaluate a bash ERE the way bash evaluates it — via bash, not via a
 * JavaScript translation. `[[ =~ ]]` and JS RegExp agree on this dialect
 * today, but "agree today" is exactly the assumption that produced #2285.
 */
function bashMatches(pattern: string, subject: string): boolean {
  const script = `[[ "$1" =~ ${pattern} ]] && echo YES || echo no`
  const out = execFileSync('bash', ['-c', script, 'bash', subject], { encoding: 'utf8' })
  return out.trim() === 'YES'
}

/** Evaluate the runtime pattern in Python, for the same reason. */
function pythonMatches(pattern: string, subjects: readonly string[]): boolean[] {
  const program = [
    'import json, re, sys',
    'pat = re.compile(sys.argv[1])',
    'subjects = json.loads(sys.argv[2])',
    'print(json.dumps([bool(pat.match(s)) for s in subjects]))',
  ].join('\n')
  const out = execFileSync('python3', ['-c', program, pattern, JSON.stringify(subjects)], {
    encoding: 'utf8',
  })
  return JSON.parse(out) as boolean[]
}

describe('customer slug pattern: one shape, every guard (#2285)', () => {
  it('every guard carries the canonical pattern', () => {
    // Documentation-level check. The matrix below is the real guard; this one
    // exists so a failure names the pattern that drifted rather than only the
    // candidate that exposed it.
    expect({
      provisioner: PROVISIONER,
      ciPublish: CI_PUBLISH,
      ciSync: CI_SYNC,
      runtime: RUNTIME,
    }).toEqual({
      provisioner: CANONICAL,
      ciPublish: CANONICAL,
      ciSync: CANONICAL,
      runtime: CANONICAL,
    })
    // The key guard embeds the same slug shape between fixed segments.
    expect(R2_KEY).toBe('^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/customer\\.yaml$')
  })

  it('all five guards return identical verdicts on every candidate', () => {
    const runtimeVerdicts = pythonMatches(RUNTIME, CANDIDATES)

    const disagreements: string[] = []
    const matrix: Record<string, Record<string, boolean>> = {}

    CANDIDATES.forEach((slug, i) => {
      const verdicts = {
        provisioner: bashMatches(PROVISIONER, slug),
        ciPublish: bashMatches(CI_PUBLISH, slug),
        ciSync: bashMatches(CI_SYNC, slug),
        r2Key: bashMatches(R2_KEY, `vaults/${slug}/customer.yaml`),
        runtime: runtimeVerdicts[i],
      }
      matrix[slug] = verdicts

      const distinct = new Set(Object.values(verdicts))
      if (distinct.size > 1) {
        disagreements.push(
          `${JSON.stringify(slug)}: ${Object.entries(verdicts)
            .map(([k, v]) => `${k}=${v ? 'accept' : 'reject'}`)
            .join(' ')}`
        )
      }
    })

    // The failure message is the whole point: it names which guard split from
    // which, on which slug, rather than reporting a bare boolean.
    expect(disagreements, `slug guards disagree:\n  ${disagreements.join('\n  ')}`).toEqual([])

    // A matrix where every guard agrees but agrees on the WRONG answer would
    // pass the check above, so pin the verdicts themselves.
    for (const slug of LIVE_SLUGS) {
      expect(matrix[slug]?.runtime, `live seat ${slug} must be accepted`).toBe(true)
    }
    for (const slug of MUST_REJECT) {
      expect(matrix[slug]?.runtime, `${slug} must be rejected`).toBe(false)
    }
    for (const slug of ['a'.repeat(39), 'a'.repeat(40)]) {
      expect(matrix[slug]?.runtime, `${slug.length}-char slug must be accepted`).toBe(true)
    }
  })

  it('the pattern accepts every slug in operator/customers/', () => {
    // The falsifier for "a fix that rejects a live seat". Reads the directory
    // rather than trusting the LIVE_SLUGS list above, so a seat added after
    // this test was written is covered without anyone remembering to add it.
    const dirs = execFileSync('bash', ['-c', 'ls -1 operator/customers'], {
      cwd: REPO_ROOT,
      encoding: 'utf8',
    })
      .split('\n')
      .map((d) => d.trim())
      .filter((d) => d.length > 0 && !d.startsWith('_'))

    expect(dirs.length).toBeGreaterThan(0)
    const rejected = dirs.filter((d) => !bashMatches(PROVISIONER, d))
    expect(
      rejected,
      `real customer dirs rejected by the canonical pattern: ${rejected.join(', ')}`
    ).toEqual([])
  })
})

/**
 * Retired seats stay retired.
 *
 * `pilot-law` was authored 2026-06-05 for the ADR 0038 6 Clio-sandbox law wedge
 * and never provisioned: no Fly app, no `customer_configs` row. It sat in
 * `operator/customers/` for eleven weeks, and because seat enumeration walks
 * AUTHORED directories rather than provisioned seats, every terminal-state
 * reconciler run held on it with a DNS failure for a machine that never
 * existed. Retired in full 2026-08-25 -- git, and the orphaned prod R2 object
 * at `vaults/pilot-law/customer.yaml`.
 *
 * This is a guard, not a note. A seat directory is cheap to recreate and the
 * cost of its return is a daily false hold; the R2 publisher and the
 * terminal-state reconciler both key off directory presence, so a reappearing
 * directory silently re-arms both. The second assertion is the one that
 * generalises: it fails for ANY directory nobody listed, not just this slug.
 *
 * If pilot-law is ever genuinely stood up, update this test in the same PR that
 * provisions it. A visible decision, not a silent one.
 */
describe('retired seats', () => {
  it('pilot-law has no seat directory', () => {
    expect(existsSync(resolve(REPO_ROOT, 'operator/customers/pilot-law'))).toBe(false)
  })

  /**
   * `smd` -- customer-zero, the June 2026 bring-up seat -- was retired
   * 2026-09-03 by Captain directive. Unlike pilot-law it HAD been provisioned:
   * a started Machine billing for nothing since its last activity on 07-13,
   * and once stopped, a daily HOLD on the audit-chain run (a stopped Machine
   * still resolves in DNS, so it is held rather than skipped). Fly app and
   * volume destroyed, `customer_configs` row deleted, R2 vault removed,
   * healthchecks ping deleted, each with a negative probe on the PR. Its
   * customer.yaml is in git history for when it is stood up again -- and when
   * it is, this assertion is updated in the same PR, as a visible decision.
   */
  it('smd has no seat directory', () => {
    expect(existsSync(resolve(REPO_ROOT, 'operator/customers/smd'))).toBe(false)
  })

  it('the live-seat list matches the directories on disk', () => {
    const onDisk = readdirSync(resolve(REPO_ROOT, 'operator/customers'), {
      withFileTypes: true,
    })
      .filter((e) => e.isDirectory() && !e.name.startsWith('_') && !e.name.startsWith('.'))
      .map((e) => e.name)
      .sort()
    expect(onDisk).toEqual([...LIVE_SLUGS].sort())
  })
})
