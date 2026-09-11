/**
 * The manifest-driven staging loop must never stage a per-customer credential.
 *
 * provision-customer.sh has two staging authorities for connector env:
 *
 *   1. dedicated per-connector blocks, which source PER-CUSTOMER values
 *      (msgraph: parsed from customer.yaml msgraph_auth + the per-seat vault
 *      keys; smokeball: an env-name remap), and
 *   2. a generic manifest-driven loop that stages every `required_secrets`
 *      runtime name FROM THE OPERATOR ENV BY PLAIN NAME.
 *
 * The loop runs second. For any name that also exists as an account-level
 * global in /ss, last-write-wins means the global CLOBBERS the per-customer
 * value. That was live on 2026-08-18: the first client seat (ashton-price)
 * deployed with its own MSGRAPH_TENANT_ID + MSGRAPH_MAILBOX but smd-staging's
 * MSGRAPH_CLIENT_ID + MSGRAPH_CLIENT_SECRET — the two names that had globals —
 * and the delta poller 401ed (AADSTS7000229) on every cycle. The corruption was
 * invisible on smd-staging itself, whose per-customer values ARE the globals.
 *
 * This test extracts the loop's embedded python verbatim from the script (the
 * same extract-don't-restate discipline as msgraph-two-app-fence.test.ts — a
 * restated copy would keep passing after the script regressed) and runs it
 * against the real operator/connectors tree: msgraph-mail must not emit a
 * single staging line, and the fixture connector proves the loop still stages
 * what it is FOR (an SMD-owned static credential), so the skip cannot be
 * "fixed" by breaking the loop entirely.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const SCRIPT = fileURLToPath(new URL('../operator/bin/provision-customer.sh', import.meta.url))
const CONNECTORS_DIR = fileURLToPath(new URL('../operator/connectors', import.meta.url))

/** The loop's python, verbatim from the heredoc between `python3 - "${_CONNECTORS_DIR}" <<'PY'` and `PY`. */
function loopPython(): string {
  const text = readFileSync(SCRIPT, 'utf8')
  const open = text.indexOf(`python3 - "\${_CONNECTORS_DIR}" <<'PY'`)
  expect(open, 'manifest-loop heredoc opener missing from provision-customer.sh').toBeGreaterThan(
    -1
  )
  const bodyStart = text.indexOf('\n', open) + 1
  const close = text.indexOf('\nPY\n', bodyStart)
  expect(close, 'manifest-loop heredoc closer missing').toBeGreaterThan(bodyStart)
  return text.slice(bodyStart, close)
}

function runLoop(connectorsDir: string): string[] {
  const out = execFileSync('python3', ['-', connectorsDir], {
    input: loopPython(),
    encoding: 'utf8',
  })
  return out.split('\n').filter(Boolean)
}

const scratch = mkdtempSync(join(tmpdir(), 'manifest-loop-'))
afterAll(() => rmSync(scratch, { recursive: true, force: true }))

describe('manifest-driven staging loop: per-customer connectors are skipped', () => {
  it('emits no staging line for msgraph-mail against the real connectors tree', () => {
    const lines = runLoop(CONNECTORS_DIR)
    const msgraph = lines.filter((l) => l.startsWith('msgraph-mail\t'))
    expect(
      msgraph,
      'the loop would stage these BY PLAIN NAME from the operator env, where the ' +
        'account-level MSGRAPH_CLIENT_ID/SECRET globals clobber the per-customer values'
    ).toEqual([])
  })

  it('smokeball stays skipped too (the original remap case)', () => {
    const lines = runLoop(CONNECTORS_DIR)
    expect(lines.filter((l) => l.startsWith('smokeball\t'))).toEqual([])
  })

  it('still stages an SMD-owned static connector — the skip must not kill the loop', () => {
    // A fixture connector shaped like the ones the loop exists for.
    const dir = join(scratch, 'fixture-static')
    mkdirSync(dir, { recursive: true })
    writeFileSync(
      join(dir, 'manifest.toml'),
      [
        '[connector]',
        'auth_model = "static"',
        '[[connector.required_secrets]]',
        'runtime_env = "FIXTURE_STATIC_API_KEY"',
      ].join('\n')
    )
    expect(runLoop(scratch)).toContain('fixture-static\tFIXTURE_STATIC_API_KEY')
  })
})
