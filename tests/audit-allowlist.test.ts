/**
 * The npm-audit gate must fail when the audit itself failed (code review
 * 2026-10-06, N5).
 *
 * `npm audit --json` reports a failed audit (registry unreachable, lockfile
 * unreadable) as `{"error": {...}}` on stdout with exit 1. The gate parsed that,
 * found no `vulnerabilities` key, counted zero advisories and passed: "could not
 * look" read as "nothing found". These tests run the real script against a fake
 * `npm` on PATH so the gate's verdict is observed, not inferred.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const SCRIPT = resolve('scripts/audit-allowlist.mjs')
let dir: string

function fakeNpm(stdout: unknown, exitCode: number): void {
  const body = `#!/usr/bin/env node\nprocess.stdout.write(${JSON.stringify(JSON.stringify(stdout))})\nprocess.exit(${exitCode})\n`
  const path = join(dir, 'npm')
  writeFileSync(path, body)
  chmodSync(path, 0o755)
}

function runGate(): { code: number | null; out: string } {
  const res = spawnSync(process.execPath, [SCRIPT, '.'], {
    encoding: 'utf8',
    env: { ...process.env, PATH: `${dir}:${process.env.PATH ?? ''}` },
  })
  return { code: res.status, out: `${res.stdout}${res.stderr}` }
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'audit-gate-'))
})
afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
})

describe('audit-allowlist gate', () => {
  it('passes a clean audit (the fake npm is actually used)', () => {
    fakeNpm({ vulnerabilities: {} }, 0)
    const result = runGate()
    expect(result.code).toBe(0)
    expect(result.out).toMatch(/npm audit gate passed/)
  })

  it('fails a real high advisory', () => {
    fakeNpm(
      {
        vulnerabilities: {
          pkg: {
            severity: 'high',
            via: [{ url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc', title: 'bad' }],
          },
        },
      },
      1
    )
    expect(runGate().code).toBe(1)
  })

  it('fails when npm reports the audit itself failed, naming the dir and the error', () => {
    fakeNpm(
      { error: { code: 'ENOLOCK', summary: 'This command requires an existing lockfile.' } },
      1
    )
    const result = runGate()
    expect(result.code).not.toBe(0)
    expect(result.out).toMatch(/npm audit failed for \./)
    expect(result.out).toMatch(/ENOLOCK/)
    expect(result.out).not.toMatch(/npm audit gate passed/)
  })
})
