/**
 * ops-task-nudge (cron, every 5 minutes on a client ops workspace) tells a
 * handed-off task's Claude session that stopped at its prompt with no outcome
 * to carry on with its task.
 *
 * On 2026-11-24 a task session on the A&P workspace answered a message typed
 * into it with `ops-handoff --say`, ended its turn and sat idle, twice on one
 * matter, about 75 minutes on an attorney's last open request. These tests run
 * the real script against a fake HOME, a fake tmux (panes, typing, screen), a
 * fake ps (what runs in each pane) and a fake curl (the healthchecks.io page),
 * and read what was typed, logged and paged.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, 'operator', 'ops-machine', 'bin', 'ops-task-nudge')

const homes: string[] = []
afterEach(() => {
  for (const h of homes.splice(0)) rmSync(h, { recursive: true, force: true })
})

const now = Math.floor(Date.now() / 1000)
const MIN = 60

interface Pane {
  pid: number
  win: number
  idleMin: number
  /** a task file in ~/inbox (a handed-off task); default true */
  task?: boolean
  /** an outbox file: the task's outcome, or its hold; default none */
  outcome?: string
  /** Claude runs in the pane; default true */
  claude?: boolean
  /** the bottom of the pane's screen */
  screen?: string
}

function workspace(panes: Pane[]): string {
  const home = mkdtempSync(join(tmpdir(), 'ops-nudge-'))
  homes.push(home)
  for (const d of ['inbox', 'outbox', 'bin', 'state']) mkdirSync(join(home, d))
  const lines: string[] = []
  for (const p of panes) {
    lines.push(`${p.pid} ${p.win} ${now - p.idleMin * MIN}`)
    if (p.task !== false) {
      writeFileSync(join(home, 'inbox', `task-20261124T150000-${p.pid}.md`), 'the task\n')
    }
    if (p.outcome !== undefined) {
      writeFileSync(join(home, 'outbox', `task-20261124T150000-${p.pid}.md`), p.outcome)
    }
    if (p.claude !== false) {
      writeFileSync(
        join(home, `ps-${p.pid}`),
        `/bin/bash /opt/ops/bin/claude --name ops-ashton-price-${p.pid} --remote-control x\n`
      )
    }
    writeFileSync(join(home, `screen-${p.win}`), p.screen ?? '> \n  ? for shortcuts\n')
  }
  writeFileSync(join(home, 'panes'), lines.join('\n') + '\n')
  writeFileSync(
    join(home, 'bin', 'tmux'),
    `#!/bin/bash
H="${home}"
case "$1" in
  list-panes) cat "$H/panes" ;;
  send-keys)
    w=\${3#main:}
    if [ "$4" = -l ]; then printf '%s\\n' "$5" >> "$H/typed-$w"; else echo "$4" >> "$H/keys-$w"; fi ;;
  capture-pane)
    w=$(echo "$*" | grep -o 'main:[0-9]*' | cut -d: -f2)
    cat "$H/screen-$w"
    [ "\${NUDGE_LOST:-0}" = 1 ] || cat "$H/typed-$w" 2>/dev/null ;;
esac
exit 0
`,
    { mode: 0o755 }
  )
  writeFileSync(
    join(home, 'bin', 'ps'),
    `#!/bin/bash
cat "${home}/ps-$4" 2>/dev/null
exit 0
`,
    { mode: 0o755 }
  )
  writeFileSync(
    join(home, 'bin', 'curl'),
    `#!/bin/bash
echo "$*" >> "${home}/curl.log"
exit 0
`,
    { mode: 0o755 }
  )
  writeFileSync(join(home, '.ops-secrets'), "export OPS_HC_URL='https://hc.example/ping/x'\n")
  return home
}

function run(home: string, env: Record<string, string> = {}): string {
  return execFileSync('bash', [SCRIPT], {
    env: {
      PATH: `${join(home, 'bin')}:${process.env.PATH}`,
      HOME: home,
      OPS_NUDGE_PAUSE: '0',
      OPS_NUDGE_KEY_PAUSE: '0',
      ...env,
    },
    encoding: 'utf8',
  })
}

const typed = (home: string, win: number): string => {
  const f = join(home, `typed-${win}`)
  return existsSync(f) ? readFileSync(f, 'utf8') : ''
}

/** Nudges already typed for a task, as epochs `minutesAgo` before now. */
function priorNudges(home: string, pid: number, minutesAgo: number[]): void {
  mkdirSync(join(home, 'state', 'task-nudge'), { recursive: true })
  writeFileSync(
    join(home, 'state', 'task-nudge', `task-20261124T150000-${pid}`),
    minutesAgo.map((m) => `${now - m * MIN}\n`).join('')
  )
}

