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
  checkTouchRule,
  naReason,
  findUnprovenRuntimeAcs,
  formatViolations,
  LOOKUP_BATCH,
  resolveVerifyIds,
} from '../scripts/runtime-ac-proof.mjs'

const VERIFY_ID = 'vfy_01KYNVJ4VG90G26SZSYPXF05KY'
const NOW = new Date('2026-11-09T12:00:00Z')

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

  it('accepts a runtime AC deferred with an owner and an enforce_by date', () => {
    const body = pr(
      '| (runtime) Machine adopts config | deferred | owner: release lead; enforce_by: 2026-11-20; tracked in #2044 |'
    )
    expect(findUnprovenRuntimeAcs(body, { now: NOW })).toEqual([])
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

describe('runtime AC proof: where it looks', () => {
  it('reads a status table under any heading, not only "Acceptance criteria status"', () => {
    for (const heading of ['## Acceptance', '## Acceptance criteria', '## Evidence', '## Status']) {
      const body = `${heading}\n\n| AC | Status | Evidence |\n|---|---|---|\n| (runtime) X | not met | later |`
      expect(findUnprovenRuntimeAcs(body), heading).toHaveLength(1)
    }
  })

  it('finds the status and evidence columns by header, in any order', () => {
    const body = '| AC | Evidence | Status |\n|---|---|---|\n| (runtime) X | src/x.ts:1 | met |'
    expect(findUnprovenRuntimeAcs(body)[0].reason).toMatch(/no crane_verify ID/)
  })

  it('reads the runtime tag from a Layer or Kind column', () => {
    const body =
      '| # | Gate | Layer | Status | Evidence |\n|---|---|---|---|---|\n| 4 | Token on the seat | runtime | met | seat-probe count = 1 |'
    const v = findUnprovenRuntimeAcs(body)
    expect(v).toHaveLength(1)
    expect(v[0].ac).toBe('Token on the seat')
  })

  it('keeps an escaped pipe inside a cell', () => {
    const body =
      '| AC | Status | Evidence |\n|---|---|---|\n| (runtime) X | met | `env \\| grep -c KEY` = 1 |'
    expect(findUnprovenRuntimeAcs(body)[0].evidence).toContain('grep -c KEY')
  })

  it('ignores tables with no Status column, fenced examples, and HTML comments', () => {
    const body = [
      '| Connector | Tools (runtime names) |',
      '|---|---|',
      '| mail | send (runtime) |',
      '',
      '```',
      '| AC | Status | Evidence |',
      '|---|---|---|',
      '| (runtime) X | not met | example |',
      '```',
      '<!--',
      '| AC | Status | Evidence |',
      '|---|---|---|',
      '| (runtime) Y | not met | commented out |',
      '-->',
    ].join('\n')
    expect(findUnprovenRuntimeAcs(body)).toEqual([])
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
    [
      'a runtime row deferred with an owner and a date',
      pr('| (runtime) Seat adopts it | deferred | owner: release lead; enforce_by: 2026-11-20 |'),
    ],
    ['a runtime row n/a', pr('| (runtime) Monitoring field | n/a | no monitoring surface |')],
  ])('%s: green without a key or a ledger call', async (_name, body) => {
    const { fetchImpl, calls } = fakeLedger({})
    const result = await checkRuntimeAcProof(body, { fetchImpl, now: NOW })
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

describe('runtime AC proof: a runtime AC that is not met cannot pass silently (PREV-6)', () => {
  // #3852's Acceptance table, verbatim. It merged with both runtime rows "not
  // met" and the check green: the gate read only "## Acceptance criteria
  // status", and it skipped every row whose status was not "met".
  const PR_3852 = [
    '## Acceptance',
    '',
    '| AC | Status | Evidence |',
    '|---|---|---|',
    '| (repo) The applier installs, verifies, records and prunes the ledger and its copies, fail-static | met | tests above |',
    '| (repo) The writer validates, hashes, calls `accept()`, writes copy then ledger, refuses with nothing written | met | tests above |',
    '| (repo) The no-client-text check passes a blanked form and fails each planted leak | met | tests above |',
    "| (repo) `resolve(copies_dir=...)` reads the seat copy and never the store's bytes; without it, unchanged | met | tests above |",
    '| (repo) CLAUDE.md "Gone means gone" names the layer, its prune rule and its purge command | met | CLAUDE.md diff |',
    '| (runtime) A seat with an installed accepted copy, read back by the reader | not met | needs the acceptance piece; no seat has a library today |',
    "| (runtime) The purge shown removing a firm's library from the vault and the seat | not met | needs a seat with a library |",
  ].join('\n')

  it("fails #3852's body on both runtime rows, without a ledger call", async () => {
    const { fetchImpl, calls } = fakeLedger({})
    const result = await checkRuntimeAcProof(PR_3852, { relayKey: 'k', fetchImpl, now: NOW })
    expect(result.violations).toHaveLength(2)
    for (const v of result.violations)
      expect(v.reason).toMatch(/"not met" is neither met nor deferred/)
    expect(calls).toHaveLength(0)
  })

  it.each(['not met', 'unmet', 'NOT met', 'pending', 'not yet', 'open', '', 'not met in this PR'])(
    'fails status %j',
    (status) => {
      const body = pr(`| (runtime) Seat adopts it | ${status} | later |`)
      expect(findUnprovenRuntimeAcs(body)).toHaveLength(1)
    }
  )

  it('fails a deferral with no owner', () => {
    const body = pr('| (runtime) Seat adopts it | deferred | enforce_by: 2026-11-20 |')
    expect(findUnprovenRuntimeAcs(body, { now: NOW })[0].reason).toMatch(/without an `owner:`/)
  })

  it('fails a deferral with no enforce_by date', () => {
    const body = pr(
      '| (runtime) Seat adopts it | deferred | owner: release lead; next reprovision |'
    )
    expect(findUnprovenRuntimeAcs(body, { now: NOW })[0].reason).toMatch(
      /without an `enforce_by: YYYY-MM-DD`/
    )
  })

  it('fails a deferral with neither, which is how "tracked in #NNN" used to pass', () => {
    const body = pr('| (runtime) Seat adopts it | deferred | tracked in #2044 |')
    expect(findUnprovenRuntimeAcs(body, { now: NOW })[0].reason).toMatch(/owner.*or.*enforce_by/)
  })

  it.each(['TBD', '?', 'none'])('fails a placeholder owner %j', (owner) => {
    const body = pr(
      `| (runtime) Seat adopts it | deferred | owner: ${owner}; enforce_by: 2026-11-20 |`
    )
    expect(findUnprovenRuntimeAcs(body, { now: NOW })).toHaveLength(1)
  })

  it('fails an enforce_by that is not a calendar date, or has already passed', () => {
    const bad = pr('| (runtime) Seat adopts it | deferred | owner: lead; enforce_by: 2026-02-30 |')
    expect(findUnprovenRuntimeAcs(bad, { now: NOW })[0].reason).toMatch(/not a calendar date/)
    const past = pr('| (runtime) Seat adopts it | deferred | owner: lead; enforce_by: 2026-11-08 |')
    expect(findUnprovenRuntimeAcs(past, { now: NOW })[0].reason).toMatch(/has passed/)
  })

  it('accepts the owner and date in the status cell, and enforce_by on the day itself', () => {
    const body = pr(
      '| (runtime) Seat adopts it | deferred (owner: release lead, enforce_by: 2026-11-09) | needs a release |'
    )
    expect(findUnprovenRuntimeAcs(body, { now: NOW })).toEqual([])
  })
})

describe('runtime AC proof: the 20 most recent merged PRs with a runtime row', () => {
  // tests/fixtures/runtime-ac-proof/recent-merged-tables.json holds, for the
  // 20 most recent merged PRs with a (runtime) row as of 2026-11-09 (#3790 to
  // #3903), each PR's status-table header and its runtime rows: the Status
  // cell and any vfy_ IDs verbatim, the other text neutralised. 19 of the 20
  // left a runtime row "not met", "unmet", "pending" or similar with no owner
  // or date, which this gate now fails. #3803 alone is met with an ID.
  const SAMPLE: { pr: number; table: string }[] = JSON.parse(
    readFileSync(resolve(__dirname, 'fixtures/runtime-ac-proof/recent-merged-tables.json'), 'utf8')
  )
  const verdicts = () =>
    SAMPLE.map(({ pr: n, table }) => ({ n, v: findUnprovenRuntimeAcs(table, { now: NOW }) }))

  it('newly fails 19 of 20, every one for a status that is neither met nor deferred', () => {
    expect(SAMPLE).toHaveLength(20)
    const failing = verdicts().filter(({ v }) => v.length > 0)
    expect(failing.map(({ n }) => n)).not.toContain(3803)
    expect(failing).toHaveLength(19)
    for (const { n, v } of failing) {
      for (const row of v) expect(row.reason, `#${n}`).toMatch(/is neither met nor deferred/)
    }
  })

  it('passes all 20 once their unmet rows are written as deferrals', async () => {
    // Proves the parser reads every one of the 20 table shapes (AC | Status |
    // Evidence, # | Criterion | ..., a Layer or Kind column, Evidence before
    // Status): the rule is the only thing failing them, and the accepted form
    // clears it.
    const ids = new Set<string>()
    const rewritten = SAMPLE.map(({ pr: n, table }) => {
      const lines = table.split('\n')
      const statusAt = lines[0].split('|').findIndex((h) => /^(status|state)\b/i.test(h.trim()))
      const out = lines.map((line, i) => {
        if (i < 2) return line
        const cols = line.split('|')
        if (/^met\b/i.test(cols[statusAt].trim())) {
          for (const id of line.match(/vfy_[0-9A-Z]{26}/g) ?? []) ids.add(id)
          return line
        }
        cols[statusAt] = ' deferred (owner: release lead; enforce_by: 2026-11-20) '
        return cols.join('|')
      })
      return { n, body: out.join('\n') }
    })
    const { fetchImpl } = fakeLedger(Object.fromEntries([...ids].map((id) => [id, 'live_state'])))
    for (const { n, body } of rewritten) {
      const result = await checkRuntimeAcProof(body, { relayKey: 'k', fetchImpl, now: NOW })
      expect(result.violations, `#${n}`).toEqual([])
      expect(result.ledgerError, `#${n}`).toBeNull()
    }
    expect(ids.size).toBeGreaterThan(0)
  })
})

/**
 * The touch rule (wave 2 PR 1): the diff side of the gate. A client-surface PR
 * with no runtime row is reported only; a bare n/a on one blocks.
 */
describe('runtime AC proof: the touch rule reads the changed paths', () => {
  const LANE = ['operator/runners/medchron/medchron/records/run.py']
  const DOCS = ['docs/handbook/x.md', 'tests/foo.test.ts']

  it('reports, and does not block, a client-surface PR with no runtime row', () => {
    const r = checkTouchRule(pr('| repo thing (repo) | met | src/x.ts:1 |'), LANE)
    expect(r.clientSurface).toBe(true)
    expect(r.cls).toBe('client-lane')
    expect(r.hasRuntimeRow).toBe(false)
    expect(r.warnings).toHaveLength(1)
    expect(r.warnings[0]).toMatch(/client-lane: operator\/runners/)
    expect(r.blocking).toEqual([])
  })

  it('blocks a bare n/a runtime row on a client-surface PR', () => {
    for (const status of ['n/a', 'N/A', '**n/a**', 'not applicable']) {
      const r = checkTouchRule(pr(`| (runtime) Seat sends it | ${status} | |`), LANE)
      expect(r.blocking, status).toHaveLength(1)
      expect(r.blocking[0]).toMatch(/n\/a with no reason/)
    }
  })

  it('a bare n/a with only a placeholder in Evidence is still bare', () => {
    const r = checkTouchRule(pr('| (runtime) Seat sends it | n/a | - |'), LANE)
    expect(r.blocking).toHaveLength(1)
  })

  it('passes an n/a that says why, in the status or the Evidence cell', () => {
    for (const row of [
      '| (runtime) Seat sends it | n/a: no seat runs this lane yet | |',
      '| (runtime) Seat sends it | n/a | the lane is not enabled on any seat |',
    ]) {
      const r = checkTouchRule(pr(row), LANE)
      expect(r.blocking, row).toEqual([])
      expect(r.warnings, row).toEqual([])
    }
  })

  it('leaves a bare n/a alone on a PR that touches no client surface', () => {
    const r = checkTouchRule(pr('| (runtime) Seat sends it | n/a | |'), DOCS)
    expect(r.clientSurface).toBe(false)
    expect(r.blocking).toEqual([])
    expect(r.warnings).toEqual([])
  })

  it('says nothing about a non-client PR with no runtime row', () => {
    const r = checkTouchRule('Just a docs change.', DOCS)
    expect(r).toMatchObject({ cls: 'none', warnings: [], blocking: [] })
  })

  it('naReason ignores the AC text when the table has no Evidence column', () => {
    expect(
      naReason({ status: 'n/a', evidence: 'long AC text here', evidenceColumn: false })
    ).toBeNull()
    expect(naReason({ status: 'n/a', evidence: '', evidenceColumn: true })).toBeNull()
    expect(
      naReason({ status: 'n/a: repo-only', evidence: '', evidenceColumn: true })
    ).not.toBeNull()
  })

  it('the workflow lists the PR files, warns on the report half, and fails only on blocking', () => {
    const workflow = readFileSync(
      resolve(__dirname, '../.github/workflows/runtime-ac-proof.yml'),
      'utf8'
    )
    expect(workflow).toContain('github.paginate(github.rest.pulls.listFiles')
    expect(workflow).toContain('checkTouchRule(body, paths)')
    expect(workflow).toMatch(/for \(const w of touch\.warnings\) core\.warning\(w\)/)
    expect(workflow).toContain('failures.push(...touch.blocking)')
    expect(workflow).not.toMatch(/failures\.push\(\.\.\.touch\.warnings\)/)
  })
})
