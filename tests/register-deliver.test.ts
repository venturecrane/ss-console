/**
 * `register deliver` -- the half of the obligation register that closes a
 * letter-stated promise (ADR 0088).
 *
 * Found 2026-09-19: every captured row in production had a NULL evidence
 * pointer and no command could set one, so a promise kept in a sent letter
 * stayed `open` forever. The command's job is to let exactly the right proof
 * through, so each refusal below is paired with the acceptance it must not
 * break: a gate that refused everything would pass every refusal test.
 *
 * The archive is a REAL git repo pushed to a real (local, bare) origin, because
 * the claim under test is "the letter is on origin/main", and a stub cannot
 * tell a merged letter from one sitting in a working tree.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { VALID_TRANSITIONS } from '../src/lib/db/obligations'

const DIR = 'operator/customers/acme-fixture/correspondence'
const PROMISE = `${DIR}/10_scott-to-client_we-will-send-the-results.md`
const KEPT = `${DIR}/11_scott-to-client_the-results.md`
const DRAFT = `${DIR}/12_scott-to-client_unsent-draft.md`

const PROMISE_TEXT = 'We will run the widget test and come back to you with the results.\n'
const KEPT_TEXT =
  'The widget test is done. Here are the results: all five cases passed, and nothing was changed.\n'
const QUOTE = 'Here are the results: all five cases passed'

let dir: string
let engagements: string
let origin: string
let dbState: string

/**
 * GIT_* stripped and hooks off. Under `git push` the pre-push hook exports
 * GIT_DIR / GIT_INDEX_FILE for THIS repo, and they win over `cwd`: the first
 * draft of this fixture committed "archive letters" into the ss-console branch
 * being pushed (a commit deleting the whole tree) and tried to push it to main.
 */
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
 * A wrangler stand-in backed by a JSON file: answers the row lookup, APPLIES
 * the delivery UPDATE (unless told to drop it), and answers the read-back from
 * what it holds. That last part is what lets a test prove the read-back reads.
 */
