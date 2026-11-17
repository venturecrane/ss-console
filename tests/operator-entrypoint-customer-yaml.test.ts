/**
 * The seat's customer.yaml boot fetch survives a short R2 outage
 * (operator/templates/entrypoint-fetch-inputs.sh, 2026-11-12).
 *
 * The live copy sits on the image's root filesystem, which a Machine resets on
 * every start, so one failed fetch after a restart was `FATAL: customer.yaml
 * not present and R2 fetch failed`, Fly's three quick restarts, and a client
 * seat parked at "stopped" during a regional outage. These tests run the real
 * fragment against a fake vault (an `aws` shim that fails a set number of
 * times), a stub validator and a stub volume-copy restore.
 *
 * FALSIFIERS: drop the retry loop and "a blip is waited out" fails; drop the
 * volume-copy branch and "an outage boots on the signed copy" fails; restore
 * the copy without checking its exit code and "a copy that does not verify"
 * fails; fall back on a 404 and "a 404 is not an outage" fails.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } from 'fs'
import { join, resolve } from 'path'
import { tmpdir } from 'os'
import { spawnSync } from 'child_process'

const FRAGMENT = resolve('operator/templates/entrypoint-fetch-inputs.sh')
const roots: string[] = []
afterEach(() => {
  for (const r of roots.splice(0)) rmSync(r, { recursive: true, force: true })
})

interface World {
  root: string
  shims: string
  live: string
}

/**
 * fails: how many fetches fail before one succeeds (a large number = never).
 * failWith: the error text a failed fetch prints. copy: what the volume copy
 * restore does ("good" verifies, "bad" refuses, "none" is absent).
 */
function world(fails: number, failWith = 'Could not connect to the endpoint URL', copy = 'good') {
  const root = mkdtempSync(join(tmpdir(), 'boot-cy-'))
  roots.push(root)
  const shims = join(root, 'shims')
  mkdirSync(shims)
  mkdirSync(join(root, 'config'))
  const shim = (name: string, body: string) =>
    writeFileSync(join(shims, name), `#!/bin/sh\n${body}\n`, { mode: 0o755 })
  writeFileSync(join(root, 'fails'), String(fails))
  writeFileSync(join(root, 'calls'), '0')
  shim(
    'aws',
    [
      'n=$(cat "$FAKE_DIR/calls"); n=$((n + 1)); echo "$n" > "$FAKE_DIR/calls"',
      `if [ "$n" -le "$(cat "$FAKE_DIR/fails")" ]; then echo "fatal error: ${failWith}" >&2; exit 1; fi`,
      'for a in "$@"; do d="$a"; done; printf "from: r2\\n" > "$d"; exit 0',
    ].join('\n')
  )
  shim('sleep', 'echo "$1" >> "$FAKE_DIR/slept"; exit 0')
  shim('validator', 'grep -q "refuse" "$2" && exit 1; exit 0')
  shim(
    'restore',
    {
      good:
        'printf "from: volume\\n" > "$5"; echo "' +
        'a'.repeat(64) +
        ' ' +
        'b'.repeat(40) +
        '"; exit 0',
      bad: 'echo "volume copy not usable: the copy\'s spec does not verify (bad_signature)" >&2; exit 1',
      none: 'echo "volume copy not usable: no copy on the volume" >&2; exit 1',
    }[copy] as string
  )
  return { root, shims, live: join(root, 'config/customer.yaml') }
}

