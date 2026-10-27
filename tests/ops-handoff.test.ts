/**
 * Tests for operator/ops-machine/laptop/ops-handoff, run against a stub `fly` so
 * no workspace is touched. What it pins is the 2026-10-22 channel fix: each
 * handoff prints the name of the workspace session that will take it
 * (ops-<slug>-<id>, the id also in the task file's name, which ops-start reads),
 * so the laptop can open the conversation (the reverse fails: a laptop session's
 * name exists only on that laptop), and a handoff to a workspace with a session
 * already running opens at once in a new tmux window instead of waiting in the
 * inbox for the next `ops`.
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
  *"sh -s"*) cat > "${home}/say.sh"; echo "\${SAY_RESULT:-1}" ;;
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
  it('prints the workspace session name, and that name carries the task file id', () => {
    const out = handoff(['ap', 'check the facilities'])
    const name = out.match(/Its session is ops-ashton-price-(\d+)\./)
    expect(name).not.toBeNull()
    // ops-start derives its --name from the id in the file name; the two must agree.
    expect(fs.readdirSync(inbox)).toEqual([
      expect.stringMatching(new RegExp(`^pending-\\d{8}T\\d{6}-${name?.[1]}\\.md$`)),
    ])
    expect(tasks()[0]).toContain('check the facilities')
  })

  it('names the workspace session what ops-handoff printed (ops-start reads the same id)', () => {
    const out = handoff(['ap', 'check the facilities'])
    const printed = out.match(/Its session is (ops-ashton-price-\d+)\./)?.[1]
    const [file] = fs.readdirSync(inbox)
    // Run ops-start's own naming lines against this handoff's file, as the workspace would.
    const start = fs.readFileSync(
      path.join(process.cwd(), 'operator', 'ops-machine', 'bin', 'ops-start'),
      'utf8'
    )
    const lines = start.split('\n').filter((l) => /^\s*(id=|name="ops-\$slug-)/.test(l))
    expect(lines).toHaveLength(2)
    const named = execFileSync(
      'bash',
      [
        '-c',
        `slug=ashton-price; task="$1"; ${lines.join('; ')}; echo "$name"`,
        '_',
        `/x/inbox/${file}`,
      ],
      { encoding: 'utf8' }
    ).trim()
    expect(named).toBe(printed)
  })

  it('refuses the retired --reply-to flag rather than sending it as the task', () => {
    expect(() => handoff(['ap', 'task', '--reply-to', 'ss-console-03'])).toThrow()
    expect(tasks()).toEqual([])
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

  describe('--say (the laptop-to-workspace channel, 2026-10-27)', () => {
    // SendMessage over Remote Control never reached a workspace session; the
    // laptop now types into the session's window and reads it back by a tag.
    const say = (env: Record<string, string> = {}, text = 'hold the filing') =>
      handoff(['ap', '--say', 'ops-ashton-price-61703', text], env)

    it('types the message into the window running that session name, then submits it', () => {
      expect(say()).toContain('delivered to ops-ashton-price-61703')
      const script = fs.readFileSync(path.join(home, 'say.sh'), 'utf8')
      expect(script).toContain("s='ops-ashton-price-61703'")
      expect(script).toContain('grep -q -- "--name $s "')
      expect(script).toMatch(/send-keys -t "main:\$w" -l "\$m".*send-keys -t "main:\$w" Enter/)
      // The message travels base64-encoded and carries the read-back tag.
      const b64 = script.match(/printf %s '([A-Za-z0-9+/=]+)'/)?.[1] ?? ''
      const msg = Buffer.from(b64, 'base64').toString('utf8')
      expect(msg).toMatch(/^\[from the laptop, msg \d{6}-\d+\] hold the filing/)
    })

    it('flattens newlines so a multi-line message is one submission', () => {
      say({}, 'line one\nline two')
      const script = fs.readFileSync(path.join(home, 'say.sh'), 'utf8')
      const b64 = script.match(/printf %s '([A-Za-z0-9+/=]+)'/)?.[1] ?? ''
      expect(Buffer.from(b64, 'base64').toString('utf8')).not.toContain('\n')
    })

    it('fails, and says nothing was sent, when no window runs that session', () => {
      expect(() => say({ SAY_RESULT: 'none' })).toThrow(/nothing was sent/)
    })

    it('fails when the message cannot be read back on the screen', () => {
      expect(() => say({ SAY_RESULT: '0' })).toThrow(/not delivered/)
    })

    it('refuses a session name that is not an ops session, sending nothing', () => {
      expect(() => handoff(['ap', '--say', "x'; rm -rf ~; '", 'hi'])).toThrow()
      expect(fs.existsSync(path.join(home, 'say.sh'))).toBe(false)
    })

    it('a fresh handoff tells the laptop agent to use --say, not SendMessage', () => {
      const out = handoff(['ap', 'check the facilities'])
      expect(out).toMatch(/ops-handoff ap --say ops-ashton-price-\d+/)
      expect(out).not.toContain('SendMessage')
    })
  })

  it('refuses an unknown flag rather than sending it as the task', () => {
    expect(() => handoff(['ap', 'task', '--reply', 'x'])).toThrow()
    expect(tasks()).toEqual([])
  })
})
