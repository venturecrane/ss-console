/**
 * `register cancel` -- the half of the register that retires a letter-stated
 * promise the CLIENT released (ADR 0088).
 *
 * Found 2026-09-28: A&P withdrew a request two hours after the scope reply went
 * out ("lets just scratch this for now"), and the row it had created could
 * only leave `open` by a reconciler that refuses captured rows or by a
 * hand-written UPDATE. `deliver` records a promise kept; this records one
 * released, and it must hold the same evidence line: the client's letter, on
 * the archive's main, quoted verbatim, and never the letter that made the
 * promise. Each refusal below is paired with the acceptance it must not break.
 *
 * Fixture shape is register-deliver.test.ts's: a real bare origin, because the
 * claim under test is "the letter is on origin/main".
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { VALID_TRANSITIONS } from '../src/lib/db/obligations'

const DIR = 'operator/customers/acme-fixture/correspondence'
const PROMISE = `${DIR}/20_scott-to-client_we-will-send-terms.md`
const WITHDRAWN = `${DIR}/21_client-to-scott_scratch-this-for-now.md`
const DRAFT = `${DIR}/22_client-to-scott_never-archived.md`

const PROMISE_TEXT = 'We will nail down the scope with you first, then put a number on it.\n'
const WITHDRAWN_TEXT =
  'I have a basic build for one already, lets just scratch this for now as well.\n'
const QUOTE = 'lets just scratch this for now as well'

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

/**
 * A wrangler stand-in backed by a JSON file. It applies whatever `state = '…'`
 * the UPDATE names (unless told to drop the write) so the read-back reads what
 * the SQL actually said, not what the test hopes it said.
 */
