/**
 * Unit coverage for the Law 9 merge gate (scripts/runtime-ac-proof.mjs).
 *
 * The gate's whole job is to be harder to satisfy than a self-declaration, so
 * the tests that matter are the ones proving it cannot be walked past: a
 * runtime AC marked met with a file:line must fail, and the template's own
 * placeholder row must not fail an otherwise-clean PR.
 *
 * @see docs/doctrine/agent-operating-doctrine.md - Law 9
 * @see docs/doctrine/wired-contract.md
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  checkRuntimeAcProof,
  findUnprovenRuntimeAcs,
  extractAcSection,
  formatViolations,
  LOOKUP_BATCH,
  resolveVerifyIds,
} from '../scripts/runtime-ac-proof.mjs'

const VERIFY_ID = 'vfy_01KYNVJ4VG90G26SZSYPXF05KY'

function pr(rows: string): string {
  return [
    '## Summary',
    'Something changed.',
    '',
    '## Acceptance criteria status',
    '',
    '| AC (verbatim from issue) | Status | Evidence |',
    '| ------------------------ | ------ | -------- |',
    rows,
    '',
    '## Test plan',
    '- [ ] `npm run verify` passes',
  ].join('\n')
}

describe('runtime AC proof: the gate blocks self-certification', () => {
  it('fails a runtime AC marked met with only a file:line', () => {
    const body = pr(
      '| (runtime) Operator adopts the new level | met | src/lib/entitlements.ts:42 |'
    )
    const violations = findUnprovenRuntimeAcs(body)
    expect(violations).toHaveLength(1)
    expect(violations[0].ac).toContain('Operator adopts')
  })

  it('fails a runtime AC marked met with empty evidence', () => {
    const body = pr('| (runtime) Secret deployed to the seat | met |  |')
    expect(findUnprovenRuntimeAcs(body)).toHaveLength(1)
  })

  it('passes a runtime AC backed by a crane_verify ID', () => {
    const body = pr(`| (runtime) Operator adopts the new level | met | ${VERIFY_ID} |`)
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('passes when the ID sits in prose alongside other evidence', () => {
    const body = pr(
      `| (runtime) Level raised as the client | met | raised on ashton-price seat, ${VERIFY_ID} |`
    )
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('rejects a malformed ID rather than reading it as absent-but-fine', () => {
    const body = pr('| (runtime) Operator adopts the new level | met | vfy_short |')
    expect(findUnprovenRuntimeAcs(body)).toHaveLength(1)
  })

  it('flags every unproven runtime row, not just the first', () => {
    const body = pr(
      [
        '| (runtime) Role granted on the seat | met | config.yaml:8 |',
        `| (runtime) Transport reachable | met | ${VERIFY_ID} |`,
        '| (runtime) Machine adopts config | met | see PR #2019 |',
      ].join('\n')
    )
    expect(findUnprovenRuntimeAcs(body)).toHaveLength(2)
  })
})

describe('runtime AC proof: what it deliberately leaves alone', () => {
  it('ignores repo-layer ACs, where a file:line is the right evidence', () => {
    const body = pr(
      '| (repo) Control renders for the admin role | met | src/pages/portal/x.astro:12 |'
    )
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('ignores untagged ACs, so pre-contract issues are not retroactively blocked', () => {
    const body = pr('| Legacy AC written before /wired existed | met | src/lib/thing.ts:3 |')
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('ignores a runtime AC that is deferred rather than claimed', () => {
    const body = pr('| (runtime) Machine adopts config | deferred | tracked in #2044 |')
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('ignores a runtime AC marked n/a', () => {
    const body = pr('| (runtime) Monitoring field emitted | n/a | no monitoring surface here |')
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('ignores the untouched template placeholder row', () => {
    const body = pr('|  | met / deferred / n/a | commit / file:line / explanation |')
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
  })

  it('stays silent on a PR with no AC section at all (chores)', () => {
    expect(findUnprovenRuntimeAcs('## Summary\n\nBumped a dependency.')).toEqual([])
  })

  it('stays silent on an empty or missing body', () => {
    expect(findUnprovenRuntimeAcs('')).toEqual([])
    expect(findUnprovenRuntimeAcs(undefined as unknown as string)).toEqual([])
  })
})

describe('runtime AC proof: section extraction', () => {
  it('stops at the next heading so later sections cannot leak rows in', () => {
    const body = pr('| (repo) Something | met | file.ts:1 |')
    const section = extractAcSection(body)
    expect(section).toContain('(repo) Something')
    expect(section).not.toContain('Test plan')
  })

  it('returns empty when the section is absent', () => {
    expect(extractAcSection('## Summary\n\nNothing here.')).toBe('')
  })

  it('is case-insensitive on the heading', () => {
    const body = '## ACCEPTANCE CRITERIA STATUS\n| (repo) X | met | f.ts:1 |'
    expect(extractAcSection(body)).toContain('(repo) X')
  })
})

/**
 * The ledger stage. A fake /verify/lookup that answers the way crane-context's
 * handleVerifyLookup does: `exists` for every asked ID, `records` for the ones
 * it holds. Recording the calls lets a test prove when the ledger was NOT asked.
 */
