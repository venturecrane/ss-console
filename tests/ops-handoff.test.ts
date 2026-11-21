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
 * writes its stdin to a file, `wc -c` reports that file's size, the tmux
 * step answers "opened" unless TMUX_FAIL=1 (tmux itself cannot start), and the
 * process-list read answers PS_RESULT sessions by that name (default 1).
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
  *"tmux has-session"*) [ "\${TMUX_FAIL:-0}" = 1 ] || echo opened ;;
  *"ps -eo args"*) echo "\${PS_RESULT:-1}" ;;
  *"sh -s"*) cat > "${home}/say.sh"
    if [ "\${REMOTE_EXEC:-0}" = 1 ]; then HOME="${home}/ws" PATH="${home}/remotebin:$PATH" sh "${home}/say.sh"
    else echo "\${SAY_RESULT-1}"; fi ;;
  *) : ;;
esac
exit 0
`,
    { mode: 0o755 }
  )
}

/**
 * The workspace side, for tests that run the script ops-handoff sends over
 * `sh -s` (REMOTE_EXEC=1): a fake tmux with one window whose ps shows the
 * session (none with NO_SESSION=1), that records what was typed (or fails the
 * typing with SENDKEYS_FAIL=1) and prints it back as the pane, keeping only the
 * last -S lines as tmux does. As Claude Code does, the pane shows typed text
 * over 200 characters collapsed to "[Pasted text #1 +N lines]", and word-wraps
 * the rest at 25 columns with an indent, which splits the tag mid-id.
 * PANE_MODE=lost prints a screen the message never reached; PANE_MODE=scroll
 * prints it and then SCROLL_LINES lines of a busy session's output.
 */
function stubWorkspace(dir: string): void {
  const bin = path.join(dir, 'remotebin')
  fs.mkdirSync(bin, { recursive: true })
  fs.mkdirSync(path.join(dir, 'ws', 'outbox'), { recursive: true })
  fs.writeFileSync(
    path.join(bin, 'tmux'),
    `#!/bin/bash
case "$1" in
  list-panes) echo "1:4242" ;;
  send-keys) [ "\${SENDKEYS_FAIL:-0}" = 1 ] && exit 1
    [ "$4" = -l ] && printf '%s' "$5" > "$HOME/typed" ;;
  capture-pane)
    keep=$(echo "$*" | grep -o -- '-S -[0-9]*' | grep -o '[0-9]*$')
    {
      echo "> an earlier prompt"
      if [ "\${PANE_MODE:-wrap}" != lost ]; then
        if [ "$(wc -c < "$HOME/typed")" -gt 200 ]; then echo "> [Pasted text #1 +37 lines]"
        else fold -w 25 "$HOME/typed" | sed '2,$s/^/  /'; echo; fi
      fi
      [ "\${PANE_MODE:-wrap}" = scroll ] && seq 1 "\${SCROLL_LINES:-500}" | sed 's/^/  working on step /'
      echo "  working..."
    } | tail -n "\${keep:-50}" ;;
