/**
 * bash-log.mjs: the PostToolUse Bash log (wave 2, Q8 and the per-PR process
 * record). Log-only, so the load-bearing assertions are (1) it writes the
 * right line for the case it exists for, (2) it writes nothing for the case
 * next to it, and (3) it exits 0 and prints nothing on every input.
 *
 * Fixtures are synthetic transcripts: a few lines carrying only the fields the
 * hook reads (tool_use names, Skill inputs, a typed /command). No real
 * transcript content is used; the repo is public.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

const HOOK = resolve('.claude/hooks/bash-log.mjs')

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})
function tmp(prefix: string): string {
  const d = mkdtempSync(join(tmpdir(), prefix))
  scratch.push(d)
  return d
}

function run(payload: unknown, board: string): { out: string; code: number } {
  try {
    const out = execFileSync('node', [HOOK], {
      input: typeof payload === 'string' ? payload : JSON.stringify(payload),
      env: { ...process.env, SS_BOARD_DIR: board },
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    return { out, code: 0 }
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; status?: number }
    return { out: `${e.stdout ?? ''}${e.stderr ?? ''}`, code: e.status ?? -1 }
  }
}

function rows(board: string, file: string): Record<string, unknown>[] {
  const p = join(board, file)
  if (!existsSync(p)) return []
  return readFileSync(p, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((l) => JSON.parse(l) as Record<string, unknown>)
}

const skillUse = (skill: string) =>
  JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'tool_use', name: 'Skill', input: { skill } }] },
  })
const planExit = JSON.stringify({
  type: 'assistant',
  message: { content: [{ type: 'tool_use', name: 'ExitPlanMode', input: {} }] },
})
const typed = (cmd: string) =>
  JSON.stringify({
    type: 'user',
    message: {
      content: `<command-message>x</command-message><command-name>/${cmd}</command-name>`,
    },
  })
const chatter = JSON.stringify({
  type: 'assistant',
  message: { content: [{ type: 'text', text: 'we could run the Skill auto-build later' }] },
})

/** <project>/<sid>.jsonl plus <project>/<sid>/subagents/agent-<id>.jsonl files. */
function session(main: string[], subagents: Record<string, string[]> = {}) {
  const project = tmp('bashlog-proj-')
  const sid = 'sess-0001'
  const mainPath = join(project, `${sid}.jsonl`)
  writeFileSync(mainPath, main.join('\n') + '\n')
  const subDir = join(project, sid, 'subagents')
  mkdirSync(subDir, { recursive: true })
  const subPaths: Record<string, string> = {}
  for (const [id, lines] of Object.entries(subagents)) {
    subPaths[id] = join(subDir, `agent-${id}.jsonl`)
    writeFileSync(subPaths[id], lines.join('\n') + '\n')
  }
  return { sid, mainPath, subPaths }
}

const created = (over: Record<string, unknown> = {}) => ({
  hook_event_name: 'PostToolUse',
  tool_name: 'Bash',
  tool_input: { command: 'gh pr create --title "feat: x" --body-file /tmp/b.md' },
  tool_response: {
    stdout: 'https://github.com/venturecrane/ss-console/pull/4242\n',
    stderr: '',
    interrupted: false,
  },
  duration_ms: 3000,
  ...over,
})

describe('long calls', () => {
  it('logs a call over 240s with the first 80 characters, background and subagent flags', () => {
    const board = tmp('bashlog-board-')
    const cmd = `until gh pr view 1 --json state | grep -q MERGED; do sleep 30; done ${'x'.repeat(100)}`
    const res = run(
      {
        session_id: 's1',
        agent_id: 'a123',
        tool_name: 'Bash',
        tool_input: { command: cmd, run_in_background: false },
        tool_response: { stdout: '', stderr: '', interrupted: false },
        duration_ms: 241_000,
      },
      board
    )
    expect(res).toEqual({ out: '', code: 0 })
    const [row] = rows(board, 'long-cmd.log')
    expect(row).toMatchObject({
      session_id: 's1',
      duration_ms: 241_000,
      cmd: cmd.slice(0, 80),
      run_in_background: false,
      subagent: true,
    })
  })

  it('logs nothing at or under 240s, or with no duration in the payload', () => {
    const board = tmp('bashlog-board-')
    for (const duration_ms of [240_000, 12, undefined]) {
      run({ session_id: 's', tool_input: { command: 'sleep 1' }, duration_ms }, board)
    }
    expect(rows(board, 'long-cmd.log')).toEqual([])
  })
})