describe('ops-task-nudge', () => {
  it('nudges a task session idle at its prompt with no outcome, reads it back and logs one line', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 12 }])
    const out = run(home)
    expect(typed(home, 3)).toMatch(
      /^\[nudge \d{6}-4242\] Continue your task from where you left it until you write its outcome\.\n$/
    )
    expect(readFileSync(join(home, 'keys-3'), 'utf8')).toBe('Enter\n')
    const lines = out.trim().split('\n')
    expect(lines).toHaveLength(1)
    expect(lines[0]).toMatch(
      /nudge: ops-ashton-price-4242 \(task-20261124T150000-4242\) idle 12m with no outcome; nudge 1 of 3, read back$/
    )
  })

  it('says so when the nudge is not read back on the screen', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 12 }])
    expect(run(home, { NUDGE_LOST: '1' })).toMatch(/nudge 1 of 3, not read back$/m)
  })

  it('does not nudge a busy session (its window printed within 10 minutes)', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 4 }])
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')
  })

  it('does not nudge a task that wrote its outcome', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 90, outcome: 'verdict: filed\n' }])
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')
  })

  it('does not nudge a task that wrote its hold', () => {
    const home = workspace([
      {
        pid: 4242,
        win: 3,
        idleMin: 90,
        outcome: 'HOLD: waiting on the attorney to name the set\n',
      },
    ])
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')
  })

  it('does not nudge the plain ops session, which has no task', () => {
    const home = workspace([{ pid: 4242, win: 0, idleMin: 90, task: false }])
    expect(run(home)).toBe('')
    expect(typed(home, 0)).toBe('')
  })

  it('does not type into a window whose Claude has exited to a shell', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 90, claude: false }])
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')
  })

  it('never types into a permission question or a usage-limit message', () => {
    const home = workspace([
      { pid: 1, win: 1, idleMin: 30, screen: ' Do you want to proceed?\n ❯ 1. Yes\n   2. No\n' },
      {
        pid: 2,
        win: 2,
        idleMin: 30,
        screen: "You've hit your weekly limit · resets Nov 27, 11pm\n",
      },
    ])
    expect(run(home)).toBe('')
    expect(typed(home, 1)).toBe('')
    expect(typed(home, 2)).toBe('')
  })

  it('nudges at most once per 20 minutes per task', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 30 }])
    priorNudges(home, 4242, [12])
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')

    const later = workspace([{ pid: 4242, win: 3, idleMin: 30 }])
    priorNudges(later, 4242, [21])
    expect(run(later)).toMatch(/nudge 2 of 3, read back$/m)
  })

  it('a second run right after a nudge does not nudge again', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 12 }])
    run(home)
    expect(run(home)).toBe('')
    expect(typed(home, 3).split('\n').filter(Boolean)).toHaveLength(1)
  })

  it('after 3 nudges it stops, writes one stalled line and fails the healthchecks.io check once', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 25 }])
    priorNudges(home, 4242, [65, 45, 22])
    const out = run(home)
    expect(typed(home, 3)).toBe('')
    expect(out).toMatch(
      /task session stalled: ops-ashton-price-4242 \(task-20261124T150000-4242\) idle 25m at its prompt with no outcome after 3 nudges/
    )
    const stalls = readFileSync(join(home, 'state', 'task-stalls'), 'utf8')
    expect(stalls.trim().split('\n')).toHaveLength(1)
    expect(stalls).toContain('task session stalled: ops-ashton-price-4242')
    const curl = readFileSync(join(home, 'curl.log'), 'utf8')
    expect(curl).toContain('https://hc.example/ping/x/fail')
    expect(curl).toContain('task session stalled')

    // The next run, still stalled: no nudge, no second line, no second page.
    expect(run(home)).toBe('')
    expect(typed(home, 3)).toBe('')
    expect(readFileSync(join(home, 'state', 'task-stalls'), 'utf8')).toBe(stalls)
    expect(readFileSync(join(home, 'curl.log'), 'utf8')).toBe(curl)
  })

  it('forgets a task once it writes its outcome', () => {
    const home = workspace([{ pid: 4242, win: 3, idleMin: 25, outcome: 'verdict: done\n' }])
    priorNudges(home, 4242, [65, 45, 22])
    run(home)
    expect(existsSync(join(home, 'state', 'task-nudge', 'task-20261124T150000-4242'))).toBe(false)
  })
})
