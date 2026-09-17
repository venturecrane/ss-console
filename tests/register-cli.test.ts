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
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, readdirSync } from 'fs'
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
