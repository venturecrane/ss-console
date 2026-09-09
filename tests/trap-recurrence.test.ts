/**
 * The trap-recurrence counter, asserted by EXECUTING the hook.
 *
 * WHY THIS TEST EXISTS AT ALL. `.claude/**` is excluded from eslint, prettier
 * and typecheck, so this file is the only gate on
 * `.claude/hooks/trap-recurrence.mjs`.
 *
 * WHY THE COUNTER EXISTS. MEMORY.md's traps block pre-registers a review:
 * "On/after 2026-09-23: check whether any of these still recurred; if so a
 * delivery hook is justified, if not this tier is enough." On 2026-09-09 that
 * was a dated decision rule with no instrument -- the same shape as the
 * silent SessionStart hook fixed in #2724, where a criterion nobody could
 * measure reads as a criterion that is being met.
 *
 * EVERY RULE IS ASSERTED IN BOTH DIRECTIONS (Law 12), and the direction that
 * matters most here is the one that must NOT fire: zero is the answer this
 * counter is under the most pressure to return, because zero closes the review
 * quietly. A counter that cannot distinguish a handled encounter from a stumble
 * would report either all-clear or all-alarm forever.
 *
 * The two narrowing tests at the end are regressions against real defects found
 * by running the first draft over 32 sessions of history: a stale-pyc rule that
 * latched on the first .py edit scored 1,056 stumbles, and a pgrep rule whose
 * remedy could never match scored 58 encounters and 58 stumbles. Both would
 * have handed the review a predetermined answer.
 */
import { execFileSync } from 'node:child_process'
import { closeSync, mkdtempSync, openSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, afterEach } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, '.claude', 'hooks', 'trap-recurrence.mjs')

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})

function scratchDir(prefix: string): string {
  const dir = mkdtempSync(join(tmpdir(), prefix))
  scratch.push(dir)
  return dir
}

function cleanEnv(extra: Record<string, string> = {}): Record<string, string> {
  return {
    ...Object.fromEntries(
      Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
    ),
    ...extra,
  }
}

interface TrapRow {
  encounters: number
  stumbles: number
  sessions: string[]
}
interface Report {
  ok: boolean
  missing?: true
  since: string
  sessionsScanned: number
  encounters: number
  stumbles: number
  verdict: string
  byTrap: Record<string, TrapRow>
  blindSpots: string[]
}

function run(args: string[]): { stdout: string; stderr: string; status: number } {
  const tmp = scratchDir('ss-trap-err-')
  const errPath = join(tmp, 'stderr.txt')
  const errFd = openSync(errPath, 'w')
  let out: { stdout: string; status: number }
  try {
    const stdout = execFileSync('node', [SCRIPT, ...args], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', errFd],
      env: cleanEnv(),
    })
    out = { stdout, status: 0 }
  } catch (err) {
    const e = err as { status?: number; stdout?: string }
    out = { stdout: e.stdout ?? '', status: e.status ?? -1 }
  } finally {
    closeSync(errFd)
  }
  return { ...out, stderr: readFileSync(errPath, 'utf8') }
}

function scanDir(dir: string): { report: Report; status: number } {
  const r = run([dir, '--since', '2000-01-01'])
  expect(r.stdout.trim(), `no JSON on stdout (stderr: ${r.stderr})`).not.toBe('')
  return { report: JSON.parse(r.stdout) as Report, status: r.status }
}

/** A transcript is JSONL of messages carrying tool_use / tool_result blocks. */
type Block = Record<string, unknown>
const msg = (blocks: Block[]): string => JSON.stringify({ message: { content: blocks } }) + '\n'
const use = (id: string, name: string, input: Record<string, unknown>): Block => ({
  type: 'tool_use',
  id,
  name,
  input,
})
const res = (id: string, text: string): Block => ({
  type: 'tool_result',
  tool_use_id: id,
  content: [{ text }],
})

function transcript(dir: string, name: string, blocks: string): void {
  writeFileSync(join(dir, name), blocks)
}

