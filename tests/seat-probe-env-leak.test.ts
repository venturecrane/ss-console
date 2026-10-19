/**
 * seat-probe.sh: the probe command must never reach `env` as a bare word.
 *
 * WHY THIS EXISTS (2026-10-18). The wrapper drops to hermes with
 * `runuser -u hermes -- env -i ${ENVV} PATH=... <command>`. The command used to
 * arrive as `printf %q` words. A probe passed ONE string,
 * `echo <base64 ending in ==> | base64 -d | python3`, which %q turned into ONE
 * backslash-escaped word containing `=`. `env` read that word as a NAME=VALUE
 * assignment, had no program left to run, and so did what `env` does with no
 * program: it PRINTED THE ENVIRONMENT, which on a seat is the gateway's whole
 * environ, every secret value included, into the agent transcript.
 *
 * The fix hands `env` a real program every time (`/bin/sh -c "$CMD"`) and
 * carries the command base64-encoded so no quoting survives or fails to survive
 * the trip; the seat refuses an empty command before `env` is reached.
 *
 * This file runs the wrapper's REAL remote script locally. `fly` is stubbed to
 * execute the -C string with sh, `pgrep` and `runuser` are stubbed, and the
 * gateway's /proc/<pid>/environ is a fake holding a canary secret. No Machine,
 * no network, no fly account. The canary must never appear in any output.
 * Against the pre-fix script, the `==` case printed it (the falsifier).
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { spawnSync } from 'child_process'
import { mkdtempSync, writeFileSync, chmodSync, rmSync, mkdirSync, existsSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'

const SCRIPT = resolve('operator/bin/seat-probe.sh')
const CANARY = 'leakme_canary_7f3a'

let dir: string

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'seat-probe-'))
  const bin = join(dir, 'bin')
  mkdirSync(bin)
  mkdirSync(join(dir, 'proc', '4242'), { recursive: true })
  // The gateway's environ: NUL-separated, a canary secret among ordinary keys.
  writeFileSync(
    join(dir, 'proc', '4242', 'environ'),
    `HOME=/opt/data\0SECRET_CANARY=${CANARY}\0HERMES_PROFILE=operator\0`
  )
  // `fly ssh console -a APP -C "<string>"`: run the string the way the seat
  // does (shell-word parsing, then exec), with /proc pointed at the fake.
  // FAKE_BLANK_PAYLOAD tampers the transport to an empty command so the
  // SEAT-side refusal is tested, not only the caller-side one.
  writeFileSync(
    join(bin, 'fly'),
    `#!/bin/bash
echo x >> "${dir}/fly-calls"
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-C" ]; then C="$2"; break; fi
  shift
done
C="$(printf '%s' "$C" | sed "s#/proc/#${dir}/proc/#g")"
if [ -n "\${FAKE_BLANK_PAYLOAD:-}" ]; then
  C="$(printf '%s' "$C" | sed -E 's/CMD_B64=[A-Za-z0-9+\\/=]*/CMD_B64=/')"
fi
exec sh -c "$C"
`
  )
  writeFileSync(join(bin, 'pgrep'), '#!/bin/sh\necho 4242\n')
  // runuser -u hermes -- <cmd...>: drop the uid switch, exec the rest.
  writeFileSync(
    join(bin, 'runuser'),
    '#!/bin/sh\nwhile [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'
  )
  for (const f of ['fly', 'pgrep', 'runuser']) chmodSync(join(bin, f), 0o755)
})

afterAll(() => {
  rmSync(dir, { recursive: true, force: true })
})

function probe(args: string[], extraEnv: Record<string, string> = {}) {
  const r = spawnSync('bash', [SCRIPT, 'test-seat', ...args], {
    encoding: 'utf-8',
    env: {
      PATH: `${join(dir, 'bin')}:/usr/bin:/bin`,
      HOME: dir,
      SMD_ACTOR: 'test',
      ...extraEnv,
    },
  })
  const out = `${r.stdout}\n${r.stderr}`
  return { status: r.status, stdout: r.stdout, out }
}

describe('seat-probe.sh never hands env a bare word (2026-10-18 env dump)', () => {
  it('a command with == and a pipe runs, and the canary never appears', () => {
    // "echo ok" base64-encoded is ZWNobyBvaw== : the incident's exact shape.
    const r = probe(['echo ZWNobyBvaw== | base64 -d | sh'])
    expect(r.out).not.toContain(CANARY)
    expect(r.out).not.toContain('SECRET_CANARY')
    expect(r.stdout.trim()).toBe('ok')
    expect(r.status).toBe(0)
  })

  it('an assignment-shaped single word is run as a command, never as env input', () => {
    const r = probe(['FOO=bar'])
    expect(r.out).not.toContain(CANARY)
  })

  it('single and double quotes survive in a one-string command', () => {
    const r = probe([`echo "single ' and double \\" and eq=="`])
    expect(r.out).not.toContain(CANARY)
    expect(r.stdout.trim()).toBe(`single ' and double " and eq==`)
  })

  it('a multi-argument command keeps argv semantics, quotes intact', () => {
    const r = probe(['printf', '%s|%s\\n', `it's`, 'say "hi" a=b'])
    expect(r.out).not.toContain(CANARY)
    expect(r.stdout.trim()).toBe(`it's|say "hi" a=b`)
  })

  it('a multi-argument command with a trailing empty argument still runs', () => {
    const r = probe(['printf', '[%s]', ''])
    expect(r.out).not.toContain(CANARY)
    expect(r.stdout.trim()).toBe('[]')
  })

  it('redirection inside the command works', () => {
    const target = join(dir, 'redir-out')
    const r = probe([`echo redirected > ${target} && cat ${target}`])
    expect(r.out).not.toContain(CANARY)
    expect(r.stdout.trim()).toBe('redirected')
    expect(existsSync(target)).toBe(true)
  })

  it('the probe still receives the gateway env (the allowlist is not broken)', () => {
    // Prove the canary IS in the probe's environment, so the absence above is
    // because nothing printed it, not because the fake never delivered it.
    const r = probe(['test "$SECRET_CANARY" = ' + CANARY + ' && echo env-delivered'])
    expect(r.stdout.trim()).toBe('env-delivered')
    expect(r.out).not.toContain(CANARY)
  })

  it('an empty command is refused on the caller side without printing the canary', () => {
    for (const args of [[''], ['   '], ['', '']]) {
      const r = probe(args)
      expect(r.status).not.toBe(0)
      expect(r.out).not.toContain(CANARY)
      expect(r.out).toMatch(/empty command/)
    }
  })

  it('an empty command that reaches the seat is refused there without printing the canary', () => {
    const r = probe(['echo should-not-run'], { FAKE_BLANK_PAYLOAD: '1' })
    expect(r.status).not.toBe(0)
    expect(r.out).not.toContain(CANARY)
    expect(r.out).not.toContain('should-not-run')
    expect(r.out).toMatch(/empty command/)
  })
})
