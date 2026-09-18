/**
 * The three modules the reconciler and the register CLI share.
 *
 * They are `.mjs` rather than `.ts` for one structural reason: the reconciler
 * runs under `tsx` in CI, and `.claude/hooks/lib/register.mjs` is run by bare
 * `node` from a bash wrapper with no build step. Before they existed, each rule
 * was written twice and the copies had already drifted -- the envelope parse
 * threw on garbage in one copy and returned empty in the other, so CI read "the
 * database is broken" where the CLI read "no such customer".
 *
 * So the falsifier for this whole file is the drift itself, which is why the
 * consumer-agreement cases live here rather than being split per module.
 */

import { describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { resolve } from 'path'
import { parseWranglerJson } from '../scripts/lib/wrangler-envelope.mjs'
import { clientOf, seatsOf, SMD_CLIENT } from '../scripts/lib/seat-clients.mjs'
import { ageInDays, sqliteUtcMs } from '../scripts/lib/sqlite-time.mjs'

const ROOT = process.cwd()

describe('parseWranglerJson', () => {
  it('reads the array-wrapped envelope', () => {
    expect(parseWranglerJson('[{"results":[{"a":1}],"success":true}]')).toEqual([{ a: 1 }])
  })

  it('reads the bare-object envelope', () => {
    // Wrangler emits both shapes depending on version and command, and both are
    // in use. Dropping either branch fails here.
    expect(parseWranglerJson('{"results":[{"a":1}],"success":true}')).toEqual([{ a: 1 }])
  })

  it('returns an empty list for an envelope carrying no rows', () => {
    expect(parseWranglerJson('[{"results":[],"success":true}]')).toEqual([])
    expect(parseWranglerJson('[{}]')).toEqual([])
  })

  it('THROWS on output that is not JSON, rather than reading it as empty', () => {
    // The load-bearing case. Swallowing unparseable output is how a broken
    // query reads as a clean table -- the failure this register exists to end.
    // Callers that want "treat garbage as empty" must say so at the call site.
    expect(() => parseWranglerJson('not json at all')).toThrow()
    expect(() => parseWranglerJson('')).toThrow()
  })
})

describe('the CLI still runs as a subprocess', () => {
  it('.claude/bin/register list reaches its cross-tree imports', () => {
    // register.mjs statically imports three modules from ../../../scripts/lib/.
    // A static import that fails to resolve throws at MODULE LOAD, which takes
    // down `add` and `sync` too -- not just the command that needed it. The
    // in-process tests import the module directly from the repo root and so
    // cannot see a path that only breaks under the bash wrapper. This runs the
    // real wrapper. Breaking either import path fails here and nowhere else.
    //
    // It points at a nonexistent D1 binary on purpose: exit 1 with the register's
    // own "cannot read" message proves the module loaded and dispatched, without
    // this test ever touching production.
    let output: string
    let code = 0
    try {
      output = execFileSync('.claude/bin/register', ['list'], {
        cwd: ROOT,
        encoding: 'utf8',
        env: { ...process.env, SS_REGISTER_D1_CMD: resolve(ROOT, 'no-such-binary-for-tests') },
        stdio: ['ignore', 'pipe', 'pipe'],
      })
    } catch (err) {
      const e = err as { status?: number; stdout?: string; stderr?: string }
      code = e.status ?? -1
      output = `${e.stdout ?? ''}${e.stderr ?? ''}`
    }
    expect(output).not.toMatch(/Cannot find module|ERR_MODULE_NOT_FOUND/)
    expect(code).toBe(1)
    expect(output).toMatch(/cannot read the register/)
  })
})

describe('clientOf', () => {
  it('rolls SMD-owned seats up to the SMD client', () => {
    for (const seat of ['pilot-smokeball', 'smd-staging', 'scott']) {
      expect(clientOf(seat)).toBe(SMD_CLIENT)
    }
  })

  it('seatsOf is the inverse, so a client name can be filtered on', () => {
    // Rows are STORED by seat and ASKED FOR by client. Without this inverse,
    // `register list --client smd-services` filtered the seat column by a client
    // name, matched zero rows, and printed "nothing open" while all three of our
    // seats had work -- a clean-looking report over open work, the precise
    // failure this register exists to end. Found in review of PR #2837 and
    // reproduced against production before the fix.
    expect(seatsOf(SMD_CLIENT).sort()).toEqual(['pilot-smokeball', 'scott', 'smd-staging'])
    // Round-trips: every seat the inverse names maps back to the client.
    for (const seat of seatsOf(SMD_CLIENT)) expect(clientOf(seat)).toBe(SMD_CLIENT)
  })

  it('seatsOf leaves an ordinary client as its own single seat', () => {
    expect(seatsOf('ashton-price')).toEqual(['ashton-price'])
  })

  it('passes every other seat through as its own client', () => {
    // Identity is the default so that onboarding a client needs no change here.
    // Removing the default -- e.g. switching to a client allowlist -- makes a
    // new client vanish from the register, which is the exact failure the
    // register exists to prevent. This case is what catches that inversion.
    expect(clientOf('ashton-price')).toBe('ashton-price')
    expect(clientOf('some-firm-we-sign-next-week')).toBe('some-firm-we-sign-next-week')
  })
})

describe('sqlite timestamps', () => {
  it('reads a zone-less SQLite timestamp as UTC, not local', () => {
    // CURRENT_TIMESTAMP writes `YYYY-MM-DD HH:MM:SS` in UTC with no marker, and
    // JS parses that form as LOCAL. On a UTC-7 machine every row read seven
    // hours young, and a row written moments earlier printed as "-1d old".
    expect(sqliteUtcMs('2026-09-17 12:00:00')).toBe(Date.parse('2026-09-17T12:00:00Z'))
  })

  it('respects an explicit zone when the value already carries one', () => {
    expect(sqliteUtcMs('2026-09-17T12:00:00Z')).toBe(Date.parse('2026-09-17T12:00:00Z'))
    expect(sqliteUtcMs('2026-09-17T05:00:00-07:00')).toBe(Date.parse('2026-09-17T12:00:00Z'))
  })

  it('returns null for absent or unparseable values', () => {
    expect(sqliteUtcMs(null)).toBeNull()
    expect(sqliteUtcMs('')).toBeNull()
    expect(sqliteUtcMs('not a date')).toBeNull()
    expect(ageInDays('not a date')).toBeNull()
  })

  it('counts whole elapsed days', () => {
    const now = Date.parse('2026-09-17T12:00:00Z')
    expect(ageInDays('2026-08-18 12:00:00', now)).toBe(30)
    expect(ageInDays('2026-09-17 11:00:00', now)).toBe(0)
  })

  it('never reports a row as younger than new', () => {
    // A future timestamp is clamped rather than surfaced. "-1d old" teaches a
    // reader the number is untrustworthy, and then the 30d ladder is too.
    const now = Date.parse('2026-09-17T12:00:00Z')
    expect(ageInDays('2026-09-18 12:00:00', now)).toBe(0)
  })
})
