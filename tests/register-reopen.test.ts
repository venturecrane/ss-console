/**
 * `register reopen` -- take back a recorded delivery that did not hold
 * (ADR 0088). `delivered -> active`, and no other edge.
 *
 * Found 2026-10-13: a captured row was marked delivered on a sent letter, the
 * thing delivered was then withdrawn, and the row had no way back. `cancel`
 * refuses from `delivered`, and the nightly reconcile would have certified it
 * `verified`. Each refusal below is paired with the acceptance it must not
 * break. Fixture shape is register-cancel.test.ts's: a real bare origin,
 * because the claim under test is "the letter is on origin/main". All text is
 * synthetic: this repo is public.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { VALID_TRANSITIONS, type ObligationState } from '../src/lib/db/obligations'

const DIR = 'operator/customers/acme-fixture/correspondence'
const PROMISE = `${DIR}/30_scott-to-client_we-will-send-the-draft.md`
const DELIVERY = `${DIR}/31_scott-to-client_here-is-the-draft.md`
const WITHDRAWN = `${DIR}/32_client-to-scott_we-wrote-our-own.md`
const DRAFT = `${DIR}/33_client-to-scott_never-archived.md`

const PROMISE_TEXT = 'We will send you a first draft of the widget letter.\n'
const DELIVERY_TEXT = 'Here is the first draft of the widget letter.\n'
const WITHDRAWN_TEXT = 'Thanks, but we went ahead and wrote our own widget letter instead.\n'
const QUOTE = 'we went ahead and wrote our own widget letter'
const WHY = 'The draft was withdrawn; the client wrote their own.'

let dir: string
let engagements: string
let origin: string
let dbState: string

const FIXTURE_ENV = Object.fromEntries(
  Object.entries({ ...process.env, HUSKY: '0' }).filter(([k]) => !k.startsWith('GIT_'))
)
const git = (cwd: string, ...args: string[]) =>
  execFileSync('git', ['-c', 'core.hooksPath=/dev/null', ...args], {
    cwd,
    encoding: 'utf8',
    env: FIXTURE_ENV,
    stdio: ['ignore', 'pipe', 'pipe'],
  })

async function loadLib() {
  return await import('../.claude/hooks/lib/register.mjs')
}
async function loadClose() {
  return await import('../.claude/hooks/lib/register-close.mjs')
}

/**
 * A wrangler stand-in backed by a JSON file. It applies the state, evidence
 * and disposition the UPDATE actually names (unless told to drop the write),
 * so the read-back reads what the SQL said, not what the test hopes it said.
 */
const D1_STUB = `#!/usr/bin/env node
const fs = require('fs')
const path = process.env.REOPEN_DB
const db = JSON.parse(fs.readFileSync(path, 'utf8'))
const sql = process.argv[3] || ''
const out = (results) => process.stdout.write(JSON.stringify([{ results, success: true }]))
db.sql.push(sql)
if (/^UPDATE/i.test(sql)) {
  if (process.env.REOPEN_DROP_WRITE !== '1') {
    const state = /SET state = '([a-z_]+)'/.exec(sql)[1]
    const loc = /evidence_locator = (NULL|'[^']+')/.exec(sql)[1]
    const disp = /disposition = '((?:[^']|'')+)'/.exec(sql)
    for (const r of db.rows) {
      r.state = state
      r.evidence_locator = loc === 'NULL' ? null : loc.slice(1, -1)
      if (/evidence_last_verified_at = NULL/.test(sql)) r.evidence_last_verified_at = null
      if (disp) r.disposition = disp[1].replace(/''/g, "'")
    }
  }
  fs.writeFileSync(path, JSON.stringify(db))
  return out([])
}
fs.writeFileSync(path, JSON.stringify(db))
out(db.rows)
`

function setRows(rows: Record<string, unknown>[]) {
  writeFileSync(dbState, JSON.stringify({ rows, sql: [] }))
}
const readDb = () =>
  JSON.parse(readFileSync(dbState, 'utf8')) as { rows: Record<string, unknown>[]; sql: string[] }

