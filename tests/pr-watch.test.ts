/**
 * `.claude/bin/pr-watch`: a PR watcher that cannot be silent about a stuck PR.
 * Two watchers on 2026-11-06 waited only for "merged" and said nothing through
 * a failed lint check overnight; one also read a queued PR's null
 * autoMergeRequest as "not armed".
 */
import { execFileSync, spawnSync } from 'node:child_process'
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe as suite, expect, it } from 'vitest'
import { checkState, describe, idle, parse, type PrSnapshot } from '../scripts/pr-watch'

const pr = (over: Partial<PrSnapshot> = {}): PrSnapshot => ({
  number: 1,
  state: 'OPEN',
  isDraft: false,
  queue: null,
  checks: [],
  ...over,
})

const body = (n: number, state: string, queued: boolean, checks: object[] = []) =>
  JSON.stringify({
    data: {
      repository: {
        [`p${n}`]: {
          number: n,
          state,
          isDraft: false,
          mergeQueueEntry: queued ? { position: 1, state: 'AWAITING_CHECKS' } : null,
          commits: { nodes: [{ commit: { statusCheckRollup: { contexts: { nodes: checks } } } }] },
        },
      },
    },
  })

suite('pr-watch: what a PR shows', () => {
  it('names a failing check, and an unknown conclusion counts as failing, never green', () => {
    expect(checkState({ status: 'COMPLETED', conclusion: 'FAILURE' })).toBe('fail')
    expect(checkState({ status: 'COMPLETED', conclusion: 'SOMETHING_NEW' })).toBe('fail')
    expect(checkState({ status: 'COMPLETED', conclusion: 'SKIPPED' })).toBe('skip')
    expect(checkState({ status: 'IN_PROGRESS', conclusion: null })).toBe('running')
    expect(checkState({ state: 'PENDING' })).toBe('running')
    expect(checkState({ state: 'ERROR' })).toBe('fail')
    const checks = [
      { name: 'substrate', state: 'fail' as const },
      { name: 'Verify', state: 'pass' as const },
    ]
    expect(describe(pr({ checks }))).toBe('open | FAILING substrate')
  })

  it('reads the merge queue, so a queued PR is never reported as un-armed', () => {
    expect(describe(pr({ queue: { position: 1, state: 'AWAITING_CHECKS' } }))).toBe(
      'open | queued #1 AWAITING_CHECKS'
    )
    expect(describe(pr())).toBe('open | green, not queued')
  })

  it('says BLOCKED when every reported check passed but a required one has not started', () => {
    // 2026-11-06: Verify sat waiting for a runner, absent from the rollup, and the line read "green".
    const checks = [{ name: 'substrate', state: 'pass' as const }]
    expect(describe(pr({ checks, mergeState: 'BLOCKED' }))).toBe(
      'open | green, not queued (BLOCKED)'
    )
    expect(describe(pr({ checks, mergeState: 'UNKNOWN' }))).toBe('open | green, not queued')
  })

  it('does not change its line as running checks finish one by one, only when one fails', () => {
    const three = pr({
      checks: ['a', 'b', 'c'].map((name) => ({ name, state: 'running' as const })),
    })
    const one = pr({
      checks: [
        { name: 'a', state: 'pass' },
        { name: 'b', state: 'pass' },
        { name: 'c', state: 'running' },
      ],
    })
    expect(describe(three)).toBe(describe(one))
    expect(describe(three)).toBe('open | checks running')
    const failed = pr({
      checks: [
        { name: 'a', state: 'fail' },
        { name: 'c', state: 'running' },
      ],
    })
    expect(describe(failed)).toBe('open | FAILING a | checks running')
  })

  it('is idle only when nothing is running and it is not in the queue', () => {
    expect(idle(pr())).toBe(true)
    expect(idle(pr({ checks: [{ name: 'x', state: 'running' }] }))).toBe(false)
    expect(idle(pr({ queue: { position: 2, state: 'QUEUED' } }))).toBe(false)
    expect(idle(pr({ state: 'MERGED' }))).toBe(false)
  })

  it('parses a GraphQL response; a PR missing from it is an error, not an empty list', () => {
    const json = body(7, 'OPEN', false, [
      { __typename: 'CheckRun', name: 'overlay', status: 'COMPLETED', conclusion: 'FAILURE' },
      { __typename: 'StatusContext', context: 'legacy', state: 'SUCCESS' },
    ])
    expect(parse(json, [7])[0].checks).toEqual([
      { name: 'overlay', state: 'fail' },
      { name: 'legacy', state: 'pass' },
    ])
    expect(() => parse(json, [8])).toThrow('PR #8 not found')
  })
})

suite('pr-watch: the command', () => {
  // A fake gh that answers the n-th call with the n-th body (the last one repeats).
  const fakeGh = (bodies: string[]) => {
    const dir = mkdtempSync(join(tmpdir(), 'prwatch-'))
    const counter = join(dir, 'n')
    writeFileSync(counter, '0')
    bodies.forEach((b, i) => writeFileSync(join(dir, `b${i}`), b))
    const gh = join(dir, 'gh')
    writeFileSync(
      gh,
      `#!/bin/sh\nn=$(cat "${counter}")\n[ "$n" -gt ${bodies.length - 1} ] && n=${bodies.length - 1}\n` +
        `cat "${dir}/b$n"\necho $((n+1)) > "${counter}"\n`
    )
    chmodSync(gh, 0o755)
    return gh
  }
  const run = (args: string[], bodies: string[]) =>
    spawnSync(resolve('.claude/bin/pr-watch'), args, {
      encoding: 'utf8',
      env: { ...process.env, SS_PRWATCH_GH: fakeGh(bodies) },
      timeout: 30_000,
    })

  it('prints the queued state once, then the merge, then exits 0', () => {
    const r = run(
      ['5', '--interval', '0.05'],
      [body(5, 'OPEN', true), body(5, 'OPEN', true), body(5, 'MERGED', false)]
    )
    const lines = r.stdout.trim().split('\n')
    expect(lines).toHaveLength(3)
    expect(lines[0]).toMatch(/#5 open \| queued #1 AWAITING_CHECKS$/)
    expect(lines[1]).toMatch(/#5 MERGED$/)
    expect(lines[2]).toBe('all merged or closed')
    expect(r.status).toBe(0)
  })

  it('says STALLED when a green PR sits outside the queue, and STILL OPEN at the deadline', () => {
    // FALSIFIER: a watcher that waits only for "merged" prints nothing here.
    const r = run(
      ['5', '--interval', '0.05', '--stall-minutes', '0.001', '--max-hours', '0.0002'],
      [body(5, 'OPEN', false)]
    )
    expect(r.stdout).toMatch(/#5 open \| green, not queued/)
    expect(r.stdout).toMatch(/#5 STALLED \d+ min: open \| green, not queued/)
    expect(r.stdout.match(/STALLED/g)).toHaveLength(1)
    expect(r.stdout).toMatch(/STILL OPEN #5/)
    expect(r.status).toBe(1)
  })

  it('refuses bad arguments with exit 2', () => {
    expect(
      spawnSync(resolve('.claude/bin/pr-watch'), ['--interval', '0'], { encoding: 'utf8' }).status
    ).toBe(2)
    expect(spawnSync(resolve('.claude/bin/pr-watch'), [], { encoding: 'utf8' }).status).toBe(2)
  })
})

// Keep execFileSync imported for parity with the other bin tests' style.
void execFileSync
