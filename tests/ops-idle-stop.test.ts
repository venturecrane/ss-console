/**
 * ops-idle-stop stops a client ops workspace after IDLE_MINUTES of idle, and
 * must never stop it under running work.
 *
 * On 2026-11-20 the A&P workspace had not stopped since 2026-10-22: every run
 * ended "busy: tmux client attached" because a terminal left open on a laptop
 * (last keystroke 688 minutes earlier) counted as busy forever. Removing that
 * rule alone would let the Machine stop under a Claude session that is working
 * with no child process, so a window that printed recently is busy too.
 *
 * These tests run the real script with `tmux` and `ps` replaced by PATH shims,
 * a fake HOME, and a fake uptime file, and read the line it logs and whether it
 * wrote state/stop-requested.
 */
import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, 'operator', 'ops-machine', 'bin', 'ops-idle-stop')

const dirs: string[] = []
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true })
})

const MIN = 60
const now = () => Math.floor(Date.now() / 1000)

// An idle Machine's process list: shells, tmux, cron, and Claude at its prompt.
const IDLE_PS = [
  '10 1 bash bash',
  '11 1 tmux: tmux: server',
  '12 11 bash -bash',
  '13 12 claude /usr/local/bin/node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js',
  '14 1 cron cron -f',
]

interface Setup {
  clients?: string[] // "<tty> <client_activity epoch>"
  windows?: string[] // "<session:index> <window_activity epoch>"
  tmux?: 'ok' | 'noserver' | 'error'
  ps?: string[]
  uptimeSec?: number
  lastBusyAgoSec?: number
  livePidLock?: boolean
}

function run(s: Setup): { out: string; stopRequested: boolean } {
  const root = mkdtempSync(join(tmpdir(), 'ops-idle-'))
  dirs.push(root)
  const home = join(root, 'home')
  const shims = join(root, 'shims')
  mkdirSync(join(home, 'state'), { recursive: true })
  mkdirSync(shims)
  writeFileSync(
    join(root, 'clients'),
    (s.clients ?? []).join('\n') + (s.clients?.length ? '\n' : '')
  )
  writeFileSync(
    join(root, 'windows'),
    (s.windows ?? []).join('\n') + (s.windows?.length ? '\n' : '')
  )
  writeFileSync(join(root, 'ps'), (s.ps ?? IDLE_PS).join('\n') + '\n')
  writeFileSync(join(root, 'uptime'), `${s.uptimeSec ?? 10 * 24 * 3600}.42 1234.5\n`)
  const mode = s.tmux ?? 'ok'
  writeFileSync(
    join(shims, 'tmux'),
    [
      '#!/bin/bash',
      `case "${mode}" in`,
      '  noserver) echo "no server running on /tmp/tmux-1000/default" >&2; exit 1;;',
      '  error) echo "lost server" >&2; exit 1;;',
      'esac',
      'case "$1" in',
      `  list-clients) cat "${join(root, 'clients')}";;`,
      `  list-windows) cat "${join(root, 'windows')}";;`,
      '  *) exit 1;;',
      'esac',
    ].join('\n') + '\n'
  )
  writeFileSync(join(shims, 'ps'), `#!/bin/bash\ncat "${join(root, 'ps')}"\n`)
  chmodSync(join(shims, 'tmux'), 0o755)
  chmodSync(join(shims, 'ps'), 0o755)
  if (s.lastBusyAgoSec !== undefined) {
    writeFileSync(join(home, 'state', 'last-busy'), `${now() - s.lastBusyAgoSec}\n`)
  }
  if (s.livePidLock) {
    mkdirSync(join(home, 'locks'))
    writeFileSync(join(home, 'locks', 'chronology.lock'), `${process.pid}\n`)
  }
  const out = execFileSync('bash', [SCRIPT], {
    env: {
      PATH: `${shims}:${process.env.PATH}`,
      HOME: home,
      IDLE_MINUTES: '60',
      OPS_IDLE_UPTIME_FILE: join(root, 'uptime'),
    },
    encoding: 'utf8',
    // The script calls /opt/ops/bin/ops-reap-tasks, absent here; its error is noise.
    stdio: ['ignore', 'pipe', 'ignore'],
  })
  return { out, stopRequested: existsSync(join(home, 'state', 'stop-requested')) }
}

