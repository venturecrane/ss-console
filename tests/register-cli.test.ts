/**
 * The obligation capture CLI's grounding gate (ADR 0088).
 *
 * The gate's whole job is to REFUSE. So the cases that matter are the refusals,
 * and the falsifier for each is its mirror: a gate that refused everything
 * would pass every rejection test while being useless, so each rejection is
 * paired with an acceptance that must still work.
 *
 * The hardest case, and the reason normalization exists: a quote copied out of
 * a markdown letter arrives with smart quotes, line wrapping and emphasis
 * markers that the source does not literally contain in that form. If those
 * were refused, agents would stop quoting accurately and start quoting
 * whatever passed — and the gate would have made grounding worse.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
  readFileSync,
  readdirSync,
  chmodSync,
} from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const LETTER = `# Letter 26 — Scott to Christa, 2026-08-14

We reviewed the task templates on the twelve matters you flagged.

We will clean up the duplicated task set on your matters, and we will confirm
with you before anything is deleted. The chronology package runs **once per
billing cycle from the 15th**, and we'll send the summary the same day.

Nothing here changes the fee.
`

let dir: string
let journal: string
let engagements: string

async function loadLib() {
  return await import('../.claude/hooks/lib/register.mjs')
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'register-test-'))
  journal = join(dir, 'journal')
  engagements = join(dir, 'engagements')
  mkdirSync(join(engagements, 'operator/customers/ashton-price/correspondence'), {
    recursive: true,
  })
  writeFileSync(
    join(engagements, 'operator/customers/ashton-price/correspondence/26_letter.md'),
    LETTER
  )
  process.env.SS_OBLIGATION_JOURNAL_DIR = journal
  process.env.SS_ENGAGEMENTS_DIR = engagements
})

afterEach(() => {
  rmSync(dir, { recursive: true, force: true })
  delete process.env.SS_OBLIGATION_JOURNAL_DIR
  delete process.env.SS_ENGAGEMENTS_DIR
})

const SOURCE = 'operator/customers/ashton-price/correspondence/26_letter.md'

function capture(overrides: Record<string, unknown> = {}) {
  return {
    client: 'ashton-price',
    kind: 'deliverable',
    key: 'smokeball-task-cleanup',
    what: 'Clean up the duplicated Smokeball task set.',
    source: SOURCE,
    quote: 'We will clean up the duplicated task set on your matters',
    ...overrides,
  }
}

describe('quote normalization', () => {
  it('matches across line wrapping, smart quotes, and emphasis markers', async () => {
    const { normalize } = await loadLib()
    expect(normalize('runs **once per\nbilling cycle** from the 15th')).toBe(
      normalize('runs once per billing cycle from the 15th')
    )
    expect(normalize('the firm’s matters')).toBe(normalize("the firm's matters"))
    expect(normalize('a — b')).toBe(normalize('a - b'))
  })

  it('does not collapse a paraphrase into a match', async () => {
    // The falsifier for normalization: if it stripped punctuation and
    // stopwords, a reworded sentence would pass and the gate would be theatre.
    const { normalize } = await loadLib()
    expect(normalize('we will clean up the duplicated task set')).not.toBe(
      normalize('we will clean up duplicated tasks')
    )
  })
})

describe('the grounding gate', () => {
  it('accepts a quote that is verbatim in the source', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(capture())
    expect(result.ok).toBe(true)
  })

  it('accepts a quote whose only differences are wrapping and markdown', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(
      capture({ quote: 'The chronology package runs once per billing cycle from the 15th' })
    )
    expect(result.ok).toBe(true)
  })

  it('refuses a quote that is not in the source', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(
      capture({ quote: 'We will migrate your entire practice to a new system' })
    )
    expect(result.ok).toBe(false)
    expect(result.error).toBe('quote_not_found')
  })

  it('refuses a plausible paraphrase of a real sentence', async () => {
    // This is the actual threat model: not a fabricated obligation, but a
    // slightly-reworded one that reads true. ~1 statement in 6 from an
    // extractor is unsupported, and they look like this.
    const { validateCapture } = await loadLib()
    const result: { ok: boolean; error?: string; nearest?: string } = validateCapture(
      capture({ quote: 'We will remove the duplicate tasks from all of your matters' })
    )
    expect(result.ok).toBe(false)
    expect(result.error).toBe('quote_not_found')
    // The nearest-window hint is part of the contract: a refusal the agent
    // cannot act on gets worked around rather than fixed.
    expect(result.nearest).toBeTruthy()
  })

  it('refuses a quote too short to mean anything', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(capture({ quote: 'we will' }))
    expect(result.ok).toBe(false)
    expect(result.error).toBe('quote_too_short')
  })

  it('fails closed when the engagements repo is not checked out', async () => {
    // "Cannot evaluate" must never read as "permitted" — the same
    // discriminator the Law 2 guard uses.
    process.env.SS_ENGAGEMENTS_DIR = join(dir, 'nonexistent')
    const { validateCapture } = await loadLib()
    const result = validateCapture(capture())
    expect(result.ok).toBe(false)
    expect(result.error).toBe('engagements_repo_absent')
  })

  it('refuses a missing source file rather than skipping the check', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(
      capture({ source: 'operator/customers/ashton-price/correspondence/99_absent.md' })
    )
    expect(result.ok).toBe(false)
    expect(result.error).toBe('source_not_found')
  })
})

describe('dated obligations', () => {
  it('refuses a due date with no date quote', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(capture({ due: '2026-10-15' }))
    expect(result.ok).toBe(false)
    expect(result.error).toBe('due_without_date_quote')
  })

  it('refuses a date quote that does not contain the date', async () => {
    // The interpretation gap: the quote is real, the date is invented. Only
    // dated rows alarm, so this is the path that would otherwise email the
    // Captain about work nobody owes.
    const { validateCapture } = await loadLib()
    const result = validateCapture(
      capture({
        due: '2026-11-30',
        'date-quote': 'We will clean up the duplicated task set on your matters',
      })
    )
    expect(result.ok).toBe(false)
    expect(result.error).toBe('date_quote_lacks_date')
  })

  it('accepts a date quote that states the recurring anchor', async () => {
    const { validateCapture } = await loadLib()
    const result = validateCapture(
      capture({
        kind: 'recurring',
        key: 'chronology-cycle',
        due: 'the 15th',
        'date-quote': 'runs once per billing cycle from the 15th',
      })
    )
    expect(result.ok).toBe(true)
  })
})

describe('validation', () => {
  it('refuses an unknown kind', async () => {
    const { validateCapture } = await loadLib()
    expect(validateCapture(capture({ kind: 'todo' })).error).toBe('unknown_kind')
  })

  it('names the missing field', async () => {
    const { validateCapture } = await loadLib()
    expect(validateCapture(capture({ key: undefined })).error).toBe('missing_key')
  })
})

describe('the journal', () => {
  it('records the considered pass so a deliberate nothing is not a silent miss', async () => {
    const { main } = await loadLib()
    const code = main([
      'add',
      '--kind',
      'none',
      '--client',
      'ashton-price',
      '--why',
      'letter was a status update',
    ])
    expect(code).toBe(0)

    const files = readdirSync(journal)
    const lines = readFileSync(join(journal, files[0]), 'utf8').trim().split('\n')
    const entry = JSON.parse(lines[0])
    expect(entry.kind).toBe('none')
    expect(entry.why).toBe('letter was a status update')
  })

  it('refuses a considered pass with no reason', async () => {
    const { main } = await loadLib()
    expect(main(['add', '--kind', 'none', '--client', 'ashton-price'])).toBe(1)
  })

  it('journals before the remote write, so an unreachable D1 loses nothing', async () => {
    // The whole reason the journal exists. With no D1 command available the
    // add still exits 0 and the row is on disk, recoverable by `register sync`.
    const { main, unsyncedEntries } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = join(dir, 'no-such-binary')
    const code = main([
      'add',
      '--client',
      'ashton-price',
      '--kind',
      'deliverable',
      '--key',
      'smokeball-task-cleanup',
      '--what',
      'Clean up the duplicated task set.',
      '--source',
      SOURCE,
      '--quote',
      'We will clean up the duplicated task set on your matters',
    ])
    delete process.env.SS_REGISTER_D1_CMD

    expect(code).toBe(0)
    const pending = unsyncedEntries()
    expect(pending).toHaveLength(1)
    expect(pending[0].key).toBe('smokeball-task-cleanup')
  })

  it('builds an upsert that refreshes without resetting state', async () => {
    const { buildUpsertSql } = await loadLib()
    const sql = buildUpsertSql({
      obligation_id: 'o1',
      customer_slug: 'ashton-price',
      entity_id: 'e1',
      stable_key: 'k',
      kind: 'deliverable',
      what: "the firm's task set",
      origin: 'captured',
      origin_source: 'letter',
      source_kind: 'letter',
      source_ref: SOURCE,
      source_quote: 'q',
      date_quote: null,
      window_start: null,
      window_end: null,
      due_at: null,
      links_json: null,
      created_by_session: null,
    })
    expect(sql).toContain('ON CONFLICT(customer_slug, kind, stable_key) DO UPDATE SET')
    expect(sql).not.toContain('state =')
    // An apostrophe in client prose must not break out of the literal.
    expect(sql).toContain("'the firm''s task set'")
  })
})

describe('regressions found in review', () => {
  it('syncs the LATEST unsynced write for a key, not the first', async () => {
    // An append-only journal means last-wins. The original dedupe only accepted
    // an overwrite when the incoming entry was synced, so correcting an
    // obligation while still offline silently lost the correction: sync wrote
    // the stale row and then marked the key done.
    const { journalAppend, unsyncedEntries } = await loadLib()
    const base = { client: 'ashton-price', kind: 'deliverable', key: 'k', synced: false }
    journalAppend({ ...base, ts: '2026-09-17T01:00:00Z', row: { what: 'Old text' } })
    journalAppend({ ...base, ts: '2026-09-17T02:00:00Z', row: { what: 'Corrected text' } })

    const pending = unsyncedEntries()
    expect(pending).toHaveLength(1)
    expect(pending[0].row.what).toBe('Corrected text')
  })

  it('stops reporting a key once its sync is journaled', async () => {
    // Falsifier for the rule above: if last-wins ignored the synced marker,
    // every key would look pending forever.
    const { journalAppend, unsyncedEntries } = await loadLib()
    const base = { client: 'ashton-price', kind: 'deliverable', key: 'k' }
    journalAppend({ ...base, ts: '2026-09-17T01:00:00Z', synced: false, row: { what: 'x' } })
    journalAppend({ ...base, ts: '2026-09-17T02:00:00Z', synced: true, row: { what: 'x' } })

    expect(unsyncedEntries()).toHaveLength(0)
  })

  it('accepts a letter that states the due date in ordinary English', async () => {
    // The gate must be strict about whether the date is ANCHORED in the source,
    // never about the client's formatting. Requiring the ISO string verbatim
    // refused exactly the well-grounded rows this exists to admit.
    const { dateQuoteAnchorsDate } = await loadLib()
    for (const quote of [
      'we will file before October 15th',
      'due October 15, 2026',
      'by 15 October',
      'on 10/15/2026',
      'the deadline is 2026-10-15',
    ]) {
      expect(dateQuoteAnchorsDate(quote, '2026-10-15')).toBe(true)
    }
  })

  it('still refuses a date quote that anchors a different date', async () => {
    // Falsifier: if the forms matched loosely, an invented date would pass and
    // the Captain would be paged about work nobody owes.
    const { dateQuoteAnchorsDate } = await loadLib()
    expect(dateQuoteAnchorsDate('we will file before October 15th', '2026-11-30')).toBe(false)
    expect(dateQuoteAnchorsDate('no date at all in this sentence', '2026-10-15')).toBe(false)
  })
})

/**
 * Reading the register (2026-09-17).
 *
 * `list` had no test at all until now: it echoed wrangler's raw stdout, so there
 * was nothing to assert beyond "a subprocess ran". It now parses and projects,
 * and the projection is a CONFIDENTIALITY BOUNDARY rather than a formatting
 * choice -- `--json` feeds /sos, whose output lands in a session transcript, and
 * transcripts persist under ~/.claude and go to model providers while this repo
 * is public. The allowlist test is the guard; adding `what` to the projection is
 * the mutation that must break it.
 */