function fakeLedger(rows: Record<string, string>, opts: { status?: number; body?: unknown } = {}) {
  const calls: { url: string; key: string | null }[] = []
  const fetchImpl = (async (url: string, init?: { headers?: Record<string, string> }) => {
    calls.push({ url, key: init?.headers?.['X-Relay-Key'] ?? null })
    const ids = decodeURIComponent(new URL(url).searchParams.get('ids') ?? '').split(',')
    const body = opts.body ?? {
      exists: Object.fromEntries(ids.map((id) => [id, id in rows])),
      records: Object.fromEntries(
        ids.filter((id) => id in rows).map((id) => [id, { method: rows[id] }])
      ),
    }
    return new Response(JSON.stringify(body), { status: opts.status ?? 200 })
  }) as unknown as typeof fetch
  return { fetchImpl, calls }
}

const OTHER_ID = 'vfy_01M3CS0PTSBKC4QZK6PJHANRQ1'
const runtimeRow = (evidence: string) =>
  pr(`| (runtime) Ruleset requires the check | met | ${evidence} |`)

describe('runtime AC proof: the ID is resolved, not pattern-matched', () => {
  it('passes an ID the ledger holds as a live observation', async () => {
    const { fetchImpl, calls } = fakeLedger({ [VERIFY_ID]: 'live_state' })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result).toEqual({ violations: [], ledgerError: null, checked: 1 })
    expect(calls).toHaveLength(1)
    expect(calls[0].key).toBe('k')
    expect(calls[0].url).toContain('/verify/lookup?ids=')
  })

  it('fails a well-formed ID the ledger has never seen', async () => {
    // The case the regex could not catch: an ID-shaped string that was never
    // recorded, whether typed from memory or pasted from another PR's template.
    const { fetchImpl } = fakeLedger({})
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.violations).toHaveLength(1)
    expect(result.violations[0].reason).toMatch(/not in the verify ledger/)
  })

  it('fails an ID that is a doc read rather than an observation', async () => {
    const { fetchImpl } = fakeLedger({ [VERIFY_ID]: 'vendor_docs' })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.violations[0].reason).toMatch(/vendor_docs/)
  })

  it('passes when any one cited ID is an observation', async () => {
    const { fetchImpl } = fakeLedger({ [VERIFY_ID]: 'vendor_docs', [OTHER_ID]: 'fresh_process' })
    const result = await checkRuntimeAcProof(runtimeRow(`${VERIFY_ID}, ${OTHER_ID}`), {
      relayKey: 'k',
      fetchImpl,
    })
    expect(result.violations).toEqual([])
    expect(result.checked).toBe(2)
  })

  it('fails a lowercase or wrong-alphabet ID at the format stage without asking the ledger', async () => {
    // A ULID never contains I, L, O or U; `vfy_` + 26 of the wrong alphabet is a typo.
    const { fetchImpl, calls } = fakeLedger({})
    const bad = 'vfy_01m3cs0ptsbkc4qzk6pjhanrqI'
    const result = await checkRuntimeAcProof(runtimeRow(bad), { relayKey: 'k', fetchImpl })
    expect(result.violations[0].reason).toMatch(/malformed ID/)
    expect(calls).toHaveLength(0)
  })
})

