/**
 * Tests for operator/ops-machine/laptop/ops-handoff, run against a stub `fly` so
 * no workspace is touched. What it pins is the 2026-10-22 channel fix: the
 * laptop session names itself (--reply-to) in the task file, so the workspace
 * session can message it (a task-started workspace session is renamed to an
 * auto-title and cannot be found by name), and a handoff to a workspace with a
 * session already running opens at once in a new tmux window instead of waiting
 * in the inbox for the next `ops`.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const laptop = path.join(process.cwd(), 'operator', 'ops-machine', 'laptop')

let home: string
let inbox: string
let calls: string

/** Every task file the stub workspace received, oldest first. */
function tasks(): string[] {
  return fs
    .readdirSync(inbox)
    .sort()
    .map((f) => fs.readFileSync(path.join(inbox, f), 'utf8'))
}

/**
 * A stub fly: machine list says started, ssh answers, `cat > ~/inbox/pending-<stamp>.md`
 * writes its stdin to a file, `wc -c` reports that file's size, and the tmux
 * probe answers "opened" only when TMUX_RUNNING=1.
 */
function stubFly(dir: string): void {
  const bin = path.join(dir, 'bin')
  fs.mkdirSync(bin, { recursive: true })
  fs.writeFileSync(
    path.join(bin, 'fly'),
    `#!/bin/bash
echo "$*" >> "${calls}"
case "$*" in
  *"machine list"*) echo '[{"id":"m1","state":"started"}]' ;;
  *"cat > ~/inbox/pending-"*) f=$(echo "$*" | grep -o 'pending-[^ ]*\\.md' | head -1); cat > "${inbox}/$f" ;;
  *"wc -c"*) f=$(echo "$*" | grep -o 'pending-[^ ]*\\.md' | head -1); wc -c < "${inbox}/$f" ;;
  *"tmux has-session"*) [ "\${TMUX_RUNNING:-0}" = 1 ] && echo opened ;;
  *) : ;;
esac
exit 0
`,
    { mode: 0o755 }
  )
}

/**
 * The repo stores both scripts 0644; install.sh installs them 0755 side by side,
 * and ops-handoff calls its sibling `ops`. Mirror that install.
 */
function install(dir: string): string {
  const bin = path.join(dir, 'laptop-bin')
  fs.mkdirSync(bin, { recursive: true })
  for (const name of ['ops', 'ops-handoff']) {
    fs.copyFileSync(path.join(laptop, name), path.join(bin, name))
    fs.chmodSync(path.join(bin, name), 0o755)
  }
  return path.join(bin, 'ops-handoff')
}

function handoff(args: string[], env: Record<string, string> = {}): string {
  return execFileSync(install(home), args, {
    env: {
      ...process.env,
      HOME: home,
      PATH: `${path.join(home, 'bin')}:${process.env.PATH}`,
      ...env,
    },
    encoding: 'utf8',
  })
}

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'ops-handoff-'))
  inbox = path.join(home, 'inbox')
  fs.mkdirSync(inbox)
  calls = path.join(home, 'calls.log')
  fs.mkdirSync(path.join(home, '.claude'), { recursive: true })
  fs.copyFileSync(
    path.join(process.cwd(), 'operator', 'ops-machine', 'workspaces.json'),
    path.join(home, '.claude', 'smd-ops-workspaces.json')
  )
  stubFly(home)
})

describe('ops-handoff', () => {
  it('writes the reply-to session into the task, so the workspace can message the laptop', () => {
    const out = handoff(['ap', 'check the facilities', '--reply-to', 'ss-console-03'])
    const [task] = tasks()
    expect(task).toMatch(/^Reply-to: ss-console-03$/m)
    expect(task).toContain('check the facilities')
    expect(out).toContain('It will message ss-console-03 when it starts')
  })

  it('omits the Reply-to line when no session is named', () => {
    handoff(['ap', 'check the facilities'])
    expect(tasks()[0]).not.toMatch(/Reply-to:/)
  })

  it('opens the task in a new window when a workspace session is already running', () => {
    const out = handoff(['ap', 'check the facilities'], { TMUX_RUNNING: '1' })
    expect(out).toContain('opened in a new window')
    // The window is opened on THIS handoff's file, never on whatever is waiting.
    expect(fs.readFileSync(calls, 'utf8')).toMatch(
      /tmux new-window -t main -n task \\?"\/opt\/ops\/bin\/ops-start \$HOME\/inbox\/pending-\d{8}T\d{6}-\d+\.md/
    )
  })

  it('tells the Captain to run ops when no session is running', () => {
    const out = handoff(['ap', 'check the facilities'])
    expect(out).toContain('type `ops ap`')
    expect(out).not.toContain('opened in a new window')
  })

  it('gives each handoff its own file, so two at once never share a session', () => {
    // 2026-10-22: two handoffs appended to one pending.md and the next workspace
    // session took both, a finished facilities write riding along with a
    // chronology hold.
    handoff(['ap', 'first task'])
    handoff(['ap', 'second task'])
    const all = tasks()
    expect(all).toHaveLength(2)
    expect(all.filter((t) => t.includes('first task'))).toHaveLength(1)
    expect(all.filter((t) => t.includes('second task'))).toHaveLength(1)
    expect(all.every((t) => (t.match(/## Handed over/g) ?? []).length === 1)).toBe(true)
  })

  it('refuses an unknown flag rather than sending it as the task', () => {
    expect(() => handoff(['ap', 'task', '--reply', 'x'])).toThrow()
    expect(tasks()).toEqual([])
  })
})
