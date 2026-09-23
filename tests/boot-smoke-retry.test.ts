/**
 * boot-smoke-test.sh: the cycling-Machine retry, and the guarantee that it
 * cannot turn a real failure green.
 *
 * WHY THIS EXISTS (2026-09-23). Step 1 of the boot smoke waits for Machine
 * state=started, but that gate passes ONCE, at the top, and the run that
 * follows takes about fourteen minutes. On a pilot rebuild the Machine went to
 * `replacing` at 15:27:32 and two medchron gate probes ran at 15:27:39 — seven
 * seconds later, against a Machine that was being replaced. Both were recorded
 * as failed gates and the provision exited FATAL on a seat that was healthy:
 * re-run on a settled Machine, both returned REFUSED, which is what they are
 * supposed to return.
 *
 * A boot smoke that cries wolf is worse than one that is slow, because the next
 * FATAL is the one nobody reads. Hence one retry, gated on the Machine actually
 * not being started.
 *
 * THE RISK THE RETRY INTRODUCES is the reason for this file. A retry is a way
 * to make a check unable to fail, and a check that cannot fail has measured
 * nothing. So the load-bearing test here is the THIRD one: a command that keeps
 * failing on a settled Machine is still recorded as a failure. Delete the
 * retry's `&&` and test 2 fails; weaken the retry into "always retry until it
 * passes" and test 3 fails.
 *
 * The helpers are sourced with `fly` stubbed on PATH, so no Machine, no
 * network, and no fly account is involved.
 */

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { execFileSync } from 'child_process'
import { mkdtempSync, writeFileSync, chmodSync, rmSync, readFileSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'

const SCRIPT = resolve('operator/bin/boot-smoke-test.sh')

let dir: string

/** A `fly` stub whose `status --json` reports the given machine state. */
function writeFlyStub(state: string, sshExit: number): void {
  writeFileSync(
    join(dir, 'fly'),
    `#!/bin/bash
if [ "$1" = "status" ]; then
  echo '{"Machines":[{"state":"${state}"}]}'
  exit 0
fi
if [ "$1" = "ssh" ]; then
  # Count attempts so a test can assert how many times the check actually ran.
  echo x >> "${dir}/ssh-attempts"
  exit ${sshExit}
fi
exit 0
`,
    { mode: 0o755 }
  )
  chmodSync(join(dir, 'fly'), 0o755)
}

/**
 * Source the script's helper definitions only (everything above the first
 * "---------- Step" banner), then run `body`. Sourcing the whole file would
 * execute a real provision.
 */
function runWithHelpers(body: string): string {
  const full = readFileSync(SCRIPT, 'utf-8')
  const cut = full.indexOf('# ---------- Step 1')
  expect(
    cut,
    'the Step 1 banner must exist; the helper/steps split is what makes this testable'
  ).toBeGreaterThan(0)
  const helpers = full.slice(0, cut)
  const harness = join(dir, 'harness.sh')
  writeFileSync(
    harness,
    `APP_NAME=stub-app
FAILED_CHECKS=()
summarize() { :; }
${helpers}
trap - EXIT
${body}
`
  )
  // The helper region carries the script's own argument parsing, so the harness
  // needs a slug to get past it. `fly` is stubbed, so nothing is contacted.
  return execFileSync('bash', [harness, 'pilot-smokeball'], {
    encoding: 'utf-8',
    // A short settle budget so the "never comes back" case does not spend the
    // production default (120s) inside a unit test. The retry LOGIC is what is
    // under test; the budget is a knob.
    env: { ...process.env, PATH: `${dir}:${process.env.PATH}`, BOOT_SMOKE_SETTLE_S: '3' },
  })
}

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'bootsmoke-'))
})
afterAll(() => {
  rmSync(dir, { recursive: true, force: true })
})

