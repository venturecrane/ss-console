/**
 * An AgentMail seat refuses to provision without the ORG-scoped webhook_read key.
 *
 * WHY THIS KEY EXISTS AT ALL. Boot smoke proves the seat's staged webhook secret
 * against the vendor. That lookup was first written to use the agent's own
 * AgentMail key, which is inbox-scoped by design (ss#2258 — the vendor itself
 * refuses to let the agent transmit). AgentMail's webhooks are ORG-level objects
 * carrying an inbox_ids filter, and permissions are intersected with scope, so
 * measured live on 2026-09-17 (vfy_01M2RD3EH4GDBH5SN2K16B0RRH): the seat key
 * returns 403 missing_permission, and a key re-minted on the same inbox WITH
 * webhook_read returns 200 and ZERO webhooks. The check could not pass on any
 * correctly-scoped seat — it reported a credential defect where none existed.
 *
 * WHY IT IS A REFUSAL AND NOT A FALLBACK. A fallback to AGENTMAIL_API_KEY would
 * restore exactly that state: a check that cannot pass, surfacing as a red seat.
 * Same reasoning as the sibling secret fence next door.
 *
 * WHAT THIS TEST DRIVES. The real fence text, extracted from
 * `operator/bin/lib/stage-agentmail.sh` between its `agentmail-webhook-read-fence`
 * sentinels, run in a bash harness with a stub `die`. The falsifier is the first
 * case: with the key present the fence passes, so a fence that died on
 * everything would fail here too.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/lib/stage-agentmail.sh', import.meta.url))

function fenceSource(): string {
  const text = readFileSync(SCRIPT, 'utf8')
  const start = text.indexOf('# >>> agentmail-webhook-read-fence')
  const end = text.indexOf('# <<< agentmail-webhook-read-fence')
  expect(start, 'opening fence sentinel missing from lib/stage-agentmail.sh').toBeGreaterThan(-1)
  expect(end, 'closing fence sentinel missing from lib/stage-agentmail.sh').toBeGreaterThan(start)
  const block = text.slice(start, end)
  expect(block).toContain('die ')
  // The whole point: the inbox-scoped key must not be consulted inside the fence.
  expect(block).not.toMatch(/\$\{AGENTMAIL_API_KEY[:}]/)
  return block
}

interface Outcome {
  status: number
  output: string
}

function runFence(env: Record<string, string>): Outcome {
  const harness = [
    'set -euo pipefail',
    'die() { echo "FATAL: $*"; exit 1; }',
    'CUSTOMER_ID="${CUSTOMER_ID}"',
    fenceSource(),
    'printf "FENCE-PASSED len=%s\\n" "${#AGENTMAIL_WEBHOOK_READ_API_KEY}"',
  ].join('\n')
  try {
    const output = execFileSync('bash', ['-c', harness], {
      encoding: 'utf8',
      env: { PATH: process.env['PATH'] ?? '', ...env },
    })
    return { status: 0, output }
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string }
    return { status: e.status ?? 1, output: `${e.stdout ?? ''}${e.stderr ?? ''}` }
  }
}

describe('agentmail webhook-read fence', () => {
  it('passes when the org-scoped key is vaulted (the falsifier)', () => {
    const { status, output } = runFence({
      CUSTOMER_ID: 'pilot-smokeball',
      AGENTMAIL_WEBHOOK_READ_API_KEY: 'am_us_orgwebhookreadkey',
    })
    expect(status).toBe(0)
    expect(output).toContain('FENCE-PASSED len=23')
  })

  it('dies when the key is absent, naming the vault key and why scope matters', () => {
    const { status, output } = runFence({ CUSTOMER_ID: 'pilot-smokeball' })
    expect(status).toBe(1)
    expect(output).toContain('agentmail-webhook-read-fence')
    expect(output).toContain('AGENTMAIL_WEBHOOK_READ_API_KEY')
    expect(output).toContain('webhook_read')
    expect(output).toContain('inbox-scoped key lists zero webhooks')
  })

  it('dies when the key is present but empty', () => {
    const { status } = runFence({
      CUSTOMER_ID: 'pilot-smokeball',
      AGENTMAIL_WEBHOOK_READ_API_KEY: '',
    })
    expect(status).toBe(1)
  })
})