const D1_STUB = `#!/usr/bin/env node
const fs = require('fs')
const path = process.env.CANCEL_DB
const db = JSON.parse(fs.readFileSync(path, 'utf8'))
const sql = process.argv[3] || ''
const out = (results) => process.stdout.write(JSON.stringify([{ results, success: true }]))
db.sql.push(sql)
if (/^UPDATE/i.test(sql)) {
  if (process.env.CANCEL_DROP_WRITE !== '1') {
    const state = /SET state = '([a-z_]+)'/.exec(sql)[1]
    const loc = /evidence_locator = '([^']+)'/.exec(sql)[1]
    const disp = /disposition = '([^']+)'/.exec(sql)
    for (const r of db.rows) { r.state = state; r.evidence_locator = loc; if (disp) r.disposition = disp[1] }
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

const openRow = (over: Record<string, unknown> = {}) => ({
  obligation_id: 'o-9',
  customer_slug: 'acme-fixture',
  kind: 'deliverable',
  stable_key: 'marketing-terms',
  origin: 'captured',
  state: 'open',
  source_ref: PROMISE,
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
  dir = mkdtempSync(join(tmpdir(), 'register-cancel-'))
  origin = join(dir, 'origin.git')
  engagements = join(dir, 'engagements')
  git(dir, 'init', '--quiet', '--bare', '--initial-branch=main', origin)
  git(dir, 'clone', '--quiet', origin, engagements)
  git(engagements, 'config', 'user.email', 'test@example.com')
  git(engagements, 'config', 'user.name', 'test')
  mkdirSync(join(engagements, DIR), { recursive: true })
  writeFileSync(join(engagements, PROMISE), PROMISE_TEXT)
  writeFileSync(join(engagements, WITHDRAWN), WITHDRAWN_TEXT)
  git(engagements, 'add', '.')
  git(engagements, 'commit', '--quiet', '-m', 'archive letters')
  git(engagements, 'push', '--quiet', 'origin', 'HEAD:main')
  writeFileSync(join(engagements, DRAFT), WITHDRAWN_TEXT)

  dbState = join(dir, 'db.json')
  const stub = join(dir, 'd1.cjs')
  writeFileSync(stub, D1_STUB)
  chmodSync(stub, 0o755)
  setRows([openRow()])

  process.env.SS_ENGAGEMENTS_DIR = engagements
  process.env.SS_OBLIGATION_JOURNAL_DIR = join(dir, 'journal')
  process.env.SS_REGISTER_D1_CMD = stub
  process.env.CANCEL_DB = dbState
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  for (const k of [
    'SS_ENGAGEMENTS_DIR',
    'SS_OBLIGATION_JOURNAL_DIR',
    'SS_REGISTER_D1_CMD',
    'CANCEL_DB',
    'CANCEL_DROP_WRITE',
  ]) {
    delete process.env[k]
  }
})

const cancelArgs = (over: Record<string, string> = {}) => {
  const a = {
    client: 'acme-fixture',
    key: 'marketing-terms',
    evidence: WITHDRAWN,
    quote: QUOTE,
    ...over,
  }
  return ['cancel', ...Object.entries(a).flatMap(([k, v]) => [`--${k}`, v])]
}

describe('the states a row can be cancelled from', () => {
  it('matches every state the state machine lets reach cancelled', async () => {
    const { CANCELLABLE_FROM } = await loadLib()
    const fromMachine = Object.entries(VALID_TRANSITIONS)
      .filter(([, to]) => to.includes('cancelled'))
      .map(([from]) => from)
    expect([...CANCELLABLE_FROM].sort()).toEqual(fromMachine.sort())
  })

  it('includes parked, which deliver does not: a stalled promise can be released, not kept', async () => {
    const { CANCELLABLE_FROM, DELIVERABLE_FROM } = await loadLib()
    expect(CANCELLABLE_FROM).toContain('parked')
    expect(DELIVERABLE_FROM).not.toContain('parked')
  })
})

describe('validateCancellation', () => {
  const letter = { ok: true, text: WITHDRAWN_TEXT, suffix: WITHDRAWN, locator: 'x' }

  it("accepts a captured open row, the client's letter, and a quote from it", async () => {
    const { validateCancellation } = await loadLib()
    expect(validateCancellation(openRow(), letter, QUOTE)).toEqual({ ok: true })
  })

  it('refuses an imported row: its source withdraws it on the nightly run', async () => {
    const { validateCancellation } = await loadLib()
    expect(validateCancellation(openRow({ origin: 'imported' }), letter, QUOTE).error).toBe(
      'imported_rows_close_from_their_source'
    )
  })

  it('refuses a row already settled', async () => {
    const { validateCancellation } = await loadLib()
    for (const state of ['delivered', 'verified', 'closed', 'cancelled', 'void']) {
      expect(validateCancellation(openRow({ state }), letter, QUOTE).error).toBe(
        'not_cancellable_from_state'
      )
    }
  })

  it('refuses the letter that made the promise as proof it was released', async () => {
    const { validateCancellation } = await loadLib()
    const promise = { ok: true, text: PROMISE_TEXT, suffix: PROMISE, locator: 'x' }
    expect(
      validateCancellation(openRow(), promise, 'nail down the scope with you first, then').error
    ).toBe('evidence_is_the_promise')
  })

  it("refuses a quote that is not in the client's letter", async () => {
    const { validateCancellation } = await loadLib()
    const r = validateCancellation(openRow(), letter, 'lets just cancel this for good as well')
    expect(r.error).toBe('quote_not_found')
  })

  it('refuses a quote too short to bind the letter to this row', async () => {
    const { validateCancellation } = await loadLib()
    expect(validateCancellation(openRow(), letter, 'scratch this').error).toBe('quote_too_short')
  })
})

describe('register cancel, end to end', () => {
  it('cancels, names the client letter in the disposition, and reads it back', async () => {
    const { main } = await loadLib()
    const { value, out } = await silence(() => main(cancelArgs()))
    expect(value).toBe(0)
    const db = readDb()
    expect(db.rows[0].state).toBe('cancelled')
    expect(db.rows[0].evidence_locator).toMatch(/^venturecrane\/engagements@[0-9a-f]{12}:/)
    expect(db.rows[0].disposition).toMatch(/^client withdrew the ask: venturecrane\/engagements@/)
    const update = db.sql.find((s) => /^UPDATE/i.test(s)) ?? ''
    expect(update).toContain("SET state = 'cancelled'")
    expect(update).not.toContain("'delivered'")
    expect(update).toContain("evidence_class = 'attested'")
    expect(update).toContain("AND state = 'open'")
    expect(out).toContain('cancelled acme-fixture/deliverable/marketing-terms')
  })

  it('fails when the write does not land, even though the UPDATE "succeeded"', async () => {
    const { main } = await loadLib()
    process.env.CANCEL_DROP_WRITE = '1'
    const { value, err } = await silence(() => main(cancelArgs()))
    expect(value).toBe(1)
    expect(err).toMatch(/did not land/)
    expect(readDb().rows[0].state).toBe('open')
  })

  it('refuses an unarchived letter and writes nothing', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() => main(cancelArgs({ evidence: DRAFT })))
    expect(value).toBe(1)
    expect(err).toMatch(/evidence_not_on_main/)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('refuses the promise letter as evidence and writes nothing', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() =>
      main(cancelArgs({ evidence: PROMISE, quote: 'nail down the scope with you first, then' }))
    )
    expect(value).toBe(1)
    expect(err).toMatch(/evidence_is_the_promise/)
    expect(readDb().rows[0].state).toBe('open')
  })

  it('requires every argument and says which is missing', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() =>
      main(['cancel', '--client', 'acme-fixture', '--key', 'marketing-terms'])
    )
    expect(value).toBe(1)
    expect(err).toMatch(/--evidence is required/)
  })
})
