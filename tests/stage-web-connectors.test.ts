/**
 * operator/bin/lib/stage-web-connectors.sh (Brave, WordPress, GA4), DRIVEN, not read.
 *
 * The provisioner sources this file in its own shell. Here it is sourced by a
 * small bash driver that stubs the four functions it calls from the
 * provisioner (stage_secret_from_env, die, log, authored_channel), so each run
 * shows exactly what a real provisioning would stage. Three properties matter,
 * and each has a run that would fail if it broke:
 *
 *  - the WordPress password comes ONLY from the per-seat vault name; a global
 *    WORDPRESS_APP_PASSWORD (another firm's) is never used;
 *  - the GA4 key is refused outright when it is the domain-wide-delegation key,
 *    because this key sits in the agent-readable connector config;
 *  - a seat that binds neither connector stages nothing.
 *
 * Needs `uv` (the block parses the yaml with `uv run --with pyyaml`), like the
 * behavioural half of provisioner-gates-parse-not-grep.test.ts; skipped without it.
 */
import { execFileSync, execSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterAll, describe, expect, it } from 'vitest'

const LIB = fileURLToPath(new URL('../operator/bin/lib/stage-web-connectors.sh', import.meta.url))

function hasUv(): boolean {
  try {
    execSync('uv --version', { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

const scratch = mkdtempSync(join(tmpdir(), 'stage-web-connectors-'))
afterAll(() => rmSync(scratch, { recursive: true, force: true }))

const DRIVER = `
set -euo pipefail
stage_secret_from_env() { if [ -n "$2" ]; then echo "STAGE $1=$2"; else echo "SKIP $1"; fi; }
die() { echo "DIE $*"; exit 3; }
log() { :; }
authored_channel() { printf '%s\\n' "$AUTHORED" | grep -qE "$1"; }
source "$LIB"
echo END
`

function run(
  yaml: string,
  authored: string,
  env: Record<string, string>
): { out: string; code: number } {
  const file = join(scratch, `c-${Math.random().toString(36).slice(2)}.yaml`)
  writeFileSync(file, yaml)
  try {
    const out = execFileSync('bash', ['-c', DRIVER], {
      encoding: 'utf8',
      env: {
        PATH: process.env.PATH ?? '',
        HOME: process.env.HOME ?? '',
        LIB,
        CUSTOMER_YAML: file,
        CUSTOMER_ID: 'example-firm',
        AUTHORED: authored,
        ...env,
      },
    })
    return { out, code: 0 }
  } catch (err) {
    const e = err as { stdout?: string; status?: number }
    return { out: e.stdout ?? '', code: e.status ?? -1 }
  }
}

const WP_YAML = `connectors:
  Website:
    adapter: wordpress
    backend: mcp:wordpress
    enabled: true
    site_url: https://www.example-firm.test
    username: smd-operator
    secret_ref: fly-secret:WORDPRESS_APP_PASSWORD
`
const GA4_YAML = `connectors:
  Analytics:
    adapter: ga4
    backend: mcp:ga4
    enabled: true
    property_id: '123456789'
`

describe.skipIf(!hasUv())('stage-web-connectors.sh, driven', () => {
  it('stages the WordPress site, user and the PER-SEAT password, never a global one', () => {
    const { out, code } = run(WP_YAML, 'adapter=wordpress\nbackend=mcp:wordpress', {
      WORDPRESS_APP_PASSWORD__EXAMPLE_FIRM: 'seat-pass',
      WORDPRESS_APP_PASSWORD: 'another-firms-pass',
    })
    expect(code).toBe(0)
    expect(out).toContain('STAGE WORDPRESS_SITE_URL=https://www.example-firm.test')
    expect(out).toContain('STAGE WORDPRESS_USERNAME=smd-operator')
    expect(out).toContain('STAGE WORDPRESS_APP_PASSWORD=seat-pass')
    expect(out).not.toContain('another-firms-pass')
  })

  it('skips the password (server stays unwired) when only a global exists', () => {
    const { out } = run(WP_YAML, 'backend=mcp:wordpress', {
      WORDPRESS_APP_PASSWORD: 'another-firms-pass',
    })
    expect(out).toContain('SKIP WORDPRESS_APP_PASSWORD')
    expect(out).not.toContain('another-firms-pass')
  })

  it('stages the GA4 property and the analytics-only key', () => {
    const { out, code } = run(GA4_YAML, 'backend=mcp:ga4', {
      GA4_SERVICE_ACCOUNT_JSON: 'analytics-key',
      GOOGLE_SERVICE_ACCOUNT_JSON: 'dwd-key',
    })
    expect(code).toBe(0)
    expect(out).toContain('STAGE GA4_PROPERTY_ID=123456789')
    expect(out).toContain('STAGE GA4_SERVICE_ACCOUNT_JSON=analytics-key')
  })

  it('refuses the domain-wide-delegation key as the GA4 key', () => {
    const { out, code } = run(GA4_YAML, 'backend=mcp:ga4', {
      GA4_SERVICE_ACCOUNT_JSON: 'dwd-key',
      GOOGLE_SERVICE_ACCOUNT_JSON: 'dwd-key',
    })
    expect(code).toBe(3)
    expect(out).toContain('DIE')
    expect(out).not.toContain('STAGE GA4_SERVICE_ACCOUNT_JSON')
  })

  it('stages the Brave key only for a native:brave seat (moved here unchanged)', () => {
    const { out } = run('connectors: {}\n', 'backend=native:brave-free', {
      BRAVE_SEARCH_API_KEY: 'brave',
    })
    expect(out).toContain('STAGE BRAVE_SEARCH_API_KEY=brave')
  })

  it('a seat that binds none of these connectors stages nothing', () => {
    const { out, code } = run('connectors: {}\n', 'adapter=msgraph\nbackend=mcp:msgraph-mail', {
      WORDPRESS_APP_PASSWORD__EXAMPLE_FIRM: 'seat-pass',
      GA4_SERVICE_ACCOUNT_JSON: 'analytics-key',
    })
    expect(code).toBe(0)
    expect(out.trim()).toBe('END')
  })
})