esac
exit 0
`,
    { mode: 0o755 }
  )
  fs.writeFileSync(
    path.join(bin, 'ps'),
    '#!/bin/sh\n[ "${NO_SESSION:-0}" = 1 ] && echo "claude --name ops-other-1 x" || echo "claude --name ops-ashton-price-61703 --resume x"\n',
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
      OPS_HANDOFF_SLEEP: '0',
      OPS_HANDOFF_WAIT: '2',
      OPS_HANDOFF_SAY_PAUSE: '0',
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
  stubWorkspace(home)
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
    const out = handoff(['ap', 'check the facilities'])
    expect(out).toContain('running there')
    // The window is opened on THIS handoff's file, never on whatever is waiting.
    expect(fs.readFileSync(calls, 'utf8')).toMatch(
      /tmux new-window -t main -n task \\?"\/opt\/ops\/bin\/ops-start \$HOME\/inbox\/pending-\d{8}T\d{6}-\d+\.md/
    )
  })

  it('starts the session itself when none is running (after a restart), on this task', () => {
    const out = handoff(['ap', 'check the facilities'])
    expect(out).toContain('running there')
    expect(fs.readFileSync(calls, 'utf8')).toMatch(
      /else tmux new-session -d -s main -n task \\?"\/opt\/ops\/bin\/ops-start \$HOME\/inbox\/pending-\d{8}T\d{6}-\d+\.md/
    )
  })

  // 2026-11-10: the script used to exit 0 with "Handed over ... tell the Captain
  // to type `ops`" when nothing had started, and a laptop session waited 16
  // minutes on a task no one was doing. "Handed over" now means a session by
  // that name is in the workspace's process list; anything else is exit 1.
  const failure = (env: Record<string, string>): { status: number; stderr: string } => {
    try {
      handoff(['ap', 'check the facilities'], env)
    } catch (e) {
      const err = e as { status: number; stderr: string }
      return { status: err.status, stderr: err.stderr }
    }
    return { status: 0, stderr: '' }
  }

  it('fails, and says nothing is working on the task, when tmux cannot start', () => {
    const got = failure({ TMUX_FAIL: '1' })
    expect(got.status).toBe(1)
    expect(got.stderr).toContain('HANDOFF NOT RUNNING')
    expect(got.stderr).toContain('Do not wait for a result')
  })

  it('fails when the window opened but no session by that name is running', () => {
    const got = failure({ PS_RESULT: '0' })
    expect(got.status).toBe(1)
    expect(got.stderr).toMatch(/no session named ops-ashton-price-\d+ started/)
  })

  it('asks the workspace process list for exactly the session name it printed', () => {
    const out = handoff(['ap', 'check the facilities'])
    const name = out.match(/Its session is (ops-ashton-price-\d+)\./)?.[1]
    expect(fs.readFileSync(calls, 'utf8')).toContain(`grep -c "[c]laude --name ${name} "`)
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

    it('writes the message to a file and types one short line naming it and the id', () => {
      expect(say()).toContain('delivered to ops-ashton-price-61703')
      const script = fs.readFileSync(path.join(home, 'say.sh'), 'utf8')
      expect(script).toContain("s='ops-ashton-price-61703'")
      expect(script).toContain('grep -q -- "--name $s "')
      expect(script).toMatch(
        /send-keys -t "main:\$w" -l "\$line"[\s\S]*send-keys -t "main:\$w" Enter/
      )
      expect(script).toContain('line="[from the laptop, msg $id] Read ~/inbox/say-$id.md and act')
      // The message travels base64-encoded on stdin, never typed and never in argv.
      const b64 = script.match(/printf %s '([A-Za-z0-9+/=]+)'/)?.[1] ?? ''
      expect(Buffer.from(b64, 'base64').toString('utf8')).toBe('hold the filing\n')
    })

    it('keeps a multi-line message whole in the file and types a single line', () => {
      const got = handoff(['ap', '--say', 'ops-ashton-price-61703', 'line one\nline two'], {
        REMOTE_EXEC: '1',
      })
      const id = got.match(/message (\d{6}-\d+) read back/)?.[1] ?? ''
      const ws = path.join(home, 'ws')
      expect(fs.readFileSync(path.join(ws, 'inbox', `say-${id}.md`), 'utf8')).toBe(
        'line one\nline two\n'
      )
      const typed = fs.readFileSync(path.join(ws, 'typed'), 'utf8')
      expect(typed).not.toContain('\n')
      expect(typed).not.toContain('line one')
      // One short line, so no UI collapses it and the id stays readable.
      expect(typed).toBe(
        `[from the laptop, msg ${id}] Read ~/inbox/say-${id}.md and act on it as if typed here.`
      )
      expect(typed.length).toBeLessThan(120)
    })

    it('fails, and says nothing was sent, when no window runs that session', () => {
      expect(() => say({ SAY_RESULT: 'none' })).toThrow(/nothing was sent/)
    })

    it('an id not read back on the screen is delivery unknown, never not delivered', () => {
      expect(() => say({ SAY_RESULT: '0' })).toThrow(/delivery unknown/)
      expect(() => say({ SAY_RESULT: '0' })).not.toThrow(/not delivered|lost/)
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

  // 2026-11-19: `ops-handoff <client> --help` started a real task whose text
  // was "--help".
  it('--help and -h print the usage and start no task', () => {
    for (const flag of ['--help', '-h']) {
      expect(handoff(['ap', flag])).toContain('usage: ops-handoff')
      expect(handoff([flag])).toContain('usage: ops-handoff')
    }
    expect(tasks()).toEqual([])
    expect(fs.existsSync(calls)).toBe(false)
  })

  it('an unknown flag in the task position prints the usage, exits 2 and starts nothing', () => {
    try {
      handoff(['ap', '--verbose'])
      expect.unreachable('should have failed')
    } catch (e) {
      const err = e as { status: number; stderr: string }
      expect(err.status).toBe(2)
      expect(err.stderr).toContain('unknown flag --verbose')
      expect(err.stderr).toContain('usage: ops-handoff')
    }
    expect(tasks()).toEqual([])
    expect(fs.existsSync(calls)).toBe(false)
  })

  describe('progress lines', () => {
    it('every task carries the standing instruction to write PROGRESS: lines', () => {
      handoff(['ap', 'check the facilities'])
      const [task] = tasks()
      expect(task).toContain('check the facilities')
      expect(task).toContain('## Progress (standing instruction from ops-handoff)')
      expect(task).toMatch(/Each line starts with PROGRESS: and gives the stage name/)
      expect(task).toContain('.progress instead of .md')
      expect(task).toContain('labelled as an estimate')
    })

    it('--results prints the newest progress line of a task still running, not of a finished one', () => {
      const outbox = path.join(home, 'ws', 'outbox')
      fs.writeFileSync(path.join(outbox, 'task-done-1.md'), 'verdict: done\n')
      fs.writeFileSync(
        path.join(outbox, 'task-done-1.progress'),
        'PROGRESS: old stage, estimate 5 minutes\n'
      )
      const past = new Date(Date.now() - 60_000)
      fs.utimesSync(path.join(outbox, 'task-done-1.progress'), past, past)
      fs.writeFileSync(
        path.join(outbox, 'task-live-2.progress'),
        'PROGRESS: reading files, 3 of 40 done, estimate 30 minutes\n' +
          'a stray note\n' +
          'PROGRESS: drafting, 12 of 40 done, estimate 10 minutes\n'
      )
      const out = handoff(['ap', '--results'], { REMOTE_EXEC: '1' })
      expect(out).toContain('== task-done-1.md')
      expect(out).toContain('verdict: done')
      expect(out).toContain(
        '== still running task-live-2: PROGRESS: drafting, 12 of 40 done, estimate 10 minutes'
      )
      expect(out).not.toContain('3 of 40')
      expect(out).not.toContain('old stage')
    })
  })

  describe('--say read-back (2026-11-19)', () => {
    const sayStatus = (env: Record<string, string>) => {
      try {
        return {
          status: 0,
          out: handoff(['ap', '--say', 'ops-ashton-price-61703', 'hold the filing'], env),
          err: '',
        }
      } catch (e) {
        const err = e as { status: number; stdout: string; stderr: string }
        return { status: err.status, out: err.stdout, err: err.stderr }
      }
    }

    it('reports delivered when the pane word-wraps the tag mid-id', () => {
      const got = sayStatus({ REMOTE_EXEC: '1' })
      expect(got.status).toBe(0)
      expect(got.out).toContain('delivered to ops-ashton-price-61703')
      // The pane really did split the tag: no single screen line holds "msg <id>".
      const id = got.out.match(/message (\d{6}-\d+) read back/)?.[1] ?? ''
      expect(id).not.toBe('')
      const pane = execFileSync(path.join(home, 'remotebin', 'tmux'), ['capture-pane'], {
        env: { ...process.env, HOME: path.join(home, 'ws') },
        encoding: 'utf8',
      })
      expect(pane.split('\n').some((l) => l.includes(`msg ${id}`))).toBe(false)
      expect(pane.replace(/\s+/g, '')).toContain(`msg${id}]`)
    })

    // 2026-11-20: a long multi-line message was reported "lost, send it again"
    // twice although the session had acted on it, because Claude Code showed
    // the paste collapsed and the id at its head never reached the screen.
    it('reports a long multi-line message delivered although the UI collapses long pastes', () => {
      const long = Array.from({ length: 37 }, (_, i) => `step ${i + 1}: run the next live step`)
      let out: string
      try {
        out = handoff(['ap', '--say', 'ops-ashton-price-61703', long.join('\n')], {
          REMOTE_EXEC: '1',
        })
      } catch (e) {
        out = `FAILED ${(e as { stderr: string }).stderr}`
      }
      expect(out).toContain('delivered to ops-ashton-price-61703')
      const id = out.match(/message (\d{6}-\d+) read back/)?.[1] ?? ''
      const inboxFile = path.join(home, 'ws', 'inbox', `say-${id}.md`)
      expect(fs.readFileSync(inboxFile, 'utf8')).toBe(`${long.join('\n')}\n`)
    })

    it('never says lost when the message landed and a busy session scrolled it away', () => {
      const got = sayStatus({
        REMOTE_EXEC: '1',
        PANE_MODE: 'scroll',
        SCROLL_LINES: '2500',
        OPS_HANDOFF_SAY_TRIES: '2',
      })
      expect(got.status).toBe(3)
      expect(got.err).toContain('delivery unknown')
      expect(got.err).toContain('before sending again')
      expect(got.err).not.toMatch(/lost|not delivered/)
    })

    it('reads back an id that 500 lines of output pushed past the old 400-line window', () => {
      const got = sayStatus({ REMOTE_EXEC: '1', PANE_MODE: 'scroll', SCROLL_LINES: '500' })
      expect(got.status).toBe(0)
      expect(got.out).toContain('delivered to ops-ashton-price-61703')
    })

    it('says delivery unknown, exit 3, never lost, when the id is nowhere on the screen', () => {
      const got = sayStatus({ REMOTE_EXEC: '1', PANE_MODE: 'lost', OPS_HANDOFF_SAY_TRIES: '2' })
      expect(got.status).toBe(3)
      expect(got.err).toContain('delivery unknown')
      expect(got.err).not.toMatch(/lost|not delivered/)
    })

    it('says not delivered, exit 1, when no running session has that name', () => {
      const got = sayStatus({ REMOTE_EXEC: '1', NO_SESSION: '1' })
      expect(got.status).toBe(1)
      expect(got.err).toMatch(/^not delivered: no running workspace session named/)
      expect(fs.existsSync(path.join(home, 'ws', 'typed'))).toBe(false)
    })

    it('says not delivered, exit 1, when typing into the session fails', () => {
      const got = sayStatus({ REMOTE_EXEC: '1', SENDKEYS_FAIL: '1' })
      expect(got.status).toBe(1)
      expect(got.err).toMatch(/^not delivered: .*typing into ops-ashton-price-61703 failed/)
    })

    it('says delivery unknown, exit 3, when no screen comes back', () => {
      const got = sayStatus({ SAY_RESULT: '' })
      expect(got.status).toBe(3)
      expect(got.err).toContain('delivery unknown')
    })
  })
})