function boot(w: World, env: Record<string, string> = {}) {
  const script = [
    'set -euo pipefail',
    'log() { echo "LOG $*"; }',
    `CONFIG_DIR="${w.root}/config" LIVE_CUSTOMER_YAML="${w.live}"`,
    'R2_BUCKET_CONFIG=b CUSTOMER_SLUG=seat-alpha R2_ACCESS_KEY_ID=k R2_SECRET_ACCESS_KEY=s _seed_endpoint=http://e',
    `. "${FRAGMENT}"`,
    'smd_boot_customer_yaml "${LIVE_CUSTOMER_YAML}"',
    'echo "BOOTED"',
  ].join('\n')
  // nosemgrep: javascript.lang.security.audit.dangerous-spawn-shell.dangerous-spawn-shell - a test running the repo's own shell fragment on a temp dir it made; no outside input
  const r = spawnSync('bash', ['-c', script], {
    encoding: 'utf8',
    timeout: 20000,
    env: {
      PATH: `${w.shims}:/usr/bin:/bin`,
      FAKE_DIR: w.root,
      SMD_CONFIG_VALIDATOR_PYTHON: join(w.shims, 'validator'),
      // The shim sees: -m config_applier.volume_copy restore <slug> <dest>
      SMD_CONFIG_COPY_PYTHON: join(w.shims, 'restore'),
      ...env,
    },
  })
  const slept = existsSync(join(w.root, 'slept'))
    ? readFileSync(join(w.root, 'slept'), 'utf8').trim().split('\n').map(Number)
    : []
  return {
    ok: r.status === 0 && r.stdout.includes('BOOTED'),
    out: r.stdout + r.stderr,
    slept,
    live: existsSync(w.live) ? readFileSync(w.live, 'utf8') : null,
  }
}

const total = (xs: number[]) => xs.reduce((a, b) => a + b, 0)

describe('entrypoint customer.yaml fetch: an R2 outage does not park the seat', () => {
  it('a blip is waited out, backing off, and R2 still wins over the volume copy', () => {
    const r = boot(world(3))
    expect(r.ok, r.out).toBe(true)
    expect(r.live).toBe('from: r2\n')
    expect(r.slept).toEqual([5, 10, 20])
    expect(r.out).toContain('customer.yaml refreshed from R2')
    expect(r.out).not.toContain('VOLUME COPY')
  })

  it('with no copy it waits out longer than the restart budget before FATAL', () => {
    const r = boot(world(1000, undefined, 'none'))
    expect(r.ok).toBe(false)
    expect(total(r.slept)).toBeGreaterThanOrEqual(300)
    expect(Math.max(...r.slept)).toBe(30)
    expect(r.out).toMatch(/FATAL: customer\.yaml not present and R2 fetch failed for \d+s/)
    expect(r.live).toBeNull()
  })

  it('an outage boots on the signed volume copy after a shorter wait, loudly', () => {
    const r = boot(world(1000))
    expect(r.ok, r.out).toBe(true)
    expect(r.live).toBe('from: volume\n')
    expect(total(r.slept)).toBeGreaterThanOrEqual(60)
    expect(total(r.slept)).toBeLessThan(300)
    expect(r.out).toMatch(
      /WARN: BOOTING FROM THE VOLUME COPY of customer\.yaml: R2 did not answer for \d+s .*Signed spec a{64}, compiled from b{40}/
    )
  })

  it('a copy that does not verify is never booted on', () => {
    const r = boot(world(1000, undefined, 'bad'))
    expect(r.ok).toBe(false)
    expect(r.live).toBeNull()
    expect(r.out).toContain('FATAL: customer.yaml not present and R2 fetch failed')
  })

  it('a copy the image validator refuses is never booted on', () => {
    const w = world(1000)
    writeFileSync(join(w.shims, 'restore'), '#!/bin/sh\nprintf "refuse\\n" > "$5"; echo "x y"\n', {
      mode: 0o755,
    })
    const r = boot(w)
    expect(r.ok).toBe(false)
    expect(r.live).toBeNull()
  })

  it('a 404 is not an outage: no retry, no copy, the seat stops (gone means gone)', () => {
    const r = boot(world(1000, 'An error occurred (404) when calling the HeadObject operation'))
    expect(r.ok).toBe(false)
    expect(r.slept).toEqual([])
    expect(r.live).toBeNull()
    expect(r.out).toContain('FATAL: customer.yaml is not in R2')
  })

  it('an R2 object the validator refuses falls back to the signed copy at once', () => {
    const w = world(0)
    writeFileSync(
      join(w.shims, 'aws'),
      '#!/bin/sh\nfor a in "$@"; do d="$a"; done; printf "refuse\\n" > "$d"\n',
      {
        mode: 0o755,
      }
    )
    const r = boot(w)
    expect(r.ok, r.out).toBe(true)
    expect(r.slept).toEqual([])
    expect(r.live).toBe('from: volume\n')
    expect(r.out).toContain('R2 customer.yaml REFUSED by the on-box validator')
    expect(r.out).toContain(
      'BOOTING FROM THE VOLUME COPY of customer.yaml: the R2 object was refused'
    )
  })
})
