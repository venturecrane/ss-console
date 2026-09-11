/**
 * boot-smoke-test.sh runs EVERY check and reports all of them (ss#2487).
 *
 * The script used to `exit 1` on the first red. On hermes-ashton-price, the
 * paying client's seat, check 6 sampled a directory property while the
 * provisioner was still writing to that directory. A race: a manual re-run
 * passed 42/42 four minutes later. But the abort meant checks 7 through 42 never
 * ran, and those include `matter-mixing-fence`, the three credential-stripping
 * assertions, and the four medchron gate-refusal probes. The operator saw
 * "FATAL: dependency chain is unhealthy", which reads identically whether the
 * seat is healthy or genuinely compromised.
 *
 * WHY THIS TEST EXISTS AT ALL. The defect being fixed is that a gate was not
 * trustworthy. Shipping the fix on a syntax check would be the same mistake in a
 * smaller package, so the script is EXECUTED here against a stubbed `fly`: one
 * named check is made to fail and the rest succeed, and the assertions are that
 * the run reached the far end, that the summary names exactly the failure, and
 * that the exit code is still non-zero. A fix that reported green on a red check
 * would be worse than the abort it replaced.
 *
 * The stub is a real executable on PATH, not a mock: the script shells out to
 * `fly`, so anything less would test a different program.
 */

import { execFileSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

const SCRIPT = resolve('operator/bin/boot-smoke-test.sh')
const SEAT_YAML = resolve('operator/customers/pilot-smokeball/customer.yaml')

/**
 * The `machine.memory_mb` pilot-smokeball authors, for the `fly status` stub.
 *
 * Deliberately the same shape the smoke script itself uses (the first
 * `memory_mb:` inside the `machine:` block) rather than a YAML parse, so the two
 * readers cannot disagree about which key they mean. Throws rather than
 * defaulting: a silent fallback would make the stub agree with the script by
 * accident and this file would stop testing the comparison at all.
 */
function authoredSeatMemoryMb(): number {
  const lines = readFileSync(SEAT_YAML, 'utf8').split('\n')
  let inMachine = false
  for (const line of lines) {
    if (/^machine:/.test(line)) {
      inMachine = true
      continue
    }
    if (!inMachine) continue
    if (/^[^ \t#]/.test(line)) break
    const m = /^\s+memory_mb:\s*(\d+)/.exec(line)
    if (m) return Number(m[1])
  }
  throw new Error(`no machine.memory_mb found in ${SEAT_YAML}`)
}

/**
 * The stub dir plus a bare system PATH, and nothing else.
 *
 * Inheriting `process.env.PATH` would leave the developer's real `uv` reachable
 * behind the stub, so a local pass would say nothing about a runner that has no
 * `uv` at all. That is precisely the gap that let the first version of this file
 * pass here and fail all three cases in CI. Pinning the PATH makes the local run
 * the same run.
 */
function hermeticPath(stub: string): string {
  return [stub, '/usr/bin', '/bin', '/usr/sbin', '/sbin'].join(':')
}

// `SS_ENGAGEMENTS_DIR` is pointed at the stub dir on every run below.
//
// Step 14 reads the engagements checkout to decide whether the seat authors a
// medchron firm config, and a MISSING checkout is a precondition abort by
// design: Law 2's fail-closed rule applied to the expectation source, so that
// "cannot evaluate" never reads as "not expected". CI has no clone of a private
// repo, so without this the run FATALs at step 14 and never reaches the end --
// which is what it did on the second CI attempt.
//
// The stub dir exists and contains no medchron config, so the check evaluates
// honestly to "this seat does not author one" and the four gate probes are
// skipped. That is a real answer, not a bypass: the same branch a seat with no
// medchron authored would take.
const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})

/**
 * A `fly` on PATH that reports the Machine started and fails exactly the checks
 * whose command contains one of `failOn`.
 */
function stubDir(failOn: string[]): string {
  const dir = mkdtempSync(join(tmpdir(), 'boot-smoke-stub-'))
  scratch.push(dir)
  // Needles go in a FILE, read by `grep -F -f`, never interpolated into the
  // stub's source. The check commands contain both quote characters, and an
  // earlier version pasted them into a double-quoted bash test: the embedded
  // `"` closed the quote, the stub became a syntax error, and it exited
  // non-zero for every check -- which presented as "the script failed all 36",
  // a far more alarming and completely wrong result. Fixed data, not code.
  writeFileSync(join(dir, 'needles.txt'), failOn.join('\n'))
  // `fly status --json` answers two questions here: is the Machine started, and
  // what guest is it running. The stub satisfies both, or
  // `guest-matches-authored-size` fails for a reason unrelated to what is under
  // test.
  //
  // The size is READ from pilot-smokeball's customer.yaml rather than written
  // here. It used to be the literal 1024, and raising the seat's authored floor
  // to 2048 (the 2026-09-01 crash-loop fix) turned all three cases in this file
  // red for a reason that had nothing to do with what they assert. A fixture
  // that has to be edited whenever an unrelated authored value moves is a
  // tripwire on the wrong thing; deriving it means this file only ever fails for
  // its own reason. Reading it also proves the check is REAL: if
  // `guest-matches-authored-size` compared nothing, this stub could report any
  // number and the suite would not notice.
  const authoredMemoryMb = authoredSeatMemoryMb()
  const fly = `#!/usr/bin/env bash
for a in "$@"; do
  [ "$a" = "status" ] && {
    echo '{"Machines":[{"state":"started","config":{"guest":{"cpu_kind":"shared","cpus":1,"memory_mb":${authoredMemoryMb}}}}]}'
    exit 0
  }
done
NEEDLES="$(dirname "$0")/needles.txt"
if [ -s "$NEEDLES" ] && printf '%s' "$*" | grep -qFf "$NEEDLES"; then exit 1; fi
exit 0
`
  writeFileSync(join(dir, 'fly'), fly)
  chmodSync(join(dir, 'fly'), 0o755)

  // A `uv` stub, and the reason it has to exist is itself the point.
  //
  // Step 1b refuses to run without `uv` on PATH, deliberately: the workstation
  // python3 has no pyyaml, and an earlier version swallowed that ImportError
  // into empty authored values and FATALed a healthy seat with nonsense. That
  // refusal is a PRECONDITION, so on a runner without `uv` the script aborts
  // before a single ssh check - which is exactly what happened when this test
  // passed locally and failed in CI on all three cases. A test that only runs
  // where uv happens to be installed is a test that does not run.
  //
  // The stub answers the two questions step 1b asks, by grepping the authored
  // file rather than parsing it. Narrow on purpose: it recognises only the two
  // programs the script sends, and exits non-zero on anything else, so a future
  // third parse cannot be silently answered with a wrong value.
  const uv = `#!/usr/bin/env bash
prog=""; file=""
for a in "$@"; do
  # The file pattern is tested FIRST: the yaml path itself can contain the
  # word "machine" (a worktree named machine-credentials did, 2026-09-10),
  # and with the program pattern first that path was taken as the program,
  # the file was never set, and every case in this file went red for a
  # reason unrelated to what it asserts.
  case "$a" in
    */customer.yaml) file="$a" ;;
    *machine*|*hermes_ref*) prog="$a" ;;
  esac
done
[ -n "$file" ] || exit 1
case "$prog" in
  *hermes_ref*) awk -F@ '/^hermes_ref:/{gsub(/[[:space:]]/,"",$2); print $2; exit}' "$file" ;;
  *memory_mb*)  awk '/^machine:/{m=1;next} m&&/memory_mb:/{print $2;exit} m&&/^[^ ]/{exit}' "$file" ;;
  *size*)       awk '/^machine:/{m=1;next} m&&/size:/{print $2;exit} m&&/^[^ ]/{exit}' "$file" ;;
  *)            exit 1 ;;
esac
`
  writeFileSync(join(dir, 'uv'), uv)
  chmodSync(join(dir, 'uv'), 0o755)
  return dir
}

function run(
  failOn: string[],
  opts: { medchronAuthored?: boolean } = {}
): { out: string; code: number } {
  const dir = stubDir(failOn)
  if (opts.medchronAuthored) {
    // The other branch of step 14: the seat authors a medchron firm config, so
    // the config-perm, token, controls, and gate-refusal checks are EXPECTED
    // to run. An empty file is enough; the script only tests for presence.
    const firm = join(dir, 'operator', 'customers', 'pilot-smokeball', 'medchron')
    mkdirSync(firm, { recursive: true })
    writeFileSync(join(firm, 'firm.yaml'), '')
  }
  try {
    const out = execFileSync('bash', [SCRIPT, 'pilot-smokeball'], {
      encoding: 'utf8',
      env: { ...process.env, PATH: hermeticPath(dir), SS_ENGAGEMENTS_DIR: dir },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    return { out, code: 0 }
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; status?: number }
    return { out: `${e.stdout ?? ''}${e.stderr ?? ''}`, code: e.status ?? -1 }
  }
}

describe('boot smoke reports every check, not just the first red (ss#2487)', () => {
  it('a failing early check does not hide the later ones', () => {
    // `customer-yaml-dir-not-agent-writable` is check 6, the one that raced on
    // the client seat. The needle ends at the closing quote so it cannot also
    // match check 5's `/var/lib/smd-config/customer.yaml` -- an early version
    // did, and turned a one-failure assertion into a two-failure run.
    const { out, code } = run(['test -w /var/lib/smd-config"'])
    expect(out).toContain('FAIL: customer-yaml-dir-not-agent-writable')
    // The far end of the run: this line only prints after the last check.
    expect(out).toContain('All checks executed')
    // Checks that live AFTER the failure must have run.
    expect(out).toMatch(/(PASS|FAIL): hermes-plugins-installed/)
    expect(out).toMatch(/(PASS|FAIL): matter-mixing-fence/)
    expect(out).toMatch(/(PASS|FAIL): msgraph-send-credential-stripped-from-agent/)
    // And it is still a failure.
    expect(code).not.toBe(0)
    expect(out).toContain('checks failed: 1')
  })

  it('the summary names every failure, not just a count', () => {
    const { out } = run(['test -w /var/lib/smd-config"', 'curator'])
    expect(out).toContain('checks failed: 2')
    expect(out).toContain('FAILED: customer-yaml-dir-not-agent-writable')
    expect(out).toContain('FAILED: curator-disabled')
  })

  // 2026-09-04: a seat that authors a medchron firm config must carry the
  // install-level controls tree the entrypoint seeds from the vault (the
  // classifier's falsifier and the ICD tables); without it every job refuses
  // at classify_scanned. The check is gated on the SAME authored expectation
  // as the firm-config checks, so it is invisible in the runs above (no
  // medchron authored). This case authors one, proves the three controls
  // checks run, and that the presence check can go red.
  it('a seat that authors medchron must carry the seeded controls tree, and the check can fail', () => {
    const { out, code } = run(['/run/smd-medchron/controls/controls.json'], {
      medchronAuthored: true,
    })
    expect(out).toContain('FAIL: medchron-controls-present')
    expect(out).toContain('FAIL: medchron-uid-reads-controls-cannot-write')
    expect(out).toMatch(/(PASS|FAIL): medchron-icd-tables-present/)
    expect(out).toMatch(/(PASS|FAIL): medchron-gate-claim-audit-refuses/)
    expect(out).not.toContain('medchron-firm-config-absent')
    expect(code).not.toBe(0)
    expect(out).toContain('checks failed: 2')
    // The control: authored, seeded, green.
    const clean = run([], { medchronAuthored: true })
    expect(clean.code).toBe(0)
    expect(clean.out).toContain('PASS: medchron-controls-present')
    expect(clean.out).toContain('PASS: medchron-icd-tables-present')
  }, 90_000)

  it('a clean run still exits zero', () => {
    // The control. Without it, every assertion above would pass on a script that
    // failed everything, and "reports all failures" would be trivially true.
    const { out, code } = run([])
    expect(code).toBe(0)
    expect(out).toContain('checks failed: 0')
    expect(out).not.toContain('FAIL:')
  })

  it('a precondition abort still summarizes what had run', () => {
    // The Machine never reaching `started` is not a check result: every ssh
    // check after it would fail for one reason, which is a different way of
    // hiding the answer. It aborts, but not silently.
    const dir = mkdtempSync(join(tmpdir(), 'boot-smoke-stub-'))
    scratch.push(dir)
    writeFileSync(
      join(dir, 'fly'),
      `#!/usr/bin/env bash
for a in "$@"; do [ "$a" = "status" ] && { echo '{"Machines":[{"state":"stopped"}]}'; exit 0; }; done
exit 0
`
    )
    chmodSync(join(dir, 'fly'), 0o755)
    let out: string
    let code = 0
    try {
      out = execFileSync('bash', [SCRIPT, 'pilot-smokeball'], {
        encoding: 'utf8',
        env: { ...process.env, PATH: hermeticPath(dir), SS_ENGAGEMENTS_DIR: dir },
        stdio: ['ignore', 'pipe', 'pipe'],
      })
    } catch (err) {
      const e = err as { stdout?: string; stderr?: string; status?: number }
      out = `${e.stdout ?? ''}${e.stderr ?? ''}`
      code = e.status ?? -1
    }
    expect(code).not.toBe(0)
    expect(out).toContain('FATAL: machine-state-started')
    expect(out).toContain('boot smoke summary')
  }, 90_000)
})
