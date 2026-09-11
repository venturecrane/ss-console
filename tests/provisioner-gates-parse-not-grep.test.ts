/**
 * Channel gates in provision-customer.sh parse the yaml; comments are not authoring.
 *
 * Five gates used to decide "does this seat bind channel X?" by `grep -qE` over
 * the RAW customer.yaml — which reads comments as config. Live consequence
 * (2026-08-18): ashton-price authors no agentmail connector, but its history
 * comment contains the literal `adapter: agentmail`, so every reprovision
 * re-staged the org-wide AgentMail key — the ss#2258-shaped credential —
 * onto a seat with no AgentMail channel, and it had to be manually unset after
 * each run. Same class as the manifest-loop clobber (#2426): a config decision
 * made without parsing the config.
 *
 * The fix parses connectors once (the authored-channel-facts block) and every
 * gate consumes the derived list via `authored_channel`. Two layers of proof:
 *
 *  1. STRUCTURAL (always runs, fails on the pre-fix script): no gate greps the
 *     raw yaml for adapter/backend/webhook_url any more, the sentinel block
 *     exists, and all five gates consume authored_channel.
 *  2. BEHAVIOURAL (runs where `uv` is available — dev machines; skipped in a
 *     runner without uv): the sentinel block is extracted VERBATIM and driven
 *     against fixture yamls — the A&P comment-trap case must not trigger, and
 *     genuinely-authored fixtures for each gate must trigger. The msgraph
 *     positive fixture matters most: breaking that gate would fail silently
 *     until the next real reprovision (critic finding, 2026-08-18).
 */
import { execFileSync, execSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/provision-customer.sh', import.meta.url))
const src = readFileSync(SCRIPT, 'utf8')

describe('structural: gates no longer grep the raw yaml', () => {
  it('no channel gate greps customer.yaml for adapter/backend/webhook_url', () => {
    const rawGates = src
      .split('\n')
      .filter((l) => /grep -qE '(adapter|backend|webhook_url):/.test(l))
      .filter((l) => l.includes('customer.yaml'))
    expect(
      rawGates,
      'these lines read COMMENTS as authoring — the A&P history comment re-staged the AgentMail key this way'
    ).toEqual([])
  })

  it('the authored-channel-facts block exists and all five gates consume it', () => {
    expect(src).toContain('# >>> authored-channel-facts')
    expect(src).toContain('# <<< authored-channel-facts')
    const uses = src.match(/if authored_channel '/g) ?? []
    // agentmail, msgraph, brave, smokeball, smokeball-webhook_url
    expect(uses.length).toBeGreaterThanOrEqual(5)
  })

  it('the facts block is defined before its first consumer', () => {
    expect(src.indexOf('# <<< authored-channel-facts')).toBeLessThan(
      src.indexOf("if authored_channel '")
    )
  })
})

// ---------------------------------------------------------------------------
// Behavioural: drive the extracted block against fixtures. Needs `uv` (the
// block's own interpreter). Structural assertions above still guard when
// this half is skipped.
// ---------------------------------------------------------------------------

function hasUv(): boolean {
  try {
    execSync('uv --version', { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

function factsBlock(): string {
  const start = src.indexOf('# >>> authored-channel-facts')
  const end = src.indexOf('# <<< authored-channel-facts')
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return src.slice(start, end)
}

/** Run the verbatim facts block against a fixture yaml, then query authored_channel. */
function channelMatches(yaml: string, regex: string): boolean {
  const dir = mkdtempSync(join(tmpdir(), 'gates-'))
  scratchDirs.push(dir)
  const yamlPath = join(dir, 'customer.yaml')
  writeFileSync(yamlPath, yaml)
  const harness = [
    'set -euo pipefail',
    `CUSTOMER_YAML=${JSON.stringify(yamlPath)}`,
    factsBlock(),
    // Distinct tokens, deliberately: an earlier draft used MATCH/NOMATCH with
    // `out.includes('MATCH')` — which is true for BOTH ("NOMATCH".includes("MATCH")),
    // so every negative case passed as positive. A predicate that cannot go
    // false measures nothing.
    `if authored_channel ${JSON.stringify(regex)}; then echo GATE_OPEN; else echo GATE_CLOSED; fi`,
  ].join('\n')
  const out = execFileSync('bash', ['-c', harness], { encoding: 'utf8' })
  expect(
    out.includes('GATE_OPEN') || out.includes('GATE_CLOSED'),
    'harness produced no verdict'
  ).toBe(true)
  return out.includes('GATE_OPEN')
}

const scratchDirs: string[] = []
afterAll(() => scratchDirs.forEach((d) => rmSync(d, { recursive: true, force: true })))

const AGENTMAIL_RE = '^adapter=agentmail$|^backend=mcp:agentmail$'
const MSGRAPH_RE = '^adapter=msgraph$|^backend=mcp:msgraph-mail$'

// The A&P shape that produced the incident: the literal gate pattern lives in a
// COMMENT while the connectors author only msgraph.
const AP_SHAPED = `
connectors:
  # This block once authored \`adapter: agentmail\` while the note said the firm
  # runs M365 — kept verbatim because prose must not be authoring.
  Email:
    adapter: msgraph
    backend: mcp:msgraph-mail
    enabled: true
    msgraph_auth:
      tenant_id: 'e4ad47eb-0000-0000-0000-000000000000'
      client_id: '03c32f5c-0000-0000-0000-000000000000'
      mailbox: operator@example.com
`

describe.skipIf(!hasUv())('behavioural: the extracted block, driven', () => {
  it('the A&P comment trap does NOT trigger the agentmail gate', () => {
    expect(channelMatches(AP_SHAPED, AGENTMAIL_RE)).toBe(false)
  })

  it('the same fixture DOES trigger the msgraph gate (the silent-failure direction)', () => {
    expect(channelMatches(AP_SHAPED, MSGRAPH_RE)).toBe(true)
  })

  it('a genuinely authored agentmail connector still triggers', () => {
    const yaml = 'connectors:\n  Email:\n    adapter: agentmail\n    backend: mcp:agentmail\n'
    expect(channelMatches(yaml, AGENTMAIL_RE)).toBe(true)
  })

  it('brave and smokeball-webhook gates match authored fields only', () => {
    const yaml = [
      'connectors:',
      '  # backend: mcp:smokeball  <- comment, must not count',
      '  Search:',
      "    backend: 'native:brave-free'",
      '  PracticeManagement:',
      '    backend: mcp:smokeball',
      "    webhook_url: 'https://x.fly.dev/webhooks/smokeball'",
      '',
    ].join('\n')
    expect(channelMatches(yaml, '^backend=native:brave')).toBe(true)
    expect(channelMatches(yaml, '^backend=mcp:smokeball$')).toBe(true)
    expect(channelMatches(yaml, '^webhook_url=.*/webhooks/smokeball$')).toBe(true)
    expect(
      channelMatches('# backend: mcp:smokeball\nconnectors: {}\n', '^backend=mcp:smokeball$')
    ).toBe(false)
  })
})