describe('trap-recurrence: a handled encounter is not a stumble', () => {
  it('counts the unremedied BEHIND and not the remedied one', () => {
    const dir = scratchDir('ss-trap-behind-')
    transcript(
      dir,
      'stumble.jsonl',
      msg([use('a1', 'Bash', { command: 'gh pr view 1 --json mergeStateStatus' })]) +
        msg([res('a1', '{"mergeStateStatus":"BEHIND"}')]) +
        msg([use('a2', 'Bash', { command: 'gh pr merge 1 --squash' })]) +
        msg([res('a2', '')])
    )
    transcript(
      dir,
      'handled.jsonl',
      msg([use('b1', 'Bash', { command: 'gh pr view 2 --json mergeStateStatus' })]) +
        msg([res('b1', '{"mergeStateStatus":"BEHIND"}')]) +
        msg([use('b2', 'Bash', { command: 'gh pr update-branch 2' })]) +
        msg([res('b2', 'PR branch updated')])
    )

    const { report, status } = scanDir(dir)
    expect(report.byTrap['behind-pr'].encounters).toBe(2)
    expect(report.byTrap['behind-pr'].stumbles).toBe(1)
    expect(report.verdict).toMatch(/^RECURRED/)
    expect(status).toBe(1)
  })

  it('reports HELD when every encounter was remedied, and exits 0', () => {
    const dir = scratchDir('ss-trap-held-')
    transcript(
      dir,
      'handled.jsonl',
      msg([use('b1', 'Bash', { command: 'gh pr view 2 --json mergeStateStatus' })]) +
        msg([res('b1', '{"mergeStateStatus":"BEHIND"}')]) +
        msg([use('b2', 'Bash', { command: 'gh pr update-branch 2' })]) +
        msg([res('b2', 'PR branch updated')])
    )
    const { report, status } = scanDir(dir)
    expect(report.stumbles).toBe(0)
    expect(report.encounters).toBe(1)
    expect(report.verdict).toMatch(/^HELD/)
    expect(status).toBe(0)
  })

  it('distinguishes an empty window from a clean one', () => {
    const dir = scratchDir('ss-trap-empty-')
    transcript(dir, 'quiet.jsonl', msg([use('c1', 'Bash', { command: 'ls -la' })]))
    const { report, status } = scanDir(dir)
    expect(report.verdict).toMatch(/^NO EVIDENCE/)
    expect(report.encounters).toBe(0)
    expect(status).toBe(0)
  })
})

describe('trap-recurrence: pairing and windowing', () => {
  it('pairs output to its own call by tool_use_id, not by position', () => {
    // Two calls issued together, results returned in the opposite order. If the
    // scanner paired by position it would attribute the BEHIND output to `ls`.
    const dir = scratchDir('ss-trap-pairing-')
    transcript(
      dir,
      's.jsonl',
      msg([
        use('x1', 'Bash', { command: 'ls -la' }),
        use('x2', 'Bash', { command: 'gh pr view 3 --json mergeStateStatus' }),
      ]) +
        msg([res('x2', '{"mergeStateStatus":"BEHIND"}'), res('x1', 'total 0')]) +
        msg([use('x3', 'Bash', { command: 'gh pr update-branch 3' })]) +
        msg([res('x3', 'ok')])
    )
    const { report } = scanDir(dir)
    expect(report.byTrap['behind-pr'].encounters).toBe(1)
    expect(report.byTrap['behind-pr'].stumbles).toBe(0)
  })

  it('survives a partially flushed final line, as a live session always has', () => {
    const dir = scratchDir('ss-trap-partial-')
    transcript(
      dir,
      's.jsonl',
      msg([use('y1', 'Bash', { command: 'gh pr view 4 --json mergeStateStatus' })]) +
        msg([res('y1', '{"mergeStateStatus":"BEHIND"}')]) +
        '{"message": {"content": [{"type": "tool_'
    )
    const { report } = scanDir(dir)
    expect(report.byTrap['behind-pr'].encounters).toBe(1)
  })
})