describe('runtime AC proof: fails closed when the ledger cannot answer', () => {
  it('is red, with a reason, when no relay key reaches the run', async () => {
    const { fetchImpl, calls } = fakeLedger({ [VERIFY_ID]: 'live_state' })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { fetchImpl })
    expect(result.ledgerError).toMatch(/CRANE_RELAY_KEY/)
    expect(calls).toHaveLength(0)
  })

  it('is red when the ledger answers non-200', async () => {
    const { fetchImpl } = fakeLedger({}, { status: 401 })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.ledgerError).toMatch(/HTTP 401/)
  })

  it('is red when the ledger is unreachable', async () => {
    const fetchImpl = (async () => {
      throw new Error('getaddrinfo ENOTFOUND')
    }) as unknown as typeof fetch
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.ledgerError).toMatch(/unreachable.*ENOTFOUND/)
  })

  it('is red when the answer has the wrong shape, rather than reading it as "absent"', async () => {
    const { fetchImpl } = fakeLedger({}, { body: { ok: true } })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.ledgerError).toMatch(/exists/)
  })

  it('is red when an ID exists but the ledger omits its method', async () => {
    const { fetchImpl } = fakeLedger({}, { body: { exists: { [VERIFY_ID]: true } } })
    const result = await checkRuntimeAcProof(runtimeRow(VERIFY_ID), { relayKey: 'k', fetchImpl })
    expect(result.ledgerError).toMatch(/no method/)
  })
})

describe('runtime AC proof: a required check must report green on PRs that claim nothing', () => {
  // The ruleset requires this check, so it has to report on every PR. These
  // are the paths Dependabot and fork PRs take: no secret, no ledger.
  it.each([
    ['no body', ''],
    ['no AC section', '## Summary\n\nBumped a dependency.'],
    ['only repo-layer rows', pr('| (repo) Parser handles tabs | met | src/x.ts:1 |')],
    ['a runtime row deferred', pr('| (runtime) Seat adopts it | deferred | next reprovision |')],
  ])('%s: green without a key or a ledger call', async (_name, body) => {
    const { fetchImpl, calls } = fakeLedger({})
    const result = await checkRuntimeAcProof(body, { fetchImpl })
    expect(result).toEqual({ violations: [], ledgerError: null, checked: 0 })
    expect(calls).toHaveLength(0)
  })

  it('batches past the worker cap rather than sending one oversized request', async () => {
    const { fetchImpl, calls } = fakeLedger({})
    const ids = Array.from(
      { length: LOOKUP_BATCH + 1 },
      (_, i) => `vfy_${String(i).padStart(26, '0')}`
    )
    await resolveVerifyIds(ids, { relayKey: 'k', fetchImpl })
    expect(calls).toHaveLength(2)
  })
})

describe('runtime AC proof: the workflow wires the ledger stage', () => {
  // The script is only half the gate; a workflow still calling the format-only
  // function would pass every ID-shaped string, which is what this replaced.
  const workflow = readFileSync(
    resolve(__dirname, '../.github/workflows/runtime-ac-proof.yml'),
    'utf8'
  )

  it('calls the resolving entry point with the relay key', () => {
    expect(workflow).toContain('checkRuntimeAcProof')
    expect(workflow).toContain('CRANE_RELAY_KEY: ${{ secrets.CRANE_RELAY_KEY }}')
  })

  it('runs on every PR event a body change can arrive on, with no paths filter', () => {
    expect(workflow).toMatch(/types: \[opened, synchronize, reopened, edited/)
    expect(workflow).not.toMatch(/^\s+paths:/m)
  })

  it('keeps the job name the ruleset requires', () => {
    expect(workflow).toContain('name: Runtime ACs carry a verification ID')
  })
})

describe('runtime AC proof: the failure message tells you what to do', () => {
  it('names the AC, the evidence given, and the remedy', () => {
    const violations = findUnprovenRuntimeAcs(
      pr('| (runtime) Operator adopts the new level | met | src/x.ts:1 |')
    )
    const message = formatViolations(violations)
    expect(message).toContain('Operator adopts the new level')
    expect(message).toContain('src/x.ts:1')
    expect(message).toContain('crane_verify')
    expect(message).toContain('docs/doctrine/wired-contract.md')
  })

  it('renders empty evidence legibly rather than as a blank', () => {
    const message = formatViolations(findUnprovenRuntimeAcs(pr('| (runtime) X | met |  |')))
    expect(message).toContain('(empty)')
  })
})