const deliveredRow = (over: Record<string, unknown> = {}) => ({
  obligation_id: 'o-7',
  customer_slug: 'acme-fixture',
  kind: 'deliverable',
  stable_key: 'widget-letter',
  origin: 'captured',
  state: 'delivered',
  source_ref: PROMISE,
  evidence_class: 'attested',
  evidence_surface: 'engagements',
  evidence_locator: `venturecrane/engagements@000000000000:${DELIVERY}`,
  evidence_last_verified_at: '2026-10-12 00:00:00',
  ...over,
})

async function silence<T>(fn: () => Promise<T>): Promise<{ value: T; out: string; err: string }> {
  const out: string[] = []
  const err: string[] = []
  const [log, error] = [console.log, console.error]
  console.log = (...a: unknown[]) => void out.push(a.join(' '))
  console.error = (...a: unknown[]) => void err.push(a.join(' '))
  try {
    const value = await fn()
    return { value, out: out.join('\n'), err: err.join('\n') }
  } finally {
    console.log = log
    console.error = error
  }
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'register-reopen-'))
  origin = join(dir, 'origin.git')
  engagements = join(dir, 'engagements')
  git(dir, 'init', '--quiet', '--bare', '--initial-branch=main', origin)
  git(dir, 'clone', '--quiet', origin, engagements)
  git(engagements, 'config', 'user.email', 'test@example.com')
  git(engagements, 'config', 'user.name', 'test')
  mkdirSync(join(engagements, DIR), { recursive: true })
  writeFileSync(join(engagements, PROMISE), PROMISE_TEXT)
  writeFileSync(join(engagements, DELIVERY), DELIVERY_TEXT)
  writeFileSync(join(engagements, WITHDRAWN), WITHDRAWN_TEXT)
  git(engagements, 'add', '.')
  git(engagements, 'commit', '--quiet', '-m', 'archive letters')
  git(engagements, 'push', '--quiet', 'origin', 'HEAD:main')
  writeFileSync(join(engagements, DRAFT), WITHDRAWN_TEXT)

  dbState = join(dir, 'db.json')
  const stub = join(dir, 'd1.cjs')
  writeFileSync(stub, D1_STUB)
  chmodSync(stub, 0o755)
  setRows([deliveredRow()])

  process.env.SS_ENGAGEMENTS_DIR = engagements
  process.env.SS_OBLIGATION_JOURNAL_DIR = join(dir, 'journal')
  process.env.SS_REGISTER_D1_CMD = stub
  process.env.REOPEN_DB = dbState
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  for (const k of [
    'SS_ENGAGEMENTS_DIR',
    'SS_OBLIGATION_JOURNAL_DIR',
    'SS_REGISTER_D1_CMD',
    'REOPEN_DB',
    'REOPEN_DROP_WRITE',
  ]) {
    delete process.env[k]
  }
})

const reopenArgs = (over: Record<string, string> = {}) => {
  const a = {
    client: 'acme-fixture',
    key: 'widget-letter',
    evidence: WITHDRAWN,
    quote: QUOTE,
    why: WHY,
    ...over,
  }
  return ['reopen', ...Object.entries(a).flatMap(([k, v]) => [`--${k}`, v])]
}

const ALL_STATES = Object.keys(VALID_TRANSITIONS) as ObligationState[]

describe('the states a row can be reopened from', () => {
  it('is delivered alone, and the state machine allows delivered -> active', async () => {
    const { REOPENABLE_FROM } = await loadClose()
    expect(REOPENABLE_FROM).toEqual(['delivered'])
    expect(VALID_TRANSITIONS.delivered).toContain('active')
  })
})