const D1_STUB = `#!/usr/bin/env node
const fs = require('fs')
const path = process.env.DELIVER_DB
const db = JSON.parse(fs.readFileSync(path, 'utf8'))
const sql = process.argv[3] || ''
const out = (results) => process.stdout.write(JSON.stringify([{ results, success: true }]))
db.sql.push(sql)
if (/^UPDATE/i.test(sql)) {
  if (process.env.DELIVER_DROP_WRITE !== '1') {
    const loc = /evidence_locator = '([^']+)'/.exec(sql)[1]
    for (const r of db.rows) { r.state = 'delivered'; r.evidence_locator = loc }
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
  obligation_id: 'o-1',
  customer_slug: 'acme-fixture',
  kind: 'deliverable',
  stable_key: 'widget-test-results',
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
  dir = mkdtempSync(join(tmpdir(), 'register-deliver-'))
  origin = join(dir, 'origin.git')
  engagements = join(dir, 'engagements')
  git(dir, 'init', '--quiet', '--bare', '--initial-branch=main', origin)
  git(dir, 'clone', '--quiet', origin, engagements)
  git(engagements, 'config', 'user.email', 'test@example.com')
  git(engagements, 'config', 'user.name', 'test')
  mkdirSync(join(engagements, DIR), { recursive: true })
  writeFileSync(join(engagements, PROMISE), PROMISE_TEXT)
  writeFileSync(join(engagements, KEPT), KEPT_TEXT)
  git(engagements, 'add', '.')
  git(engagements, 'commit', '--quiet', '-m', 'archive letters')
  git(engagements, 'push', '--quiet', 'origin', 'HEAD:main')
  // Sitting in the working tree, never pushed: the unsent draft.
  writeFileSync(join(engagements, DRAFT), KEPT_TEXT)

  dbState = join(dir, 'db.json')
  const stub = join(dir, 'd1.cjs')
  writeFileSync(stub, D1_STUB)
  chmodSync(stub, 0o755)
  setRows([openRow()])

  process.env.SS_ENGAGEMENTS_DIR = engagements
  process.env.SS_OBLIGATION_JOURNAL_DIR = join(dir, 'journal')
  process.env.SS_REGISTER_D1_CMD = stub
  process.env.DELIVER_DB = dbState
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  for (const k of [
    'SS_ENGAGEMENTS_DIR',
    'SS_OBLIGATION_JOURNAL_DIR',
    'SS_REGISTER_D1_CMD',
    'DELIVER_DB',
    'DELIVER_DROP_WRITE',
  ]) {
    delete process.env[k]
  }
})

const deliverArgs = (over: Record<string, string> = {}) => {
  const a = {
    client: 'acme-fixture',
    key: 'widget-test-results',
    evidence: KEPT,
    quote: QUOTE,
    ...over,
  }
  return ['deliver', ...Object.entries(a).flatMap(([k, v]) => [`--${k}`, v])]
}

describe('the states a row can be delivered from', () => {
  it('matches every state the state machine lets reach delivered', async () => {
    const { DELIVERABLE_FROM } = await loadLib()
    const fromMachine = Object.entries(VALID_TRANSITIONS)
      .filter(([, to]) => to.includes('delivered'))
      .map(([from]) => from)
    expect([...DELIVERABLE_FROM].sort()).toEqual(fromMachine.sort())
  })
})

describe('reading the archive', () => {
  it('reads a letter on origin/main and pins the locator to that commit', async () => {
    const { readArchivedLetter } = await loadLib()
    const letter = readArchivedLetter(KEPT)
    const sha = git(engagements, 'rev-parse', 'origin/main').trim().slice(0, 12)
    expect(letter.ok).toBe(true)
    expect(letter.locator).toBe(`venturecrane/engagements@${sha}:${KEPT}`)
  })

  it('reads the engagements repo even when a git hook has exported GIT_DIR', async () => {
    // Inside any git hook, GIT_DIR names the hook's repository and beats `-C`.
    // Pointed at nothing, an unscrubbed read fails; pointed at a real repo, it
    // would read the wrong one. Either way the archive must still be the one read.
    const { readArchivedLetter } = await loadLib()
    process.env.GIT_DIR = join(dir, 'not-a-repo')
    try {
      expect(readArchivedLetter(KEPT).ok).toBe(true)
    } finally {
      delete process.env.GIT_DIR
    }
  })

  it('refuses a letter that exists on disk but was never pushed', async () => {
    const { readArchivedLetter } = await loadLib()
    expect(readArchivedLetter(DRAFT)).toMatchObject({ ok: false, error: 'evidence_not_on_main' })
  })

  it('refuses when the archive cannot be fetched, rather than reading a stale copy', async () => {
    const { readArchivedLetter } = await loadLib()
    rmSync(origin, { recursive: true, force: true })
    expect(readArchivedLetter(KEPT)).toMatchObject({ ok: false, error: 'engagements_unreachable' })
  })
})

describe('validateDelivery', () => {
  const letter = { ok: true, text: KEPT_TEXT, suffix: KEPT, locator: 'x' }

  it('accepts a captured open row, a different letter, and a quote from it', async () => {
    const { validateDelivery } = await loadLib()
    expect(validateDelivery(openRow(), letter, QUOTE)).toEqual({ ok: true })
  })

  it('refuses an imported row: those close from their own source', async () => {
    const { validateDelivery } = await loadLib()
    expect(validateDelivery(openRow({ origin: 'imported' }), letter, QUOTE).error).toBe(
      'imported_rows_close_from_their_source'
    )
  })

  it('refuses a row that is not in a working state', async () => {
    const { validateDelivery } = await loadLib()
    for (const state of ['delivered', 'verified', 'parked']) {
      expect(validateDelivery(openRow({ state }), letter, QUOTE).error).toBe(
        'not_deliverable_from_state'
      )
    }
  })

  it('refuses the letter that made the promise as proof it was kept', async () => {
    const { validateDelivery } = await loadLib()
    const promise = { ok: true, text: PROMISE_TEXT, suffix: PROMISE, locator: 'x' }
    const quote = 'come back to you with the results'
    expect(
      validateDelivery(openRow(), promise, `We will run the widget test and ${quote}`).error
    ).toBe('evidence_is_the_promise')
  })

  it('refuses a quote that is not in the delivery letter', async () => {
    const { validateDelivery } = await loadLib()
    const r = validateDelivery(openRow(), letter, 'Here are the results: all six cases passed')
    expect(r.error).toBe('quote_not_found')
  })
})

describe('register deliver, end to end', () => {
  it('delivers, writes the receipt, and reads it back', async () => {
    const { main } = await loadLib()
    const { value, out } = await silence(() => main(deliverArgs()))
    expect(value).toBe(0)
    const db = readDb()
    expect(db.rows[0].state).toBe('delivered')
    expect(db.rows[0].evidence_locator).toMatch(/^venturecrane\/engagements@[0-9a-f]{12}:/)
    const update = db.sql.find((s) => /^UPDATE/i.test(s)) ?? ''
    expect(update).toContain("evidence_class = 'attested'")
    expect(update).toContain("evidence_surface = 'engagements'")
    // Guarded on the state that was read, so a concurrent move is not clobbered.
    expect(update).toContain("AND state = 'open'")
    expect(out).toContain('delivered acme-fixture/deliverable/widget-test-results')
  })

  it('fails when the write does not land, even though the UPDATE "succeeded"', async () => {
    // An UPDATE matching nothing exits 0. Without the read-back this printed
    // "delivered" over a row that still read open.
    const { main } = await loadLib()
    process.env.DELIVER_DROP_WRITE = '1'
    const { value, err } = await silence(() => main(deliverArgs()))
    expect(value).toBe(1)
    expect(err).toMatch(/did not land/)
    expect(readDb().rows[0].state).toBe('open')
  })

  it('refuses an unpushed letter and writes nothing', async () => {
    const { main } = await loadLib()
    const { value } = await silence(() => main(deliverArgs({ evidence: DRAFT })))
    expect(value).toBe(1)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('refuses a key that names more than one row', async () => {
    const { main } = await loadLib()
    setRows([openRow(), openRow({ obligation_id: 'o-2', kind: 'request' })])
    const { value, err } = await silence(() => main(deliverArgs()))
    expect(value).toBe(1)
    expect(err).toMatch(/ambiguous_key/)
  })
})

describe('register list', () => {
  it('no longer counts a verified row as owed', async () => {
    const { main } = await loadLib()
    setRows([])
    await silence(() => main(['list', '--json']))
    expect(readDb().sql[0]).toMatch(/state NOT IN \('verified','closed','cancelled','void'\)/)
  })
})