describe('built-by: what built each PR', () => {
  it('records the PR with flags read from the main and subagent transcripts', () => {
    const board = tmp('bashlog-board-')
    // The lead runs /auto-build and plans; a builder subagent runs /wired and
    // opens the PR. The record must carry both halves.
    const s = session([typed('auto-build'), planExit, chatter], { b1: [skillUse('wired')] })
    const res = run(
      created({ session_id: s.sid, agent_id: 'b1', transcript_path: s.subPaths.b1 }),
      board
    )
    expect(res).toEqual({ out: '', code: 0 })
    expect(rows(board, 'built-by.jsonl')).toEqual([
      expect.objectContaining({
        pr: 4242,
        repo: 'venturecrane/ss-console',
        session_id: s.sid,
        agent: true,
        auto_build: true,
        wired: true,
        plan: true,
        critique: false,
      }),
    ])
  })

  it('a session that ran none of them records all false; prose naming a skill is not a run', () => {
    const board = tmp('bashlog-board-')
    const s = session([chatter])
    run(created({ session_id: s.sid, transcript_path: s.mainPath }), board)
    expect(rows(board, 'built-by.jsonl')).toEqual([
      expect.objectContaining({
        pr: 4242,
        agent: false,
        auto_build: false,
        wired: false,
        plan: false,
        critique: false,
      }),
    ])
  })

  it('reads a plugin-prefixed Skill name the same as the bare one', () => {
    const board = tmp('bashlog-board-')
    const s = session([skillUse('ss:critique')])
    run(created({ session_id: s.sid, transcript_path: s.mainPath }), board)
    expect(rows(board, 'built-by.jsonl')[0]).toMatchObject({ critique: true })
  })

  it('writes nothing when gh pr create failed, was interrupted, or was not gh pr create', () => {
    const board = tmp('bashlog-board-')
    const s = session([typed('auto-build')])
    const base = { session_id: s.sid, transcript_path: s.mainPath }
    run(created({ ...base, tool_response: { stdout: '', stderr: 'GraphQL: error' } }), board)
    run(
      created({
        ...base,
        tool_response: {
          stdout: 'https://github.com/venturecrane/ss-console/pull/1\n',
          interrupted: true,
        },
      }),
      board
    )
    run(
      created({
        ...base,
        tool_input: { command: 'gh pr view 4242 --json url' },
      }),
      board
    )
    expect(rows(board, 'built-by.jsonl')).toEqual([])
  })

  it('reads a subagent transcript at most once: its flags come from the marker after that', () => {
    const board = tmp('bashlog-board-')
    const s = session([], { b1: [chatter] })
    const payload = created({ session_id: s.sid, agent_id: 'b1', transcript_path: s.subPaths.b1 })
    run(payload, board)
    // The transcript now gains a /wired call. A second scan would see it; the
    // marker means it is not read again.
    writeFileSync(s.subPaths.b1, [chatter, skillUse('wired')].join('\n') + '\n')
    run(payload, board)
    const [first, second] = rows(board, 'built-by.jsonl')
    expect(first).toMatchObject({ wired: false })
    expect(second).toMatchObject({ wired: false })
    expect(existsSync(join(board, 'built-by-scan', s.sid, 'agent-b1.jsonl.json'))).toBe(true)
  })
})

describe('never breaks the session', () => {
  it('exits 0 and prints nothing on malformed, empty, and pathless payloads', () => {
    const board = tmp('bashlog-board-')
    for (const p of [
      '{not json',
      '',
      '{}',
      JSON.stringify(created({ transcript_path: '/nonexistent/x.jsonl', session_id: 'z' })),
    ]) {
      expect(run(p, board)).toEqual({ out: '', code: 0 })
    }
  })
})