describe('validateReopen', () => {
  const letter = { ok: true, text: WITHDRAWN_TEXT, suffix: WITHDRAWN, locator: 'x' }

  it('accepts a captured delivered row, a letter, a quote from it and a reason', async () => {
    const { validateReopen } = await loadClose()
    expect(validateReopen(deliveredRow(), letter, QUOTE, WHY)).toEqual({ ok: true })
  })

  it('refuses from EVERY state other than delivered (falsifier: walks the whole machine)', async () => {
    const { validateReopen } = await loadClose()
    const others = ALL_STATES.filter((s) => s !== 'delivered')
    // The walk must cover something, or "refused from all" is vacuous.
    expect(others.length).toBe(ALL_STATES.length - 1)
    expect(others).toEqual(expect.arrayContaining(['verified', 'closed', 'active', 'open']))
    for (const state of others) {
      const r = validateReopen(deliveredRow({ state }), letter, QUOTE, WHY)
      expect(r, state).toMatchObject({ ok: false, error: 'not_reopenable_from_state', state })
    }
  })

  it('refuses an imported row: its source settles it on the nightly run', async () => {
    const { validateReopen } = await loadClose()
    expect(validateReopen(deliveredRow({ origin: 'imported' }), letter, QUOTE, WHY).error).toBe(
      'imported_rows_close_from_their_source'
    )
  })

  it('refuses a quote that is not in the letter', async () => {
    const { validateReopen } = await loadClose()
    const r = validateReopen(deliveredRow(), letter, 'we never wrote any widget letter at all', WHY)
    expect(r.error).toBe('quote_not_found')
  })

  it('refuses the letter that made the promise', async () => {
    const { validateReopen } = await loadClose()
    const promise = { ok: true, text: PROMISE_TEXT, suffix: PROMISE, locator: 'x' }
    expect(
      validateReopen(deliveredRow(), promise, 'send you a first draft of the widget', WHY).error
    ).toBe('evidence_is_the_promise')
  })

  it('refuses a reopen with no reason', async () => {
    const { validateReopen } = await loadClose()
    expect(validateReopen(deliveredRow(), letter, QUOTE, '   ').error).toBe('reopen_needs_a_reason')
  })
})

describe('register reopen, end to end', () => {
  it('moves delivered -> active, clears the delivery receipt, names the letter and the reason', async () => {
    const { main } = await loadLib()
    const { value, out } = await silence(() => main(reopenArgs()))
    expect(value).toBe(0)
    const db = readDb()
    expect(db.rows[0].state).toBe('active')
    // The receipt is gone, so the reconciler probes `absent` (owed) rather than
    // `present` (and refusing to certify an active row every night).
    expect(db.rows[0].evidence_locator).toBeNull()
    expect(db.rows[0].evidence_last_verified_at).toBeNull()
    expect(db.rows[0].disposition).toMatch(
      /^delivery reopened: The draft was withdrawn; the client wrote their own\. \(venturecrane\/engagements@[0-9a-f]{12}:/
    )
    const update = db.sql.find((s) => /^UPDATE/i.test(s)) ?? ''
    expect(update).toContain("SET state = 'active'")
    expect(update).toContain("AND state = 'delivered'")
    expect(update).not.toContain("'verified'")
    expect(out).toContain('reopened acme-fixture/deliverable/widget-letter')
  })

  it('then cancels on the same letter: the full path the Captain runs', async () => {
    const { main } = await loadLib()
    expect((await silence(() => main(reopenArgs()))).value).toBe(0)
    const cancel = reopenArgs().filter((_, i, a) => a[i] !== '--why' && a[i - 1] !== '--why')
    cancel[0] = 'cancel'
    const { value } = await silence(() => main(cancel))
    expect(value).toBe(0)
    expect(readDb().rows[0].state).toBe('cancelled')
  })

  it('refuses a verified row end to end and writes nothing', async () => {
    const { main } = await loadLib()
    setRows([deliveredRow({ state: 'verified' })])
    const { value, err } = await silence(() => main(reopenArgs()))
    expect(value).toBe(1)
    expect(err).toMatch(/not_reopenable_from_state/)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('refuses a quote not in the file and writes nothing', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() =>
      main(reopenArgs({ quote: 'we never wrote any widget letter at all' }))
    )
    expect(value).toBe(1)
    expect(err).toMatch(/quote_not_found/)
    expect(readDb().rows[0].state).toBe('delivered')
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('refuses an unarchived letter and writes nothing', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() => main(reopenArgs({ evidence: DRAFT })))
    expect(value).toBe(1)
    expect(err).toMatch(/evidence_not_on_main/)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('fails when the write does not land, even though the UPDATE "succeeded"', async () => {
    const { main } = await loadLib()
    process.env.REOPEN_DROP_WRITE = '1'
    const { value, err } = await silence(() => main(reopenArgs()))
    expect(value).toBe(1)
    expect(err).toMatch(/did not land/)
    expect(readDb().rows[0].state).toBe('delivered')
  })

  it('requires --why and says so', async () => {
    const { main } = await loadLib()
    const args = reopenArgs().slice(0, -2)
    const { value, err } = await silence(() => main(args))
    expect(value).toBe(1)
    expect(err).toMatch(/--why is required/)
  })
})