describe('boot smoke: the cycling-Machine retry', () => {
  it('does NOT retry when the Machine is started — a failure there is the check′s own', () => {
    writeFlyStub('started', 1)
    writeFileSync(join(dir, 'ssh-attempts'), '')
    const out = runWithHelpers(`
ssh_exec "a-failing-check" "false"
echo "FAILED_COUNT=\${#FAILED_CHECKS[@]}"
echo "ATTEMPTS=$(wc -l < "${dir}/ssh-attempts" | tr -d ' ')"
`)
    expect(out).toContain('FAILED_COUNT=1')
    // One attempt only: a settled Machine gets no second bite.
    expect(out).toMatch(/ATTEMPTS=1/)
  })

  it('retries once when the Machine is NOT started, and passes if the retry succeeds', () => {
    // `replacing` on the first state read, and the ssh succeeds. await_started
    // then sees `started` because the stub is rewritten between the two reads.
    writeFileSync(join(dir, 'ssh-attempts'), '')
    writeFileSync(
      join(dir, 'fly'),
      `#!/bin/bash
if [ "$1" = "status" ]; then
  if [ -f "${dir}/settled" ]; then echo '{"Machines":[{"state":"started"}]}'
  else touch "${dir}/settled"; echo '{"Machines":[{"state":"replacing"}]}'; fi
  exit 0
fi
if [ "$1" = "ssh" ]; then
  echo x >> "${dir}/ssh-attempts"
  # Fail the first attempt, succeed the second — the real shape of the race.
  [ "$(wc -l < "${dir}/ssh-attempts" | tr -d ' ')" -ge 2 ] && exit 0
  exit 1
fi
exit 0
`,
      { mode: 0o755 }
    )
    chmodSync(join(dir, 'fly'), 0o755)
    rmSync(join(dir, 'settled'), { force: true })

    const out = runWithHelpers(`
ssh_exec "a-check-caught-mid-replace" "true"
echo "FAILED_COUNT=\${#FAILED_CHECKS[@]}"
echo "ATTEMPTS=$(wc -l < "${dir}/ssh-attempts" | tr -d ' ')"
`)
    expect(out).toContain('FAILED_COUNT=0')
    expect(out).toContain('passed on retry')
    expect(out).toMatch(/ATTEMPTS=2/)
  })

  it('STILL FAILS when the command keeps failing after the Machine settles', () => {
    // The load-bearing one. If the retry could mask a genuine failure, this is
    // where it would show, and the whole change would be a way of not knowing.
    writeFileSync(join(dir, 'ssh-attempts'), '')
    writeFileSync(
      join(dir, 'fly'),
      `#!/bin/bash
if [ "$1" = "status" ]; then
  if [ -f "${dir}/settled2" ]; then echo '{"Machines":[{"state":"started"}]}'
  else touch "${dir}/settled2"; echo '{"Machines":[{"state":"replacing"}]}'; fi
  exit 0
fi
if [ "$1" = "ssh" ]; then
  echo x >> "${dir}/ssh-attempts"
  exit 1
fi
exit 0
`,
      { mode: 0o755 }
    )
    chmodSync(join(dir, 'fly'), 0o755)
    rmSync(join(dir, 'settled2'), { force: true })

    const out = runWithHelpers(`
ssh_exec "a-genuinely-broken-check" "false"
echo "FAILED_COUNT=\${#FAILED_CHECKS[@]}"
echo "ATTEMPTS=$(wc -l < "${dir}/ssh-attempts" | tr -d ' ')"
`)
    expect(out).toContain('FAILED_COUNT=1')
    expect(out).not.toContain('passed on retry')
    // Exactly two: one retry, never a loop until it passes.
    expect(out).toMatch(/ATTEMPTS=2/)
  })

  // Two settle waits at the 3s test budget (the explicit await_started, then
  // the retry's own) put this just over vitest's 5s default.
  it(
    'records the failure rather than hanging when the Machine never comes back',
    { timeout: 20_000 },
    () => {
      writeFlyStub('replacing', 1)
      writeFileSync(join(dir, 'ssh-attempts'), '')
      const out = runWithHelpers(`
await_started 3 && echo "UNEXPECTED: await_started returned 0" || echo "await_started gave up"
ssh_exec "a-check-on-a-dead-machine" "false"
echo "FAILED_COUNT=\${#FAILED_CHECKS[@]}"
`)
      expect(out).toContain('await_started gave up')
      expect(out).toContain('FAILED_COUNT=1')
    }
  )
})