describe('reading the register', () => {
  /** A wrangler stand-in that prints a canned envelope, ignoring its argv. */
  function successStub(rows: Record<string, unknown>[], shape: 'array' | 'bare' = 'array'): string {
    const envelope =
      shape === 'array'
        ? JSON.stringify([{ results: rows, success: true, meta: { served_by: 'test' } }])
        : JSON.stringify({ results: rows, success: true })
    const path = join(dir, shape === 'array' ? 'd1-ok-array.cjs' : 'd1-ok-bare.cjs')
    writeFileSync(path, `#!/usr/bin/env node\nprocess.stdout.write(${JSON.stringify(envelope)})\n`)
    chmodSync(path, 0o755)
    return path
  }

  const row = (over: Record<string, unknown> = {}) => ({
    obligation_id: 'o1',
    customer_slug: 'ashton-price',
    kind: 'deliverable',
    stable_key: 'legacy-migration-quote',
    state: 'open',
    due_at: null,
    created_at: new Date(Date.now() - 12 * 86400000).toISOString().replace('T', ' ').slice(0, 19),
    what: 'Reconcile the synthetic widget ledger for the fixture firm',
    ...over,
  })

  function capture(): { lines: string[]; restore: () => void } {
    const lines: string[] = []
    const original = console.log
    console.log = (...args: unknown[]) => void lines.push(args.join(' '))
    return { lines, restore: () => void (console.log = original) }
  }

  it('--json emits only allowlisted fields, never the obligation text', async () => {
    const { main, JSON_FIELDS } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = successStub([row()])
    const out = capture()
    const code = main(['list', '--json'])
    out.restore()
    delete process.env.SS_REGISTER_D1_CMD

    expect(code).toBe(0)
    const parsed = JSON.parse(out.lines.join('')) as Record<string, unknown>[]
    expect(parsed).toHaveLength(1)
    // The mutation this catches: adding `what` to projectRow.
    expect(Object.keys(parsed[0]).sort()).toEqual([...JSON_FIELDS].sort())
    expect(out.lines.join('')).not.toContain('synthetic widget ledger')
  })

  it('--json rolls an SMD-owned seat up to the smd-services client', async () => {
    const { main } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = successStub([row({ customer_slug: 'pilot-smokeball' })])
    const out = capture()
    main(['list', '--json'])
    out.restore()
    delete process.env.SS_REGISTER_D1_CMD

    const parsed = JSON.parse(out.lines.join('')) as Record<string, unknown>[]
    expect(parsed[0].client).toBe('smd-services')
    expect(parsed[0].customer_slug).toBe('pilot-smokeball')
  })

  it('reads the bare-object envelope as well as the array-wrapped one', async () => {
    // Wrangler emits both shapes. Before the parser was shared, lookupEntityId
    // handled both and cmdList handled neither; re-forking the parse fails here.
    const { main } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = successStub([row()], 'bare')
    const out = capture()
    const code = main(['list', '--json'])
    out.restore()
    delete process.env.SS_REGISTER_D1_CMD

    expect(code).toBe(0)
    expect(JSON.parse(out.lines.join(''))).toHaveLength(1)
  })

  it('says "nothing open" rather than printing an empty table', async () => {
    // A broken selector and a clean register look identical in a zero-row
    // table. That confusion is why the cadence engine sat at 7 of 16 overdue
    // behind a green report.
    const { main } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = successStub([])
    const out = capture()
    const code = main(['list'])
    out.restore()
    delete process.env.SS_REGISTER_D1_CMD

    expect(code).toBe(0)
    expect(out.lines.join('\n')).toMatch(/nothing open/)
  })

  it('the table groups by client, dates the age, and flags an all-undated register', async () => {
    const { renderTable } = await loadLib()
    const text = renderTable([row(), row({ obligation_id: 'o2', customer_slug: 'smd-staging' })])
    expect(text).toMatch(/ashton-price\s+\(1\)/)
    expect(text).toMatch(/smd-services\s+\(1\)/)
    expect(text).toMatch(/12d old/)
    expect(text).toMatch(/2 open across 2 clients; 0 dated/)
    // Only a dated row can go overdue, so an all-undated register cannot alarm
    // through that path. Saying so is the point.
    expect(text).toMatch(/none can go overdue/)
  })

  it('never reports a row as younger than new', async () => {
    // SQLite writes UTC without a zone marker; JS parses that as local, which
    // printed "-1d old" for a row created minutes earlier on a UTC-7 machine.
    const { renderTable } = await loadLib()
    const justNow = new Date().toISOString().replace('T', ' ').slice(0, 19)
    expect(renderTable([row({ created_at: justNow })])).toMatch(/0d old/)
  })

  it('reports WHY the register could not be read', async () => {
    // "cannot read" with no reason is indistinguishable from an empty register
    // at a glance, and /sos has to tell the two apart.
    const { main } = await loadLib()
    process.env.SS_REGISTER_D1_CMD = join(dir, 'no-such-binary')
    const errs: string[] = []
    const original = console.error
    console.error = (...args: unknown[]) => void errs.push(args.join(' '))
    const code = main(['list'])
    console.error = original
    delete process.env.SS_REGISTER_D1_CMD

    expect(code).toBe(1)
    expect(errs.join(' ')).toMatch(/cannot read the register \(.+\)/)
  })

  it('attributes a capture to the session the harness actually names', async () => {
    // register.mjs read CLAUDE_SESSION_ID, which is never set; the harness
    // exports CLAUDE_CODE_SESSION_ID. Every one of the register's first eleven
    // production rows carries created_by_session = NULL because of it.
    const { sessionId } = await loadLib()
    const prior = process.env.CLAUDE_CODE_SESSION_ID
    process.env.CLAUDE_CODE_SESSION_ID = 'sess-abc'
    expect(sessionId()).toBe('sess-abc')
    if (prior === undefined) delete process.env.CLAUDE_CODE_SESSION_ID
    else process.env.CLAUDE_CODE_SESSION_ID = prior
  })
})
