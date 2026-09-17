/**
 * The AgentMail channel refuses to provision without the seat's OWN webhook secret.
 *
 * WHY THIS IS A REFUSAL AND NOT A FALLBACK. Each seat's inbox is wired to its
 * own vendor webhook with its own Svix signing secret. The provisioner used to
 * fall back to the global WEBHOOK_SECRET_AGENTMAIL when the per-seat key was
 * not vaulted. On scott that staged a secret the seat's webhook never had, and
 * every inbound email was rejected 401 "invalid signature" for weeks while boot
 * smoke passed 39/39 and the fleet row read green (2026-09-15,
 * vfy_01M2HXT17Q32RX6TCV5NVZA9D6). A fallback whose only possible product is a
 * silently dead inbound path is a defect, so it is gone: the fence dies and
 * names the key to vault.
 *
 * WHAT THIS TEST DRIVES. The real fence text, extracted from
 * `operator/bin/lib/stage-agentmail.sh` between its `agentmail-webhook-secret-fence`
 * sentinels, run in a bash harness with a stub `die`. Same shape as
 * tests/msgraph-two-app-fence.test.ts. The falsifier is the first case: with
 * the per-seat key vaulted the fence passes and stages that value, so a fence
 * that died on everything would fail here too.
 */
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/lib/stage-agentmail.sh', import.meta.url))
// The block moved to the lib on 2026-09-17 (shell size ratchet); `unset_stale`
// and the rest of the provisioner's helpers still live in the caller.
const PROVISIONER = fileURLToPath(new URL('../operator/bin/provision-customer.sh', import.meta.url))

function fenceSource(): string {
  const text = readFileSync(SCRIPT, 'utf8')
  const start = text.indexOf('# >>> agentmail-webhook-secret-fence')
  const end = text.indexOf('# <<< agentmail-webhook-secret-fence')
  expect(start, 'opening fence sentinel missing from lib/stage-agentmail.sh').toBeGreaterThan(-1)
  expect(end, 'closing fence sentinel missing from lib/stage-agentmail.sh').toBeGreaterThan(start)
  const block = text.slice(start, end)
  expect(block).toContain('die ')
  // The whole point: the global name must not be consulted inside the fence.
  expect(block).not.toMatch(/\$\{WEBHOOK_SECRET_AGENTMAIL[:}]/)
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
    'printf "FENCE-PASSED key=%s len=%s\\n" "${_AGENTMAIL_WH_KEY}" "${#_AGENTMAIL_WH_SECRET}"',
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

describe('agentmail-webhook-secret-fence', () => {
  it('passes with the per-seat key vaulted, and stages that value (the falsifier)', () => {
    const r = runFence({
      CUSTOMER_ID: 'scott',
      WEBHOOK_SECRET_AGENTMAIL__SCOTT: 'whsec_seatvalue_0123456789abcdef0123456789',
      WEBHOOK_SECRET_AGENTMAIL: 'whsec_globalvalue_shouldneverbeused',
    })
    expect(r.status, r.output).toBe(0)
    expect(r.output).toContain('FENCE-PASSED key=WEBHOOK_SECRET_AGENTMAIL__SCOTT len=42')
  })

  it('refuses when only the global secret exists (the scott shape)', () => {
    const r = runFence({
      CUSTOMER_ID: 'scott',
      WEBHOOK_SECRET_AGENTMAIL: 'whsec_globalvalue_shouldneverbeused',
    })
    expect(r.status).toBe(1)
    expect(r.output).toContain('FATAL: agentmail-webhook-secret-fence')
    expect(r.output).toContain('WEBHOOK_SECRET_AGENTMAIL__SCOTT is not vaulted')
    expect(r.output).not.toContain('FENCE-PASSED')
  })

  it('refuses when nothing is vaulted at all', () => {
    const r = runFence({ CUSTOMER_ID: 'scott' })
    expect(r.status).toBe(1)
    expect(r.output).toContain('WEBHOOK_SECRET_AGENTMAIL__SCOTT is not vaulted')
  })

  it('a seat that binds no agentmail adapter unsets every AgentMail secret the Machine may still carry', () => {
    // 2026-09-16: the first client seat (msgraph, no agentmail) failed boot
    // smoke because a global WEBHOOK_SECRET_AGENTMAIL staged by a provision
    // before the fence persisted on the Machine. The else branch of the
    // agentmail block converges the Machine on the authored state. The
    // falsifier: drop the unset line and this reads the fence's own name
    // only inside the `if`.
    const text = readFileSync(SCRIPT, 'utf8')
    const start = text.indexOf(
      "if authored_channel '^adapter=agentmail$|^backend=mcp:agentmail$'; then"
    )
    expect(start, 'agentmail block missing').toBeGreaterThan(-1)
    const elseAt = text.indexOf('\nelse\n', start)
    const fiAt = text.indexOf('\nfi\n', start)
    expect(elseAt, 'the agentmail block has no else branch').toBeGreaterThan(-1)
    expect(elseAt, 'the else branch is outside the agentmail block').toBeLessThan(fiAt)
    const branch = text.slice(elseAt, fiAt)
    expect(branch).toMatch(
      /unset_stale "[^"]+" WEBHOOK_SECRET_AGENTMAIL AGENTMAIL_API_KEY AGENTMAIL_SEND_API_KEY/
    )
    // The helper itself is the removal: a stale value is unset --stage so the
    // deploy that follows carries the authored state. It is DEFINED in the
    // provisioner and called from the sourced block, so this assertion reads the
    // provisioner even though the block above now lives in lib/stage-agentmail.sh.
    expect(readFileSync(PROVISIONER, 'utf8')).toMatch(
      /unset_stale\(\) \{[^\n]*fly secrets unset --stage -a "\$\{APP_NAME\}" "\$@"/
    )
    expect(
      branch,
      'the router signing secret belongs to the msgraph block on an msgraph seat'
    ).not.toContain('SMD_WEBHOOK_SIGNING_SECRET AGENTMAIL')
  })

  it('derives the key name from a hyphenated slug the same way the read/send keys do', () => {
    const r = runFence({
      CUSTOMER_ID: 'pilot-smokeball',
      WEBHOOK_SECRET_AGENTMAIL__PILOT_SMOKEBALL: 'whsec_pilot',
    })
    expect(r.status, r.output).toBe(0)
    expect(r.output).toContain('key=WEBHOOK_SECRET_AGENTMAIL__PILOT_SMOKEBALL')
  })
})
