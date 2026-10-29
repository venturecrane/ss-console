/**
 * ops-reap-tasks closes a handed-off task's Claude session only when it is
 * finished: its outbox file exists AND its window has been quiet long enough.
 *
 * On 2026-10-29 the A&P ops workspace held 15 sessions from earlier handoffs
 * (the Machine never idles, because the Captain's laptop stays attached), ran
 * out of memory, and starved a new task. These tests run the real script in
 * dry mode against a fake HOME and a fake pane list, and read its decisions.
 * The cases that matter most are the ones it must NOT close: a session still
 * being talked to, and a task that has not reported yet.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(REPO_ROOT, 'operator', 'ops-machine', 'bin', 'ops-reap-tasks')

const homes: string[] = []
afterEach(() => {
  for (const h of homes.splice(0)) rmSync(h, { recursive: true, force: true })
})

const now = Math.floor(Date.now() / 1000)
const HOUR = 3600

function run(panes: string[], outboxPids: string[], memMb: number): string {
  const home = mkdtempSync(join(tmpdir(), 'ops-reap-'))
  homes.push(home)
  mkdirSync(join(home, 'outbox'))
  for (const pid of outboxPids) {
    writeFileSync(join(home, 'outbox', `task-20261029T120000-${pid}.md`), 'done\n')
  }
  const paneFile = join(home, 'panes')
  writeFileSync(paneFile, panes.join('\n') + '\n')
  const env: NodeJS.ProcessEnv = {
    PATH: process.env.PATH,
    HOME: home,
    OPS_REAP_PANES: paneFile,
    OPS_REAP_MEM_MB: String(memMb),
    OPS_REAP_DRY: '1',
  }
  return execFileSync('bash', [SCRIPT], { env, encoding: 'utf8' })
}

describe('ops-reap-tasks', () => {
  it('closes a reported task whose window has been quiet past the period', () => {
    const out = run([`101 3 ${now - 13 * HOUR}`], ['101'], 3000)
    expect(out).toContain('would: tmux kill-window -t main:3')
  })

  it('keeps a reported task the Captain is still talking to', () => {
    const out = run([`102 4 ${now - 2 * HOUR}`], ['102'], 3000)
    expect(out).not.toContain('kill-window')
  })

  it('never closes a task that has not written its outbox, however old', () => {
    const out = run([`103 5 ${now - 6 * 24 * HOUR}`], [], 3000)
    expect(out).not.toContain('kill-window')
  })

  it('never closes the plain session: no outbox file carries its pid', () => {
    const out = run([`104 1 ${now - 6 * 24 * HOUR}`, `105 6 ${now - 13 * HOUR}`], ['105'], 3000)
    expect(out).not.toContain('main:1')
    expect(out).toContain('main:6')
  })

  it('under memory pressure closes reported tasks quiet for 30 minutes, oldest first', () => {
    const out = run(
      [`201 7 ${now - 40 * 60}`, `202 8 ${now - 3 * HOUR}`, `203 9 ${now - 10 * 60}`],
      ['201', '202', '203'],
      300
    )
    expect(out).toContain('quiet period 30m')
    const lines = out.split('\n').filter((l) => l.includes('kill-window'))
    expect(lines).toEqual([
      'would: tmux kill-window -t main:8',
      'would: tmux kill-window -t main:7',
    ])
  })
})
