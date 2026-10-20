/**
 * Three register changes approved 2026-10-20 (ADR 0088 amendment of that date):
 *
 *   1. `register retire` -- a row that was NEVER an obligation goes to `void`,
 *      with its reason stored, and stays there through the nightly import.
 *   2. No recurring row without a window, on every write path.
 *   3. `register await` -- a row waiting on someone else goes to
 *      `awaiting_external` on letter evidence, and such rows (and every
 *      `external_dependency` row) stop raising the undated aging alert.
 *
 * The database cases run the real migration chain on the test D1, so the SQL
 * the CLI ships is the SQL that is exercised, not a regex over it. The CLI
 * cases reuse register-reopen.test.ts's shape: a real bare origin for the
 * engagements archive and a JSON-file wrangler stand-in. All text is
 * synthetic: this repo is public.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import {
  getObligation,
  listOpenObligations,
  recurringWindowRefusal,
  upsertObligation,
  VALID_TRANSITIONS,
  type ObligationState,
  type UpsertObligationInput,
} from '../src/lib/db/obligations'
import { markAwaitingExternal, markRetired } from '../src/lib/db/obligation-capture'
import { classifyRow } from '../scripts/lib/obligation-reconcile/verdicts'
import type { Obligation } from '../src/lib/db/obligations'

const ORG = '01JQFK0000SMDSERVICES000'
const SLUG = 'acme-fixture'
const ENTITY = 'e-acme'

const ALL_STATES = Object.keys(VALID_TRANSITIONS) as ObligationState[]
const edgesInto = (to: ObligationState) =>
  ALL_STATES.filter((from) => VALID_TRANSITIONS[from].includes(to)).sort()

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, {
    files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
  })
  await db
    .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)`)
    .bind(ENTITY, ORG, 'Acme Fixture', SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (customer_slug, entity_id, org_id, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1', '[]', 'deadbeef', datetime('now'))`
    )
    .bind(SLUG, ENTITY, ORG)
    .run()
  return db
}

const input = (over: Partial<UpsertObligationInput> = {}): UpsertObligationInput => ({
  customer_slug: SLUG,
  entity_id: ENTITY,
  stable_key: 'widget-letter',
  kind: 'deliverable',
  what: 'Send the widget letter.',
  origin: 'captured',
  origin_source: 'letter',
  source_kind: 'letter',
  source_ref: 'operator/customers/acme-fixture/correspondence/30_x.md',
  source_quote: 'we will send you the widget letter next week',
  ...over,
})

// ------------------------------------------------------------- 1. retire

describe('retire: the state machine', () => {
  it('reaches void from every working state, and void has no way out', () => {
    // Before 2026-10-20 only open and parked could reach void, so a row noticed
    // as never-owed after work started could not be retired. Removing the new
    // edges fails this.
    expect(edgesInto('void')).toEqual(['active', 'awaiting_external', 'open', 'parked'])
    expect(VALID_TRANSITIONS.void).toEqual([])
  })

  it('RETIRABLE_FROM is exactly the edges into void', async () => {
    const { RETIRABLE_FROM } = await import('../.claude/hooks/lib/register-close.mjs')
    expect([...RETIRABLE_FROM].sort()).toEqual(edgesInto('void'))
  })
})

describe('retire: on the real schema', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('moves an open row to void, stores the reason, and reads it back', async () => {
    const id = await upsertObligation(db, input())
    const disposition = 'retired, never an obligation: a contract term every job checks'
    expect(await markRetired(db, { obligationId: id, fromState: 'open', disposition })).toEqual({
      ok: true,
    })
    const row = await getObligation(db, id)
    expect(row?.state).toBe('void')
    expect(row?.disposition).toBe(disposition)
    expect(row?.closed_at).toBeTruthy()
    expect((await listOpenObligations(db)).map((o) => o.obligation_id)).not.toContain(id)
  })

  it('a retired IMPORTED row stays retired when the nightly import restates it', async () => {
    const imported = input({
      stable_key: 'gh-123',
      kind: 'product_defect',
      origin: 'imported',
      origin_source: 'github',
      source_kind: 'github',
      source_ref: 'venturecrane/ss-console#123',
      source_quote: null,
    })
    const id = await upsertObligation(db, imported)
    await markRetired(db, { obligationId: id, fromState: 'open', disposition: 'retired: test' })
    // The importer's exact call, a second time: same identity, refreshed text.
    const again = await upsertObligation(db, { ...imported, what: 'Restated by the import.' })
    expect(again).toBe(id)
    expect((await getObligation(db, id))?.state).toBe('void')
    const n = await db
      .prepare(`SELECT COUNT(*) AS n FROM client_obligations WHERE stable_key = 'gh-123'`)
      .first<{ n: number }>()
    expect(n?.n).toBe(1)
  })

  it('refuses a settled row (delivered) and writes nothing', async () => {
    const id = await upsertObligation(db, input())
    await db
      .prepare(`UPDATE client_obligations SET state = 'delivered' WHERE obligation_id = ?`)
      .bind(id)
      .run()
    const r = await markRetired(db, { obligationId: id, fromState: 'delivered', disposition: 'x' })
    expect(r).toEqual({ ok: false, error: 'illegal_transition' })
    expect((await getObligation(db, id))?.state).toBe('delivered')
  })
})

describe('validateRetire', () => {
  const row = (over: Record<string, unknown> = {}) => ({
    obligation_id: 'o-1',
    customer_slug: SLUG,
    kind: 'recurring',
    stable_key: 'k',
    origin: 'captured',
    state: 'open',
    source_ref: 'x',
    ...over,
  })
  const WHY = 'A contract term, not work owed: every job checks it first.'

  it('accepts a working row with a real reason, captured or imported', async () => {
    const { validateRetire } = await import('../.claude/hooks/lib/register-close.mjs')
    expect(validateRetire(row(), WHY)).toEqual({ ok: true })
    expect(validateRetire(row({ origin: 'imported' }), WHY)).toEqual({ ok: true })
  })

  it('refuses a label in place of a reason', async () => {
    const { validateRetire } = await import('../.claude/hooks/lib/register-close.mjs')
    expect(validateRetire(row(), 'not needed').error).toBe('retire_needs_a_reason')
    expect(validateRetire(row(), '   ').error).toBe('retire_needs_a_reason')
  })

  it('refuses every settled state (falsifier: walks the machine)', async () => {
    const { validateRetire, RETIRABLE_FROM } =
      await import('../.claude/hooks/lib/register-close.mjs')
    const settled = ALL_STATES.filter((s) => !RETIRABLE_FROM.includes(s))
    expect(settled).toEqual(
      expect.arrayContaining(['delivered', 'verified', 'closed', 'cancelled'])
    )
    for (const state of settled) {
      expect(validateRetire(row({ state }), WHY), state).toMatchObject({
        ok: false,
        error: 'not_retirable_from_state',
      })
    }
  })
})

// ------------------------------------------------- 2. recurring needs a window

describe('no recurring row without a window', () => {
  it('the pure rule refuses either half missing and says why', () => {
    expect(recurringWindowRefusal({ kind: 'recurring' })).toMatch(
      /needs --window-start and --window-end.*can never be discharged/
    )
    expect(
      recurringWindowRefusal({ kind: 'recurring', window_start: '2026-10-01', window_end: null })
    ).not.toBeNull()
    expect(
      recurringWindowRefusal({
        kind: 'recurring',
        window_start: '2026-10-01',
        window_end: '2026-10-31',
      })
    ).toBeNull()
    // Scoped to recurring: a deliverable has no period and must not need one.
    expect(recurringWindowRefusal({ kind: 'deliverable' })).toBeNull()
  })

  it('upsertObligation refuses it, so no writer (CLI or importer) can add one', async () => {
    const db = await freshDb()
    await expect(
      upsertObligation(db, input({ kind: 'recurring', stable_key: 'r1' }))
    ).rejects.toThrow(/recurring_without_window/)
    const id = await upsertObligation(
      db,
      input({
        kind: 'recurring',
        stable_key: 'r2',
        window_start: '2026-10-01',
        window_end: '2026-10-31',
      })
    )
    expect((await getObligation(db, id))?.window_end).toBe('2026-10-31')
  })
})

// ---------------------------------------------- 3. await, and the alert scope

describe('the undated aging alert skips rows waiting on someone else', () => {
  const NOW = new Date('2026-10-20T12:00:00Z')
  const row = (over: Partial<Obligation> = {}) =>
    ({
      obligation_id: 'o-age',
      customer_slug: SLUG,
      entity_id: ENTITY,
      kind: 'deliverable',
      what: 'synthetic',
      state: 'open',
      origin: 'captured',
      due_at: null,
      created_at: '2026-09-18 00:00:00',
      evidence_class: null,
      ...over,
    }) as Obligation
  const absent = { status: 'absent' as const }

  it('an open captured row 32 days old is still stale (the alert is not simply off)', () => {
    expect(classifyRow(row(), absent, NOW)).toMatchObject({ verdict: 'stale' })
  })

  it('an awaiting_external row of the same age is not', () => {
    expect(classifyRow(row({ state: 'awaiting_external' }), absent, NOW)).toEqual({
      verdict: 'still_open',
    })
  })

  it('an external_dependency row is not, whatever its state', () => {
    expect(classifyRow(row({ kind: 'external_dependency' }), absent, NOW)).toEqual({
      verdict: 'still_open',
    })
  })

  it('a DATED awaiting_external row past due still goes overdue', () => {
    const dated = row({ state: 'awaiting_external', due_at: '2026-10-01', date_quote: 'by Oct 1' })
    expect(classifyRow(dated, absent, NOW)).toMatchObject({ verdict: 'overdue' })
  })
})

describe('await: the state machine and the schema', () => {
  it('AWAITABLE_FROM is exactly the edges into awaiting_external', async () => {
    const { AWAITABLE_FROM } = await import('../.claude/hooks/lib/register-close.mjs')
    expect([...AWAITABLE_FROM].sort()).toEqual(edgesInto('awaiting_external'))
  })

  it('moves open -> awaiting_external without a receipt the reconciler would certify', async () => {
    const db = await freshDb()
    const id = await upsertObligation(db, input())
    const disposition = 'awaiting external: "the quote follows those" (x@abc:y.md)'
    expect(
      await markAwaitingExternal(db, { obligationId: id, fromState: 'open', disposition })
    ).toEqual({ ok: true })
    const after = await getObligation(db, id)
    expect(after?.state).toBe('awaiting_external')
    expect(after?.disposition).toBe(disposition)
    // An attested receipt would probe `present` and certify work nobody did.
    expect(after?.evidence_locator).toBeNull()
    expect(after?.evidence_last_verified_at).toBeNull()
  })
})

// ------------------------------------------------------- the CLI, end to end

const DIR = 'operator/customers/acme-fixture/correspondence'
const PROMISE = `${DIR}/30_scott-to-client_quote-after-your-answers.md`
const STATUS = `${DIR}/31_scott-to-client_where-things-stand.md`
const PROMISE_TEXT = 'We will send the widget quote once you return the three answers.\n'
const STATUS_TEXT =
  'Next steps: the inventory (You), the answers (You). The quote follows those. (Us)\n'
const AWAIT_QUOTE = 'the answers (You). The quote follows those.'

const D1_STUB = `#!/usr/bin/env node
const fs = require('fs')
const path = process.env.RA_DB
const db = JSON.parse(fs.readFileSync(path, 'utf8'))
const sql = process.argv[3] || ''
const out = (results) => process.stdout.write(JSON.stringify([{ results, success: true }]))
db.sql.push(sql)
if (/^UPDATE/i.test(sql)) {
  if (process.env.RA_DROP_WRITE !== '1') {
    const state = /SET state = '([a-z_]+)'/.exec(sql)[1]
    const disp = /disposition = '((?:[^']|'')+)'/.exec(sql)
    for (const r of db.rows) {
      r.state = state
      if (disp) r.disposition = disp[1].replace(/''/g, "'")
    }
  }
  fs.writeFileSync(path, JSON.stringify(db))
  return out([])
}
fs.writeFileSync(path, JSON.stringify(db))
out(db.rows)
`

let dir: string
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

const cliRow = (over: Record<string, unknown> = {}) => ({
  obligation_id: 'o-9',
  customer_slug: SLUG,
  kind: 'deliverable',
  stable_key: 'widget-quote',
  origin: 'captured',
  state: 'open',
  source_ref: PROMISE,
  evidence_locator: null,
  disposition: null,
  ...over,
})
const setRows = (rows: Record<string, unknown>[]) =>
  writeFileSync(dbState, JSON.stringify({ rows, sql: [] }))
const readDb = () =>
  JSON.parse(readFileSync(dbState, 'utf8')) as { rows: Record<string, unknown>[]; sql: string[] }
const argv = (cmd: string, a: Record<string, string>) => [
  cmd,
  ...Object.entries(a).flatMap(([k, v]) => [`--${k}`, v]),
]

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

describe('register retire / await / add, through the CLI', () => {
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'register-retire-await-'))
    const origin = join(dir, 'origin.git')
    const engagements = join(dir, 'engagements')
    git(dir, 'init', '--quiet', '--bare', '--initial-branch=main', origin)
    git(dir, 'clone', '--quiet', origin, engagements)
    git(engagements, 'config', 'user.email', 'test@example.com')
    git(engagements, 'config', 'user.name', 'test')
    mkdirSync(join(engagements, DIR), { recursive: true })
    writeFileSync(join(engagements, PROMISE), PROMISE_TEXT)
    writeFileSync(join(engagements, STATUS), STATUS_TEXT)
    git(engagements, 'add', '.')
    git(engagements, 'commit', '--quiet', '-m', 'archive letters')
    git(engagements, 'push', '--quiet', 'origin', 'HEAD:main')

    dbState = join(dir, 'db.json')
    const stub = join(dir, 'd1.cjs')
    writeFileSync(stub, D1_STUB)
    chmodSync(stub, 0o755)
    setRows([cliRow()])
    process.env.SS_ENGAGEMENTS_DIR = engagements
    process.env.SS_OBLIGATION_JOURNAL_DIR = join(dir, 'journal')
    process.env.SS_REGISTER_D1_CMD = stub
    process.env.RA_DB = dbState
  })

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
    for (const k of [
      'SS_ENGAGEMENTS_DIR',
      'SS_OBLIGATION_JOURNAL_DIR',
      'SS_REGISTER_D1_CMD',
      'RA_DB',
      'RA_DROP_WRITE',
    ]) {
      delete process.env[k]
    }
  })

  const loadLib = () => import('../.claude/hooks/lib/register.mjs')
  const WHY = 'A scheduled routine: work-health grades every run of it.'
  const journal = () => {
    const d = join(dir, 'journal')
    return execFileSync('cat', [join(d, `${new Date().toISOString().slice(0, 10)}.jsonl`)], {
      encoding: 'utf8',
    })
  }

  it('retire: void, the reason as disposition, journaled, refusing a short reason', async () => {
    const { main } = await loadLib()
    const short = await silence(() =>
      main(argv('retire', { client: SLUG, key: 'widget-quote', why: 'nope' }))
    )
    expect(short.value).toBe(1)
    expect(short.err).toMatch(/retire_needs_a_reason/)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)

    const { value, out } = await silence(() =>
      main(argv('retire', { client: SLUG, key: 'widget-quote', why: WHY }))
    )
    expect(value).toBe(0)
    expect(readDb().rows[0].state).toBe('void')
    expect(readDb().rows[0].disposition).toBe(`retired, never an obligation: ${WHY}`)
    expect(out).toContain('retired acme-fixture/deliverable/widget-quote (void)')
    const entry = JSON.parse(journal().trim().split('\n').pop() ?? '{}')
    expect(entry).toMatchObject({ kind: 'retired', key: 'widget-quote', why: WHY })
  })

  it('retire: fails when the write does not land', async () => {
    const { main } = await loadLib()
    process.env.RA_DROP_WRITE = '1'
    const { value, err } = await silence(() =>
      main(argv('retire', { client: SLUG, key: 'widget-quote', why: WHY }))
    )
    expect(value).toBe(1)
    expect(err).toMatch(/did not land/)
  })

  it('await: awaiting_external on a quote from the archived letter, locator + quote stored', async () => {
    const { main } = await loadLib()
    const { value } = await silence(() =>
      main(
        argv('await', { client: SLUG, key: 'widget-quote', evidence: STATUS, quote: AWAIT_QUOTE })
      )
    )
    expect(value).toBe(0)
    const row = readDb().rows[0]
    expect(row.state).toBe('awaiting_external')
    expect(String(row.disposition)).toMatch(
      new RegExp(
        `^awaiting external: "${AWAIT_QUOTE.replace(/[().]/g, '\\$&')}" \\(venturecrane/engagements@[0-9a-f]{12}:`
      )
    )
    const update = readDb().sql.find((s) => /^UPDATE/i.test(s)) ?? ''
    expect(update).not.toMatch(/evidence_/)
  })

  it('await: refuses a quote the letter does not hold, and an imported row', async () => {
    const { main } = await loadLib()
    const bad = await silence(() =>
      main(
        argv('await', {
          client: SLUG,
          key: 'widget-quote',
          evidence: STATUS,
          quote: 'we already sent the widget quote to you',
        })
      )
    )
    expect(bad.value).toBe(1)
    expect(bad.err).toMatch(/quote_not_found/)
    setRows([cliRow({ origin: 'imported' })])
    const imp = await silence(() =>
      main(
        argv('await', { client: SLUG, key: 'widget-quote', evidence: STATUS, quote: AWAIT_QUOTE })
      )
    )
    expect(imp.value).toBe(1)
    expect(imp.err).toMatch(/imported_rows_follow_their_source/)
    expect(readDb().sql.some((s) => /^UPDATE/i.test(s))).toBe(false)
  })

  it('add --kind recurring without a window is refused, and the refusal says why', async () => {
    const { main } = await loadLib()
    const { value, err } = await silence(() =>
      main(
        argv('add', {
          client: SLUG,
          kind: 'recurring',
          key: 'weekly-widget',
          what: 'Send the widget report every week.',
          source: PROMISE,
          quote: 'We will send the widget quote once you return',
        })
      )
    )
    expect(value).toBe(1)
    expect(err).toMatch(/recurring_without_window/)
    expect(err).toMatch(/owed per period/)
  })
})
