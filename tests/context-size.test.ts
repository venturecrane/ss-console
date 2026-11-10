/**
 * context-size.mjs: the size of the context the last model call read, logged
 * at every prompt (wave 2, Q7). LOG-ONLY.
 *
 * The cases that matter: the LAST usage line wins, a line split across read
 * chunks still parses, the 1MB cap turns into "not_found" rather than a guess,
 * and the CLI writes one JSON line while printing nothing. Fixtures carry
 * usage fields only.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import { contextOfLine, lastContextSize } from '../.claude/hooks/lib/context-size.mjs'

const CLI = resolve('.claude/hooks/lib/context-size.mjs')

const scratch: string[] = []
afterEach(() => {
  while (scratch.length) rmSync(scratch.pop() as string, { recursive: true, force: true })
})
function tmp(): string {
  const d = mkdtempSync(join(tmpdir(), 'ctxsize-'))
  scratch.push(d)
  return d
}

const call = (input: number, read: number, created: number, model = 'claude-opus-5-5') =>
  JSON.stringify({
    type: 'assistant',
    message: {
      model,
      usage: {
        input_tokens: input,
        cache_read_input_tokens: read,
        cache_creation_input_tokens: created,
        output_tokens: 999,
      },
    },
  })
const prompt = JSON.stringify({ type: 'user', message: { content: 'hello' } })

function transcript(lines: string[]): string {
  const p = join(tmp(), 't.jsonl')
  writeFileSync(p, lines.join('\n') + '\n')
  return p
}

describe('lastContextSize', () => {
  it('sums input, cache read and cache creation of the LAST assistant usage line', () => {
    const p = transcript([prompt, call(10, 100_000, 5), prompt, call(2, 310_000, 7), prompt])
    expect(lastContextSize(p)).toEqual({ tokens: 310_009, outcome: 'ok' })
  })

  it('skips synthetic lines, whose all-zero usage is not a call', () => {
    const p = transcript([call(1, 200_000, 0), call(0, 0, 0, '<synthetic>')])
    expect(lastContextSize(p)).toEqual({ tokens: 200_001, outcome: 'ok' })
  })

  it('finds a usage line split across read chunks', () => {
    const filler = JSON.stringify({ type: 'user', message: { content: 'é'.repeat(5000) } })
    const p = transcript([call(3, 123_456, 0), filler, filler])
    // A 997-byte chunk splits both the usage line and the 2-byte characters.
    expect(lastContextSize(p, { chunk: 997 })).toEqual({ tokens: 123_459, outcome: 'ok' })
  })

  it('stops at the cap and says not_found rather than reporting an older or smaller number', () => {
    const big = JSON.stringify({ type: 'user', message: { content: 'x'.repeat(50_000) } })
    const p = transcript([call(1, 400_000, 0), big])
    expect(lastContextSize(p, { cap: 10_000 })).toEqual({ tokens: null, outcome: 'not_found' })
    expect(lastContextSize(p)).toEqual({ tokens: 400_001, outcome: 'ok' })
  })

  it('separates no usage yet, no transcript, and no path', () => {
    expect(lastContextSize(transcript([prompt]))).toEqual({ tokens: null, outcome: 'not_found' })
    expect(lastContextSize('/nonexistent/t.jsonl').outcome).toBe('no_transcript')
    expect(lastContextSize(undefined).outcome).toBe('no_transcript')
  })

  it('contextOfLine ignores non-assistant lines that mention usage', () => {
    expect(contextOfLine(JSON.stringify({ type: 'user', usage: 1, x: 'assistant' }))).toBeNull()
    expect(contextOfLine('{"type":"assistant","usage" broken')).toBeNull()
  })
})

describe('the CLI the primer calls', () => {
  it('appends one JSON line to the board log and prints nothing', () => {
    const board = tmp()
    const p = transcript([call(5, 300_000, 0)])
    const out = execFileSync('node', [CLI, 'log'], {
      input: JSON.stringify({ session_id: 's9', transcript_path: p, prompt: 'hi' }),
      env: { ...process.env, SS_BOARD_DIR: board },
      encoding: 'utf8',
    })
    expect(out).toBe('')
    const lines = readFileSync(join(board, 'context-size.log'), 'utf8').trim().split('\n')
    expect(lines).toHaveLength(1)
    expect(JSON.parse(lines[0])).toMatchObject({ session_id: 's9', tokens: 300_005, outcome: 'ok' })
  })

  it('exits 0 and prints nothing on a malformed payload, logging no_transcript', () => {
    const board = tmp()
    const out = execFileSync('node', [CLI, 'log'], {
      input: '{nope',
      env: { ...process.env, SS_BOARD_DIR: board },
      encoding: 'utf8',
    })
    expect(out).toBe('')
    const row = JSON.parse(readFileSync(join(board, 'context-size.log'), 'utf8').trim())
    expect(row).toMatchObject({ tokens: null, outcome: 'no_transcript' })
  })
})