describe('trap-recurrence: rules narrowed after running over real history', () => {
  /**
   * The first draft latched on the first .py edit in a session and then counted
   * EVERY later python invocation, scoring 1,128 encounters over 32 sessions.
   * The trap is a mutation pass -- run, edit, run the same file again -- so an
   * unrelated python call after an unrelated edit must not register.
   */
  it('stale-pyc fires on a re-run of an edited file, not on any python call', () => {
    const dir = scratchDir('ss-trap-pyc-')
    transcript(
      dir,
      'mutation.jsonl',
      msg([use('m1', 'Bash', { command: 'python3 falsifier.py' })]) +
        msg([res('m1', 'ok')]) +
        msg([use('m2', 'Write', { file_path: '/x/falsifier.py' })]) +
        msg([res('m2', 'written')]) +
        msg([use('m3', 'Bash', { command: 'python3 falsifier.py' })]) +
        msg([res('m3', 'ok')])
    )
    transcript(
      dir,
      'unrelated.jsonl',
      msg([use('n1', 'Write', { file_path: '/x/helper.py' })]) +
        msg([res('n1', 'written')]) +
        msg([use('n2', 'Bash', { command: 'python3 other.py' })]) +
        msg([res('n2', 'ok')])
    )
    const { report } = scanDir(dir)
    expect(report.byTrap['stale-pyc'].encounters).toBe(1)
    expect(report.byTrap['stale-pyc'].stumbles).toBe(1)
  })

  it('stale-pyc clears when bytecode caching is disabled on the re-run', () => {
    const dir = scratchDir('ss-trap-pyc-clean-')
    transcript(
      dir,
      'mutation.jsonl',
      msg([use('m1', 'Bash', { command: 'python3 falsifier.py' })]) +
        msg([res('m1', 'ok')]) +
        msg([use('m2', 'Write', { file_path: '/x/falsifier.py' })]) +
        msg([res('m2', 'written')]) +
        msg([use('m3', 'Bash', { command: 'python3 falsifier.py' })]) +
        msg([res('m3', 'ok')]) +
        msg([use('m4', 'Bash', { command: 'PYTHONDONTWRITEBYTECODE=1 python3 falsifier.py' })]) +
        msg([res('m4', 'ok')])
    )
    const { report } = scanDir(dir)
    expect(report.byTrap['stale-pyc'].stumbles).toBe(0)
  })

  /**
   * The first draft's pgrep rule had a remedy that could never match, so all 58
   * historical encounters scored as stumbles. A bare `pgrep -f` is not the
   * failure; trusting the pid it returns is. A check that cannot pass is as
   * empty as one that cannot fail.
   */
  it('pgrep only stumbles when the pid is then trusted', () => {
    const dir = scratchDir('ss-trap-pgrep-')
    transcript(
      dir,
      'harmless.jsonl',
      msg([use('p1', 'Bash', { command: 'pgrep -f smd-operator' })]) + msg([res('p1', '4242')])
    )
    transcript(
      dir,
      'harmful.jsonl',
      msg([use('q1', 'Bash', { command: 'pgrep -f smd-operator' })]) +
        msg([res('q1', '4242')]) +
        msg([use('q2', 'Bash', { command: 'cat /proc/4242/environ' })]) +
        msg([res('q2', 'PATH=...')])
    )
    const { report } = scanDir(dir)
    expect(report.byTrap['pgrep-own-shell'].encounters).toBe(2)
    expect(report.byTrap['pgrep-own-shell'].stumbles).toBe(1)
  })
})

describe('trap-recurrence: the built-in falsifier and its blind spots', () => {
  it('self-test passes and names both directions', () => {
    const r = run(['--self-test'])
    expect(r.status).toBe(0)
    expect(r.stderr).toContain('planted stumble caught')
    expect(r.stderr).toContain('handled encounter not counted')
  })

  it('every run declares what it cannot see', () => {
    const dir = scratchDir('ss-trap-blind-')
    transcript(dir, 'q.jsonl', msg([use('z1', 'Bash', { command: 'ls' })]))
    const { report } = scanDir(dir)
    expect(report.blindSpots.length).toBeGreaterThan(0)
    expect(report.blindSpots.join(' ')).toContain('subagent')
  })

  it('fails loud on an unreadable transcript directory rather than reporting clean', () => {
    const r = run([join(tmpdir(), 'ss-trap-does-not-exist-ever'), '--since', '2000-01-01'])
    expect(r.status).toBe(1)
    const report = JSON.parse(r.stdout) as Report
    expect(report.ok).toBe(false)
    expect(report.missing).toBe(true)
  })

  it('rejects a --since that is not a date instead of scanning everything', () => {
    const r = run(['--since', 'last tuesday'])
    expect(r.status).toBe(1)
    expect(r.stderr).toContain('is not a date')
  })
})