describe('ops-idle-stop', () => {
  it('an attached terminal typed in 5 minutes ago is busy', () => {
    const r = run({ clients: [`/dev/pts/0 ${now() - 5 * MIN}`], lastBusyAgoSec: 70 * MIN })
    expect(r.out).toContain('busy: terminal input 5m ago')
    expect(r.stopRequested).toBe(false)
  })

  it('a terminal attached but untouched for 700 minutes does not keep the Machine up', () => {
    const r = run({
      clients: [`/dev/pts/0 ${now() - 700 * MIN}`],
      windows: [`main:0 ${now() - 700 * MIN}`],
      lastBusyAgoSec: 70 * MIN,
    })
    expect(r.out).toContain('idle 70m; requesting stop (terminal attached, last input 700m ago)')
    expect(r.stopRequested).toBe(true)
  })

  it('an untouched terminal counts toward the stop but does not stop before IDLE_MINUTES', () => {
    const r = run({
      clients: [`/dev/pts/0 ${now() - 700 * MIN}`],
      windows: [`main:0 ${now() - 700 * MIN}`],
      lastBusyAgoSec: 40 * MIN,
    })
    expect(r.out).toContain('idle 40m of 60')
    expect(r.stopRequested).toBe(false)
  })

  it('a window that printed 2 minutes ago is busy with nobody attached (a working Claude)', () => {
    const r = run({
      windows: [`main:0 ${now() - 300 * MIN}`, `main:7 ${now() - 2 * MIN}`],
      lastBusyAgoSec: 70 * MIN,
    })
    expect(r.out).toContain('busy: window main:7 output 2m ago')
    expect(r.stopRequested).toBe(false)
  })

  it('a process not on the idle list is busy, as before', () => {
    const r = run({
      clients: [`/dev/pts/0 ${now() - 700 * MIN}`],
      ps: [...IDLE_PS, '99 12 sha256sum sha256sum -c manifest'],
      lastBusyAgoSec: 70 * MIN,
    })
    expect(r.out).toContain('busy: job running: 99 sha256sum')
    expect(r.stopRequested).toBe(false)
  })

  it('a live lock is busy, as before', () => {
    const r = run({ livePidLock: true, lastBusyAgoSec: 70 * MIN })
    expect(r.out).toContain('busy: lock chronology.lock')
    expect(r.stopRequested).toBe(false)
  })

  it('a Machine up less than IDLE_MINUTES is busy, as before', () => {
    const r = run({ uptimeSec: 20 * MIN, lastBusyAgoSec: 70 * MIN })
    expect(r.out).toContain('busy: up 1200s')
    expect(r.stopRequested).toBe(false)
  })

  it('tmux running but failing to answer is busy: cannot evaluate is never idle', () => {
    const r = run({ tmux: 'error', lastBusyAgoSec: 70 * MIN })
    expect(r.out).toContain('busy: tmux cannot be queried')
    expect(r.stopRequested).toBe(false)
  })

  it('no tmux server leaves the decision to the other rules, as before', () => {
    const busy = run({
      tmux: 'noserver',
      ps: [...IDLE_PS, '99 12 sha256sum sha256sum -c manifest'],
      lastBusyAgoSec: 70 * MIN,
    })
    expect(busy.out).toContain('busy: job running: 99 sha256sum')
    const idle = run({ tmux: 'noserver', lastBusyAgoSec: 70 * MIN })
    expect(idle.out).toContain('idle 70m; requesting stop')
    expect(idle.stopRequested).toBe(true)
  })

  it('writes stop-requested only after a full IDLE_MINUTES of idle runs', () => {
    const quiet = { windows: [`main:0 ${now() - 300 * MIN}`] }
    expect(run({ ...quiet, lastBusyAgoSec: 50 * MIN }).stopRequested).toBe(false)
    expect(run({ ...quiet, lastBusyAgoSec: 59 * MIN + 30 }).stopRequested).toBe(false)
    const r = run({ ...quiet, lastBusyAgoSec: 60 * MIN + 5 })
    expect(r.out).toContain('idle 60m; requesting stop')
    expect(r.stopRequested).toBe(true)
  })
})
