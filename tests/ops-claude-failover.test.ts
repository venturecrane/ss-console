/**
 * Claude account failover on the ops machines (smd-ops and every client ops
 * workspace).
 *
 * On 2026-11-08 the A&P workspace's Claude account hit its weekly limit and
 * every window sat at Claude's limit prompt until the reset, while the
 * Captain's second account sat unused. Three pieces move a stuck session onto
 * the other account with no person typing:
 *   - bin/ops-claude-accounts: the per-Machine record of which account is
 *     exhausted until when (parses Claude's own "resets ..." text);
 *   - bin/claude (the shim): picks the first account not exhausted, keeps the
 *     tokens away from every tool, and inside tmux resumes the same
 *     conversation on the next account when the watcher asks;
 *   - bin/ops-claude-failover (cron): spots a window stuck at the limit message,
 *     marks the account, and asks for the restart, or pages once when every
 *     account is spent.
 * These tests run the real scripts against a fake HOME, a fake `tmux` and a
 * fake Claude binary. Token values here are test strings, never real ones.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const BIN = join(fileURLToPath(new URL('..', import.meta.url)), 'operator', 'ops-machine', 'bin')
const SHIM = join(BIN, 'claude')
const ACCOUNTS = join(BIN, 'ops-claude-accounts')
const WATCHER = join(BIN, 'ops-claude-failover')
const TOOL_ENV = join(BIN, 'ops-tool-env')

// Sunday 2026-11-08 10:00 in Phoenix (UTC-7, no DST).
const NOW = Date.UTC(2026, 10, 8, 17, 0, 0) / 1000
const LIMIT_SCREEN = [
  '> keep going on the chronology',
  '',
  "  ⎿  You've hit your weekly limit · resets Mon 12:00am (America/Phoenix)",
  '     /rate-limit-options',
  '  1. Stop and wait for limit to reset',
  '  2. Switch to usage credits',
].join('\n')

const homes: string[] = []
const kids: ChildProcess[] = []
afterEach(() => {
  for (const k of kids.splice(0)) k.kill()
  for (const h of homes.splice(0)) rmSync(h, { recursive: true, force: true })
})

function makeHome(): string {
  const home = mkdtempSync(join(tmpdir(), 'ops-claude-'))
  homes.push(home)
  for (const d of ['fakebin', 'state', 'logs']) mkdirSync(join(home, d), { recursive: true })
  // tmux capture-pane prints the screen the test wrote.
  writeFileSync(join(home, 'fakebin', 'tmux'), '#!/bin/sh\ncat "$HOME/screen.txt"\n')
  chmodSync(join(home, 'fakebin', 'tmux'), 0o755)
  return home
}

function baseEnv(home: string, extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  return {
    PATH: `${join(home, 'fakebin')}:${process.env.PATH ?? '/usr/bin:/bin'}`,
    HOME: home,
    TZ: 'America/Phoenix',
    OPS_CLAUDE_NOW: String(NOW),
    ...extra,
  }
}

function run(cmd: string, args: string[], env: NodeJS.ProcessEnv): string {
  return execFileSync(cmd, args, { env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
}

function accounts(home: string, ...args: string[]): string {
  return run('python3', [ACCOUNTS, ...args], baseEnv(home)).trim()
}

function stateOf(home: string): Record<string, { exhausted_until: number }> {
  return JSON.parse(readFileSync(join(home, 'state', 'claude-accounts.json'), 'utf8')).accounts
}

describe('ops-claude-accounts: reading the reset out of Claude’s limit message', () => {
  const at = (text: string) => Number(accounts(makeHome(), 'parse', text))
  it('a weekday reset is the coming one', () => {
    expect(at("You've hit your weekly limit · resets Mon 12:00am")).toBe(
      Date.UTC(2026, 10, 9, 7, 0) / 1000
    )
  })
  it('a time later today is today, an earlier one is tomorrow', () => {
    expect(at("You've hit your session limit · resets 3:45pm")).toBe(
      Date.UTC(2026, 10, 8, 22, 45) / 1000
    )
    expect(at("You've hit your session limit · resets 9am")).toBe(
      Date.UTC(2026, 10, 9, 16, 0) / 1000
    )
  })
  it('a dated reset with a named zone', () => {
    expect(at('resets Nov 12, 5pm (America/Phoenix)')).toBe(Date.UTC(2026, 10, 13, 0, 0) / 1000)
  })
  it('an unreadable reset is retried in an hour, not trusted', () => {
    const home = makeHome()
    expect(accounts(home, 'mark', 'B', "You've hit your weekly limit")).toContain('retry in 1h')
    expect(stateOf(home).B.exhausted_until).toBe(NOW + 3600)
  })
})

describe('the claude shim', () => {
  function shim(home: string, args: string[], extra: Record<string, string> = {}): string {
    return run(
      'bash',
      [SHIM, ...args],
      baseEnv(home, { OPS_CLAUDE_SHIM_DRY: '1', ...extra })
    ).trim()
  }
  const TOKENS = { CLAUDE_OAUTH_TOKEN_A: 'test-token-a', CLAUDE_OAUTH_TOKEN_B: 'test-token-b' }

  it('with no token behaves exactly as before: the stored login, exec', () => {
    const out = shim(makeHome(), ['--name', 'ops-x', '--remote-control', 'ops-x'])
    expect(out).toBe(
      'account=login mode=exec env_file=none args=--name ops-x --remote-control ops-x'
    )
  })

  it('prefers B, falls to A when B is exhausted, and never prints a token', () => {
    const home = makeHome()
    expect(shim(home, ['-p', 'hi'], TOKENS)).toMatch(/^account=B /)
    accounts(home, 'mark', 'B', "You've hit your weekly limit · resets Mon 12:00am")
    const out = shim(home, ['-p', 'hi'], TOKENS)
    expect(out).toMatch(/^account=A /)
    expect(out).toContain('token_var=CLAUDE_CODE_OAUTH_TOKEN')
    expect(out).not.toContain('test-token')
  })

  it('reads the tokens from ~/.claude-oauth on a workspace', () => {
    const home = makeHome()
    writeFileSync(join(home, '.claude-oauth'), "export CLAUDE_OAUTH_TOKEN_A='test-token-a'\n")
    expect(shim(home, ['-p', 'hi'])).toMatch(/^account=A /)
  })

  it('a token session drops Remote Control and gets a known session id', () => {
    const out = shim(
      makeHome(),
      ['--name', 'ops-x-1', '--remote-control', 'ops-x-1', 'do the task'],
      TOKENS
    )
    expect(out).toMatch(/args=--session-id [0-9a-f-]{36} --name ops-x-1 do the task$/)
    expect(out).toContain('env_file=' + TOOL_ENV)
  })

  it('in tmux, resumes the SAME conversation on the other account when the watcher asks', () => {
    const home = makeHome()
    const log = join(home, 'launches')
    // The fake Claude records which token and arguments it got, and what a tool
    // would inherit. On its first run it plays the watcher: marks its account
    // exhausted and asks for the restart.
    writeFileSync(
      join(home, 'fakebin', 'real-claude'),
      `#!/bin/bash
echo "token=$CLAUDE_CODE_OAUTH_TOKEN envfile=$CLAUDE_ENV_FILE a=\${CLAUDE_OAUTH_TOKEN_A:-unset} api=\${ANTHROPIC_API_KEY:-unset} args=$*" >> "${log}"
if [ ! -f "$HOME/once" ]; then
  touch "$HOME/once"
  python3 "${ACCOUNTS}" mark B "You've hit your weekly limit · resets Mon 12:00am" >/dev/null
  : > "$HOME/state/claude-panes/pane-7.restart"
  exit 143
fi
exit 0
`
    )
    chmodSync(join(home, 'fakebin', 'real-claude'), 0o755)
    run(
      'bash',
      [SHIM, '--name', 'ops-x-1', '--remote-control', 'ops-x-1', 'do the task'],
      baseEnv(home, {
        ...TOKENS,
        ANTHROPIC_API_KEY: 'test-client-key',
        TMUX_PANE: '%7',
        OPS_CLAUDE_BIN: join(home, 'fakebin', 'real-claude'),
      })
    )
    const [first, second, third] = readFileSync(log, 'utf8').trim().split('\n')
    expect(third).toBeUndefined()
    expect(first).toMatch(/^token=test-token-b /)
    expect(second).toMatch(/^token=test-token-a /)
    const sid = first.match(/--session-id ([0-9a-f-]{36})/)![1]
    expect(second).toContain(`--resume ${sid}`)
    expect(second).toContain('--name ops-x-1')
    expect(second).toContain('Continue the task exactly where you left off')
    expect(second).not.toContain('do the task')
    expect(second).not.toContain('--remote-control')
    for (const line of [first, second]) {
      expect(line).toContain(`envfile=${TOOL_ENV}`)
      expect(line).toContain('a=unset')
      expect(line).toContain('api=unset')
    }
    // The pane's registration is cleaned up when the session ends.
    expect(existsSync(join(home, 'state', 'claude-panes', 'pane-7'))).toBe(false)
  })
})

describe('ops-tool-env: what every tool command sources', () => {
  it('keeps the workspace secrets and removes every Claude account token', () => {
    const home = makeHome()
    writeFileSync(join(home, '.ops-secrets'), "export ANTHROPIC_API_KEY='test-client-key'\n")
    const out = run(
      'bash',
      ['-c', `. "${TOOL_ENV}"; env | cut -d= -f1 | sort`],
      baseEnv(home, {
        CLAUDE_CODE_OAUTH_TOKEN: 'x',
        CLAUDE_OAUTH_TOKEN_A: 'x',
        CLAUDE_OAUTH_TOKEN_B: 'x',
      })
    )
    expect(out).toContain('ANTHROPIC_API_KEY')
    expect(out).not.toMatch(/OAUTH_TOKEN/)
  })
})

describe('ops-claude-failover: the watcher', () => {
  function register(home: string, account: string): void {
    const sleeper = spawn('sleep', ['60'])
    kids.push(sleeper)
    mkdirSync(join(home, 'state', 'claude-panes'), { recursive: true })
    writeFileSync(
      join(home, 'state', 'claude-panes', 'pane-7'),
      `pid=${sleeper.pid}\npane=%7\naccount=${account}\nsid=abc\naccounts=B A\nstarted=${NOW}\n`
    )
  }
  const watch = (home: string) => run('bash', [WATCHER], baseEnv(home, { OPS_FAILOVER_DRY: '1' }))

  it('moves a window stuck at the limit to the other account, after confirming it is stuck', () => {
    const home = makeHome()
    register(home, 'B')
    writeFileSync(join(home, 'screen.txt'), LIMIT_SCREEN)
    const first = watch(home)
    expect(first).toContain('confirming next run')
    expect(first).not.toContain('would: restart')
    const second = watch(home)
    expect(second).toContain('would: restart pane %7 on A')
    expect(stateOf(home).B.exhausted_until).toBe(Date.UTC(2026, 10, 9, 7, 0) / 1000)
  })

  it('leaves a working session alone even when it prints the words', () => {
    const home = makeHome()
    register(home, 'B')
    writeFileSync(join(home, 'screen.txt'), LIMIT_SCREEN + '\nworking 1')
    watch(home)
    writeFileSync(join(home, 'screen.txt'), LIMIT_SCREEN + '\nworking 2')
    expect(watch(home)).not.toContain('would: restart')
    expect(existsSync(join(home, 'state', 'claude-accounts.json'))).toBe(false)
  })

  it('ignores limits that are not the account’s (spend, fast mode)', () => {
    const home = makeHome()
    register(home, 'B')
    writeFileSync(join(home, 'screen.txt'), "You've hit your monthly spend limit · resets Dec 1")
    watch(home)
    expect(watch(home)).toBe('')
  })

  it('with every account spent, pages once and does not restart', () => {
    const home = makeHome()
    register(home, 'B')
    accounts(home, 'mark', 'A', "You've hit your session limit · resets 3:45pm")
    writeFileSync(join(home, 'screen.txt'), LIMIT_SCREEN)
    watch(home)
    const second = watch(home)
    expect(second).toContain(
      'would page: every Claude account on this Machine is at its usage limit'
    )
    expect(second).toContain('A resets Sun 2026-11-08 15:45')
    expect(second).toContain('B resets Mon 2026-11-09 00:00')
    expect(second).not.toContain('would: restart')
    expect(watch(home)).not.toContain('would page')
  })

  it('does nothing for a session on the stored login (no tokens on this Machine)', () => {
    const home = makeHome()
    register(home, 'login')
    writeFileSync(join(home, 'screen.txt'), LIMIT_SCREEN)
    watch(home)
    expect(watch(home)).toBe('')
  })
})
