/**
 * /eos check J (scripts/eos-ready.mjs): of the PRs a session merged, the ones
 * that touch a client surface and are not READY are named at close.
 *
 * The replay fixture is two real sessions, reduced to PR numbers, changed paths
 * and parsed row kinds (no titles, no AC text: the repo is public):
 *   - the document-library session, which merged client-lane and seat builds
 *     with no runtime row: every one of them must be named not READY;
 *   - the wave-1 session (#3909-#3913), process tooling only: nothing listed.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { assess, prsFromLog, report, rowsOf } from '../scripts/eos-ready.mjs'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const FIXTURE = JSON.parse(
  readFileSync(join(ROOT, 'tests/fixtures/eos-ready/replay.json'), 'utf8')
) as {
  prs: {
    number: number
    group: string
    paths: string[]
    rows: { kind: string; naReasoned: boolean }[]
  }[]
}

const group = (g: string) => FIXTURE.prs.filter((p) => p.group === g).map((p) => assess(p))

describe('eos-ready replay', () => {
  it('names every client-surface PR of the document-library session not READY (control)', () => {
    const lib = group('library')
    const client = lib.filter((r) => r.clientSurface)
    expect(client.map((r) => r.number)).toEqual(expect.arrayContaining([3906]))
    expect(client.length).toBeGreaterThan(0)
    expect(client.every((r) => !r.ready)).toBe(true)
    const text = report(lib, { source: 'fixture' })
    expect(text).toMatch(/^Shipped, not READY:$/m)
    expect(text).toMatch(/^- #3906 .*\[client-lane\]: no \(runtime\) row$/m)
  })

  it('names #3882 and #3883 not READY: their runtime rows were never met', () => {
    const closed = group('library-closed')
    expect(closed.map((r) => [r.number, r.clientSurface, r.ready])).toEqual([
      [3882, true, false],
      [3883, true, false],
    ])
  })

  it('lists nothing for the #3909-#3913 session', () => {
    const w1 = group('wave1')
    expect(w1.map((r) => r.number)).toEqual(expect.arrayContaining([3909, 3910, 3912, 3913]))
    expect(w1.filter((r) => !r.ready)).toEqual([])
    expect(report(w1, { source: 'fixture' })).toMatch(/^Shipped, not READY: none$/m)
  })
})

describe('eos-ready rules', () => {
  const lane = ['operator/runners/jobs/jobs/example.py']

  it('a client-surface PR is READY only when every runtime row is met', () => {
    expect(assess({ number: 1, paths: lane, rows: [{ kind: 'met' }] }).ready).toBe(true)
    expect(assess({ number: 1, paths: lane, rows: [] }).ready).toBe(false)
    for (const kind of ['deferred', 'unmet', 'na']) {
      const r = assess({ number: 1, paths: lane, rows: [{ kind: 'met' }, { kind, ac: 'x' }] })
      expect(r.ready).toBe(false)
    }
  })

  it('a PR off the client surface is never listed, rows or not', () => {
    const r = assess({ number: 2, paths: ['docs/x.md', 'tests/a.test.ts'], rows: [] })
    expect(r.clientSurface).toBe(false)
    expect(r.ready).toBe(true)
  })

  it('reads runtime rows from a body with the merge gate parser', () => {
    const body = [
      '| AC | Status | Evidence |',
      '|---|---|---|',
      '| (runtime) the seat sends it | deferred | owner: lead; enforce_by: 2026-12-01 |',
      '| (runtime) a second act | n/a: the lane is dark | |',
    ].join('\n')
    expect(rowsOf(body).map((r) => [r.kind, r.naReasoned])).toEqual([
      ['deferred', false],
      ['na', true],
    ])
  })

  it('a read error is reported as could not evaluate, never as none', () => {
    const text = report([], { source: 'x', errors: ['gh: rate limited'] })
    expect(text).toMatch(/COULD NOT EVALUATE: gh: rate limited/)
    expect(text).not.toMatch(/not READY: none/)
  })
})

describe('eos-ready session trailer', () => {
  const LOG = [
    '',
    'feat(a): one (#10)\n\nbody\n\nClaude-Session: https://claude.ai/code/session_AAAAAAAAAA',
    'fix(b): two (#11)\n\nClaude-Session: https://claude.ai/code/session_BBBBBBBBBB',
    'chore: three (#12)\n\nmentions session_AAAAAAAAAA in prose only',
    'feat(c): four (#13)\n\nCo-Authored-By: x\nClaude-Session: https://claude.ai/code/session_AAAAAAAAAA',
  ].join('\x1e')

  it('keeps only squash commits whose Claude-Session trailer names the session', () => {
    expect(prsFromLog(LOG, 'session_AAAAAAAAAA')).toEqual([10, 13])
    expect(prsFromLog(LOG, 'session_BBBBBBBBBB')).toEqual([11])
  })

  it('refuses a needle too short to be a session id', () => {
    expect(prsFromLog(LOG, 'sess')).toEqual([])
  })
})
