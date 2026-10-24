/**
 * The board keeps peers visible and prunes the dead; the extractor serves
 * fresh doctrine or nothing.
 *
 * Both are executed, not read (staleness-detection idiom). Law 12 controls:
 * the extractor's garbage-input case and the board's dead-pid case are the
 * deliberately-failing instruments -- if either "passes", the check measured
 * nothing.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const BOARD = join(REPO_ROOT, '.claude', 'hooks', 'lib', 'board.mjs')
const EXTRACTOR = join(REPO_ROOT, '.claude', 'hooks', 'lib', 'extract-primer-lines.mjs')

let dir: string
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'ss-board-test-'))
})
afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
})

function board(args: string[], env: Record<string, string> = {}): string {
  return execFileSync('node', [BOARD, ...args], {
    encoding: 'utf8',
    env: { ...process.env, SS_BOARD_DIR: dir, ...env },
  })
}

describe('extract-primer-lines', () => {
  it('extracts numbered lines from the real doctrine file', () => {
    const doctrine = readFileSync(
      join(REPO_ROOT, 'docs', 'doctrine', 'agent-operating-doctrine.md'),
      'utf8'
    )
    const out = execFileSync('node', [EXTRACTOR], { input: doctrine, encoding: 'utf8' })
    const lines = out.trim().split('\n')
    expect(lines.length).toBeGreaterThanOrEqual(6)
    expect(out).toMatch(/Gate-enforced laws .*signal-not-volume/)
    expect(lines[0]).toMatch(/^1\. Resolve whose call it is/)
    // single-quote YAML escapes must be unescaped ("let''s" -> "let's")
    expect(out).toMatch(/"let's review X"/)
  })

  it('prints NOTHING on garbage input (Law 12 control: fallback must engage)', () => {
    const out = execFileSync('node', [EXTRACTOR], {
      input: '# not a doctrine\nno laws here\n',
      encoding: 'utf8',
    })
    expect(out).toBe('')
  })

  it('prints nothing when fewer than the sanity floor of laws parse', () => {
    const out = execFileSync('node', [EXTRACTOR], {
      input: "primer_line: 'only one law'\n",
      encoding: 'utf8',
    })
    expect(out).toBe('')
  })
})

describe('board: set / refresh / peers', () => {
  const me = '/fake/worktrees/me'
  const peer = '/fake/worktrees/peer-a'

  it('set + refresh prints the mission line', () => {
    board([
      'set',
      me,
      '--mission',
      'ship the thing',
      '--focus',
      '#123',
      '--pid',
      String(process.pid),
    ])
    const out = board(['refresh', me])
    expect(out).toContain('[mission] ship the thing (focus: #123)')
  })

  it('the mission line carries the skunkworks stance every turn', () => {
    // Folded 2026-10-24: the Captain typed /skunkworks to reset agents. The
    // stance rides this line because it is the only surface that survives
    // /clear and /compact and reaches sessions that never ran /sos.
    board(['set', me, '--mission', 'ship the thing', '--pid', String(process.pid)])
    const out = board(['refresh', me])
    expect(out).toContain('if the next action does not serve it, stop')
    expect(out).toContain('Reversible repo work: name the risk and mitigation, then move')
    expect(out).toContain('Client, paid or seat work: Law 16 path first')
    expect(out).toContain('Side work off this line goes to another session')
  })

  it('a registered session with no mission is told to draft one, with the stance', () => {
    // SessionStart registers the record before any focus exists; without
    // this branch such a session would see no stance at all.
    const unset = '/fake/worktrees/unset'
    board(['set', unset, '--pid', String(process.pid)])
    const out = board(['refresh', unset])
    expect(out).toContain('[mission] not set')
    expect(out).toContain('draft a winning line')
    expect(out).toContain('Law 16 path first')
  })

  it('refresh with no record prints nothing and does not crash', () => {
    expect(board(['refresh', '/fake/none'])).toBe('')
  })

  it('peers shows a live peer with mission and branch, not self', () => {
    board(['set', me, '--mission', 'mine', '--pid', String(process.pid)])
    board([
      'set',
      peer,
      '--mission',
      'their mission',
      '--focus',
      '#99',
      '--pid',
      String(process.pid),
    ])
    const out = board(['peers', me])
    expect(out).toContain('peer-a')
    expect(out).toContain('their mission')
    expect(out).not.toContain('mine')
  })

  it('prunes a dead-pid peer (Law 12 control: the record built to be removed)', () => {
    board(['set', peer, '--mission', 'ghost', '--pid', '999999'])
    const before = readdirSync(dir).filter((f) => f.startsWith('wt-'))
    expect(before.length).toBe(1)
    const out = board(['peers', me])
    expect(out).not.toContain('ghost')
    const after = readdirSync(dir).filter((f) => f.startsWith('wt-'))
    expect(after.length).toBe(0)
  })

  it('prunes a stale record by age even when the pid is alive (pid life is not trusted)', () => {
    board(['set', peer, '--mission', 'old work', '--pid', String(process.pid)])
    const f = readdirSync(dir).find((x) => x.startsWith('wt-'))!
    const rec = JSON.parse(readFileSync(join(dir, f), 'utf8'))
    rec.updated = new Date(Date.now() - 25 * 3600 * 1000).toISOString()
    writeFileSync(join(dir, f), JSON.stringify(rec))
    expect(board(['peers', me])).not.toContain('old work')
    expect(existsSync(join(dir, f))).toBe(false)
  })

  it('a corrupt record never breaks the listing', () => {
    writeFileSync(join(dir, 'wt-corrupt.json'), '{{{')
    board(['set', peer, '--mission', 'fine', '--pid', String(process.pid)])
    expect(board(['peers', me])).toContain('fine')
  })
})
