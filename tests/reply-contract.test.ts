/**
 * The reply-contract hook blocks walls of text and nothing else.
 *
 * Two properties carry the mechanism's credibility, and both are asserted
 * here by EXECUTING the hook (the staleness-detection idiom -- reading the
 * script as text proves nothing about behavior):
 *
 *   1. It must never block what should pass: short answers, code-heavy short
 *      answers, skill-formatted reports, and long replies that carry the
 *      header + fold. A false positive teaches the fleet to route around the
 *      gate (the primer's own design law).
 *   2. It must always exit 0 and must bounce a given reply at most once.
 *      A Stop hook that loops or crashes eats the Captain's session.
 *
 * Law 12 control: the wall-of-text fixture is the deliberately-failing case.
 * If it ever passes, the instrument is broken, not the fleet suddenly tidy.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const HOOK = join(REPO_ROOT, '.claude', 'hooks', 'reply-contract.mjs')

let boardDir: string
beforeEach(() => {
  boardDir = mkdtempSync(join(tmpdir(), 'ss-reply-contract-'))
})
afterEach(() => {
  rmSync(boardDir, { recursive: true, force: true })
})

interface HookResult {
  stdout: string
  status: number
}

function runHook(payload: unknown, env: Record<string, string> = {}): HookResult {
  try {
    const stdout = execFileSync('node', [HOOK], {
      input: typeof payload === 'string' ? payload : JSON.stringify(payload),
      encoding: 'utf8',
      env: { ...process.env, SS_BOARD_DIR: boardDir, ...env },
    })
    return { stdout, status: 0 }
  } catch (err) {
    const e = err as { status?: number; stdout?: string }
    return { stdout: e.stdout ?? '', status: e.status ?? -1 }
  }
}

function decision(r: HookResult): string | null {
  if (!r.stdout.trim()) return null
  return (JSON.parse(r.stdout) as { decision?: string }).decision ?? null
}

const proseLine = (i: number) => `This is prose line number ${i} of a long unstructured reply.`
const WALL = Array.from({ length: 40 }, (_, i) => proseLine(i)).join('\n')

const CONFORMING_LONG = [
  'MISSION: reply-contract build',
  'STATUS: OK',
  'DID: wrote the hook and its tests',
  'NEXT: premise gate',
  '',
  'One line of summary prose.',
  '',
  '--- Detail',
  ...Array.from({ length: 30 }, (_, i) => proseLine(i)),
].join('\n')

const payload = (msg: string, extra: Record<string, unknown> = {}) => ({
  session_id: 'test-session',
  cwd: '/tmp/test-tree',
  hook_event_name: 'Stop',
  last_assistant_message: msg,
  ...extra,
})

describe('reply-contract: blocking', () => {
  it('BLOCKS a wall of text (Law 12 control: the case built to fail)', () => {
    const r = runHook(payload(WALL))
    expect(r.status).toBe(0)
    expect(decision(r)).toBe('block')
    const reason = (JSON.parse(r.stdout) as { reason: string }).reason
    expect(reason).toMatch(/MISSION/)
    expect(reason).toMatch(/Do NOT repeat/)
  })

  it('bounces a given reply at most once (marker file guard)', () => {
    expect(decision(runHook(payload(WALL)))).toBe('block')
    expect(decision(runHook(payload(WALL)))).toBeNull()
  })

  it('honors stop_hook_active without touching the marker', () => {
    const r = runHook(payload(WALL, { stop_hook_active: true }))
    expect(decision(r)).toBeNull()
  })

  it('blocks a long reply whose fold exists but whose header is missing', () => {
    const msg = [
      'Some intro.',
      '--- Detail',
      ...Array.from({ length: 30 }, (_, i) => proseLine(i)),
    ].join('\n')
    expect(decision(runHook(payload(msg)))).toBe('block')
  })

  it('blocks a long reply whose header exists but 20 prose lines sit above the fold', () => {
    const msg = [
      'MISSION: x',
      'STATUS: OK',
      'DID: y',
      'NEXT: z',
      ...Array.from({ length: 20 }, (_, i) => proseLine(i)),
      '--- Detail',
      ...Array.from({ length: 20 }, (_, i) => proseLine(i)),
    ].join('\n')
    expect(decision(runHook(payload(msg)))).toBe('block')
  })
})

describe('reply-contract: what must pass', () => {
  it('passes a short answer', () => {
    expect(decision(runHook(payload('Yes -- merged as #2140.')))).toBeNull()
  })

  it('passes a conforming long reply (header + fold)', () => {
    expect(decision(runHook(payload(CONFORMING_LONG)))).toBeNull()
  })

  it('passes a short answer wrapping a 60-line code block (prose counting)', () => {
    const msg = [
      'The failing output:',
      '```',
      ...Array.from({ length: 60 }, (_, i) => `log line ${i}`),
      '```',
      'Root cause is the umask.',
    ].join('\n')
    expect(decision(runHook(payload(msg)))).toBeNull()
  })

  it('passes tables and blockquotes without counting them as prose', () => {
    const msg = [
      'Summary line.',
      ...Array.from({ length: 40 }, (_, i) => `| row ${i} | value |`),
    ].join('\n')
    expect(decision(runHook(payload(msg)))).toBeNull()
  })

  it('passes skill-formatted reports untouched', () => {
    const critique = [
      "## Devil's Advocate Critique",
      ...Array.from({ length: 40 }, (_, i) => proseLine(i)),
    ].join('\n')
    expect(decision(runHook(payload(critique)))).toBeNull()
  })

  it('passes when disabled by env', () => {
    expect(decision(runHook(payload(WALL), { SS_REPLY_CONTRACT_DISABLE: '1' }))).toBeNull()
  })
})

describe('reply-contract: a cost is never weighed against the monthly fee', () => {
  // The Captain, 2026-09-23 and 2026-10-08 ("for the 50th time"): the fee already
  // carries chronology cost of goods, labor and hosting, and the margin is slim.
  // The first two offenders are sentences actually written; each must bounce at any length.
  const OFFENDERS = [
    'Neither threatens the engagement; the $5,150 monthly fee covers it many times over.',
    'That is small against the $5,000 monthly fee.',
    'Even $500 is a fraction of the retainer.',
    'The discovery backfill is well within the fee.',
  ]
  for (const s of OFFENDERS) {
    it(`BLOCKS: ${s}`, () => {
      const r = runHook(payload(`Short answer.\n\n${s}`))
      expect(r.status).toBe(0)
      expect(decision(r)).toBe('block')
      expect((JSON.parse(r.stdout) as { reason: string }).reason).toMatch(/ADDED cost of goods/)
    })
  }

  const ALLOWED = [
    'The weekday refresh reads about 180,000 tokens a day, about $16 a month of added cost of goods.',
    'The firm paid $2,500 for the first run; the hand run cost $513 in agents.',
    'Spend caps stay at $500 a job and $750 a month as runaway guards.',
    'The retainer started on 9/15 and bills on the 15th.',
  ]
  for (const s of ALLOWED) {
    it(`passes: ${s}`, () => {
      expect(decision(runHook(payload(s)))).toBeNull()
    })
  }
})

describe('reply-contract: containment', () => {
  it('exits 0 on malformed stdin', () => {
    expect(runHook('not json {{{').status).toBe(0)
  })

  it('exits 0 on a payload with no message', () => {
    const r = runHook({ session_id: 'x', hook_event_name: 'Stop' })
    expect(r.status).toBe(0)
    expect(decision(r)).toBeNull()
  })

  it('exits 0 with an unwritable board dir and does not block (fail-open)', () => {
    const r = runHook(payload(WALL), { SS_BOARD_DIR: '/dev/null/impossible' })
    expect(r.status).toBe(0)
    // bounce guard cannot be recorded -> must not risk a loop -> passes
    expect(decision(r)).toBeNull()
  })
})
