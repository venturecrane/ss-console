#!/usr/bin/env node
/**
 * register.mjs -- the obligation capture CLI (ADR 0088, migration 0117).
 *
 * WHAT THIS IS FOR. Most of the register is IMPORTED: the reconciler derives
 * rows each run from GitHub, fleet_alert_state, the medchron ledger and the
 * change-request table, because a source a machine can enumerate is a source
 * that cannot be forgotten. This CLI covers the one class no source exposes --
 * an obligation SMD stated in a letter to a client. Those live in prose in a
 * private repo, and prose is exactly where the 2026 audit found a client
 * commitment sitting unread for months (the Smokeball task cleanup, stated in
 * correspondence letters 26/27/28 and tracked nowhere).
 *
 * THE GROUNDING GATE IS THE POINT. Extraction faithfulness tops out around
 * 0.83 -- roughly one statement in six is unsupported by its source. No amount
 * of prompting fixes that, so the control is mechanical instead: every row
 * carries a verbatim quote, the quote is string-matched against the source
 * file, and a row whose quote is not found is REFUSED. There is no --force.
 * The same discipline legal-extraction work uses, and the same one this repo
 * already applies to routine-grid.yaml's *_verbatim fields.
 *
 * It grounds the CITATION, never the INTERPRETATION. A hallucinated `what`
 * attached to a real quote still passes, which is why only DATED obligations
 * alarm and why a date needs its own quote containing it (schema CHECK). The
 * residual error stays on a page the Captain reads deliberately rather than
 * arriving in his inbox.
 *
 * JOURNAL FIRST, THEN D1. The local append-only journal is written before the
 * remote write and stamped after it. A session with no network still records
 * the obligation; `register sync` replays it. The alternative -- write to a
 * file the PR carries -- was rejected: a row that lives only in a branch that
 * never merges is a silently lost obligation, which is the failure this whole
 * register exists to end.
 *
 * Exit codes: 0 ok (including "journaled but unsynced"), 1 refused.
 *
 * Env: SS_OBLIGATION_JOURNAL_DIR (default ~/.claude/ss-obligation-journal)
 *      SS_REGISTER_D1_CMD        override the wrangler invocation (tests)
 *      SS_REGISTER_DB            D1 database name (default ss-console-db)
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import crypto from 'node:crypto'
import { engagementsDir, engagementsRepoPresent, suffixOf } from './engagement-paths.mjs'

const DB = process.env.SS_REGISTER_DB || 'ss-console-db'
const MIN_QUOTE_WORDS = 6

export const KINDS = [
  'request',
  'deliverable',
  'recurring',
  'renewal',
  'incident',
  'external_dependency',
  'config_ops',
  'provisioning',
  'product_defect',
]

export function journalDir() {
  return (
    process.env.SS_OBLIGATION_JOURNAL_DIR ||
    path.join(os.homedir(), '.claude', 'ss-obligation-journal')
  )
}

/**
 * Normalize for comparison.
 *
 * Both sides get the same treatment, so a quote that differs from its source
 * only in line wrapping, smart quotes, or markdown emphasis still matches. A
 * quote spanning several lines matches for free: the newline has already
 * become a space by the time we compare.
 *
 * Deliberately NOT stripping punctuation wholesale -- a quote that matches
 * only after its commas are removed is a paraphrase, and paraphrase is exactly
 * what this gate exists to reject.
 */
export function normalize(text) {
  return String(text)
    .normalize('NFKC')
    .replace(/[‘’‛′]/g, "'")
    .replace(/[“”‟″]/g, '"')
    .replace(/[‐-―−]/g, '-')
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

export function wordCount(text) {
  const t = normalize(text)
  return t.length === 0 ? 0 : t.split(' ').length
}

/**
 * Resolve a source reference to readable text.
 *
 * Letters live in the private engagements repo, which may not be cloned on
 * this machine. That case FAILS CLOSED, matching the Law 2 guard's
 * discriminator: "cannot evaluate" must never read as "permitted". A register
 * that silently accepts ungrounded rows on an incomplete checkout is worse
 * than one that refuses them.
 */
export function resolveSource(sourceKind, sourceRef, opts = {}) {
  const cwd = opts.cwd || process.cwd()
  if (sourceKind === 'letter') {
    if (!engagementsRepoPresent()) {
      return { ok: false, error: 'engagements_repo_absent' }
    }
    const suffix = suffixOf(sourceRef) || sourceRef
    const candidates = [
      path.resolve(cwd, sourceRef),
      path.join(engagementsDir(), suffix),
      path.join(engagementsDir(), sourceRef),
    ]
    for (const candidate of candidates) {
      try {
        if (fs.statSync(candidate).isFile()) {
          return { ok: true, text: fs.readFileSync(candidate, 'utf8'), resolved: candidate }
        }
      } catch {
        /* next candidate */
      }
    }
    return { ok: false, error: 'source_not_found' }
  }

  if (sourceKind === 'git') {
    // 'git:<sha>:<path>' -- the content as of that commit, so a later rewrite
    // of the file cannot retroactively ungroundize a row.
    const m = /^git:([0-9a-f]{7,40}):(.+)$/.exec(sourceRef)
    if (!m) return { ok: false, error: 'malformed_git_ref' }
    try {
      const text = execFileSync('git', ['show', `${m[1]}:${m[2]}`], {
        cwd,
        encoding: 'utf8',
        maxBuffer: 16 * 1024 * 1024,
      })
      return { ok: true, text, resolved: sourceRef }
    } catch {
      return { ok: false, error: 'source_not_found' }
    }
  }

  if (sourceKind === 'github') {
    const m = /^([\w.-]+\/[\w.-]+)#(\d+)$/.exec(sourceRef)
    if (!m) return { ok: false, error: 'malformed_github_ref' }
    try {
      const text = execFileSync(
        'gh',
        ['issue', 'view', m[2], '--repo', m[1], '--json', 'title,body', '-q', '.title + "\\n" + .body'],
        { cwd, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 }
      )
      return { ok: true, text, resolved: sourceRef }
    } catch {
      return { ok: false, error: 'source_not_found' }
    }
  }

  return { ok: false, error: 'unsupported_source_kind' }
}

/**
 * Is this quote actually in this source?
 *
 * On failure, returns the highest-overlap window from the source so the drift
 * is visible. An agent that mis-clipped a sentence by two words should be able
 * to see that immediately rather than guess.
 */
export function checkQuote(sourceText, quote) {
  if (wordCount(quote) < MIN_QUOTE_WORDS) {
    return { ok: false, error: 'quote_too_short', words: wordCount(quote) }
  }
  const haystack = normalize(sourceText)
  const needle = normalize(quote)
  if (haystack.includes(needle)) return { ok: true }
  return { ok: false, error: 'quote_not_found', nearest: nearestWindow(haystack, needle) }
}

function nearestWindow(haystack, needle) {
  const terms = needle.split(' ').filter((w) => w.length > 3)
  if (terms.length === 0) return null
  const words = haystack.split(' ')
  const span = needle.split(' ').length
  let best = { score: 0, at: -1 }
  for (let i = 0; i + span <= words.length; i += Math.max(1, Math.floor(span / 4))) {
    const window = words.slice(i, i + span).join(' ')
    let score = 0
    for (const term of terms) if (window.includes(term)) score += 1
    if (score > best.score) best = { score, at: i }
  }
  if (best.at < 0) return null
  return words.slice(best.at, best.at + span * 2).join(' ').slice(0, 200)
}

/**
 * Validate a capture request. Pure: the gate is testable without a database,
 * and the CLI and any future caller check the same function.
 */
export function validateCapture(args, opts = {}) {
  const required = ['client', 'kind', 'key', 'what', 'source', 'quote']
  for (const field of required) {
    if (!args[field]) return { ok: false, error: `missing_${field}` }
  }
  if (!KINDS.includes(args.kind)) return { ok: false, error: 'unknown_kind' }
  if (args.due && !args['date-quote']) return { ok: false, error: 'due_without_date_quote' }

  const sourceKind = args['source-kind'] || inferSourceKind(args.source)
  const source = opts.sourceText
    ? { ok: true, text: opts.sourceText, resolved: args.source }
    : resolveSource(sourceKind, args.source, opts)
  if (!source.ok) return { ok: false, error: source.error }

  const quoteCheck = checkQuote(source.text, args.quote)
  if (!quoteCheck.ok) return { ok: false, ...quoteCheck }

  if (args.due) {
    const dateCheck = checkQuote(source.text, args['date-quote'])
    if (!dateCheck.ok) return { ok: false, error: 'date_quote_not_found', nearest: dateCheck.nearest }
    if (!dateQuoteAnchorsDate(args['date-quote'], args.due)) {
      return { ok: false, error: 'date_quote_lacks_date' }
    }
  }

  return { ok: true, sourceKind, resolved: source.resolved }
}

const MONTHS = [
  'january',
  'february',
  'march',
  'april',
  'may',
  'june',
  'july',
  'august',
  'september',
  'october',
  'november',
  'december',
]

/**
 * The forms a letter might legitimately use to state one date.
 *
 * A client writes "before October 15th", not "2026-10-15". An earlier version
 * required the raw --due string to appear verbatim in the quote, which refused
 * every properly-grounded obligation whose letter used ordinary English -- a
 * false refusal on exactly the cases this gate exists to admit. The gate must
 * be strict about whether the date is ANCHORED in the source, never about the
 * client's formatting.
 *
 * Returns the accepted spellings; the caller passes if the quote contains any.
 * A non-ISO --due (a recurring anchor like "the 15th") is matched literally,
 * which is the right behaviour for a duty with no calendar date.
 */
export function dateQuoteForms(due) {
  const raw = normalize(due)
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw)
  if (!iso) return [raw]

  const month = MONTHS[Number(iso[2]) - 1]
  const day = String(Number(iso[3]))
  const year = iso[1]
  if (!month) return [raw]

  return [
    raw, // 2026-10-15
    `${month} ${day}`, // october 15
    `${month} ${day}st`,
    `${month} ${day}nd`,
    `${month} ${day}rd`,
    `${month} ${day}th`, // october 15th
    `${day} ${month}`, // 15 october
    `${month} ${day}, ${year}`, // october 15, 2026
    `${iso[2]}/${day}/${year}`, // 10/15/2026
    `${iso[2]}/${iso[3]}/${year}`,
  ]
}

/** Does the date quote anchor the due date in the source's own words? */
export function dateQuoteAnchorsDate(dateQuote, due) {
  const quote = normalize(dateQuote)
  return dateQuoteForms(due).some((form) => quote.includes(form))
}

export function inferSourceKind(ref) {
  if (/^git:/.test(ref)) return 'git'
  if (/^[\w.-]+\/[\w.-]+#\d+$/.test(ref)) return 'github'
  return 'letter'
}

/** Append one line to the journal, atomically. */
export function journalAppend(entry) {
  const dir = journalDir()
  fs.mkdirSync(dir, { recursive: true })
  const file = path.join(dir, `${new Date().toISOString().slice(0, 10)}.jsonl`)
  const tmp = path.join(dir, `.tmp-${crypto.randomBytes(6).toString('hex')}`)
  const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : ''
  fs.writeFileSync(tmp, existing + JSON.stringify(entry) + '\n')
  fs.renameSync(tmp, file)
  return file
}

export function journalEntries() {
  const dir = journalDir()
  if (!fs.existsSync(dir)) return []
  const out = []
  for (const name of fs.readdirSync(dir).sort()) {
    if (!name.endsWith('.jsonl')) continue
    for (const line of fs.readFileSync(path.join(dir, name), 'utf8').split('\n')) {
      if (!line.trim()) continue
      try {
        out.push({ ...JSON.parse(line), _file: path.join(dir, name) })
      } catch {
        /* a corrupt line must not hide the rest */
      }
    }
  }
  return out
}

/** Unsynced journal lines, newest state per obligation key. */
export function unsyncedEntries() {
  // The journal is append-only and ordered, so the LAST entry for a key is the
  // current state of that obligation: a later unsynced write supersedes an
  // earlier one, and a synced marker supersedes whatever it confirms.
  //
  // An earlier version only overwrote when the incoming entry was synced, which
  // silently dropped a correction: journal "Old text" offline, correct it to
  // "New text" while still offline, and sync would write the stale row and then
  // mark the key done — losing the correction with no error. Last-wins is the
  // only rule consistent with an append-only log.
  const byKey = new Map()
  for (const entry of journalEntries()) {
    byKey.set(`${entry.client}/${entry.kind}/${entry.key}`, entry)
  }
  return [...byKey.values()].filter((e) => !e.synced)
}

function sqlLiteral(value) {
  if (value === null || value === undefined) return 'NULL'
  return `'${String(value).replaceAll("'", "''")}'`
}

export function buildUpsertSql(row) {
  const cols = [
    'obligation_id',
    'customer_slug',
    'entity_id',
    'stable_key',
    'kind',
    'what',
    'origin',
    'origin_source',
    'source_kind',
    'source_ref',
    'source_quote',
    'date_quote',
    'window_start',
    'window_end',
    'due_at',
    'links_json',
    'created_by_session',
  ]
  const values = cols.map((c) => sqlLiteral(row[c]))
  return (
    `INSERT INTO client_obligations (${cols.join(', ')}) VALUES (${values.join(', ')}) ` +
    `ON CONFLICT(customer_slug, kind, stable_key) DO UPDATE SET ` +
    `what = excluded.what, source_ref = excluded.source_ref, ` +
    `source_quote = excluded.source_quote, date_quote = excluded.date_quote, ` +
    `window_start = excluded.window_start, window_end = excluded.window_end, ` +
    `due_at = excluded.due_at, links_json = excluded.links_json, ` +
    `last_seen_at = datetime('now');`
  )
}

/** Look up the entity_id for a seat. Returns null when D1 is unreachable. */
export function lookupEntityId(slug) {
  const out = runD1(
    `SELECT entity_id FROM customer_configs WHERE customer_slug = ${sqlLiteral(slug)};`
  )
  if (!out.ok) return null
  try {
    const parsed = JSON.parse(out.stdout)
    const results = parsed?.[0]?.results ?? parsed?.results ?? []
    return results[0]?.entity_id ?? null
  } catch {
    return null
  }
}

export function runD1(sql) {
  const override = process.env.SS_REGISTER_D1_CMD
  try {
    const stdout = override
      ? execFileSync(override, [DB, sql], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 })
      : execFileSync(
          'npx',
          ['wrangler', 'd1', 'execute', DB, '--remote', '--json', '--command', sql],
          { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }
        )
    return { ok: true, stdout }
  } catch (err) {
    return { ok: false, error: String(err?.message ?? err) }
  }
}

function parseArgs(argv) {
  const args = {}
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i]
    if (!token.startsWith('--')) continue
    const key = token.slice(2)
    const next = argv[i + 1]
    if (next === undefined || next.startsWith('--')) {
      args[key] = true
    } else {
      args[key] = next
      i += 1
    }
  }
  return args
}

function usage() {
  console.error(`usage:
  register add --client <slug> --kind <${KINDS.join('|')}> --key <stable-key> \\
      --what "<sentence>" --source <ref> --quote "<verbatim from source>" \\
      [--due <date> --date-quote "<verbatim containing the date>"] \\
      [--window-start <date>] [--window-end <date>] [--links <json>]
  register add --kind none --client <slug> --why "<reason nothing was owed>"
  register sync
  register list [--client <slug>]`)
}

function cmdAdd(args) {
  if (!args.client) {
    console.error('register: --client is required')
    return 1
  }

  // The considered pass. An agent that looked and found nothing owed records
  // that, so a silent miss and a deliberate "nothing here" stop being
  // indistinguishable. Journal-only: it is not an obligation.
  if (args.kind === 'none') {
    if (!args.why) {
      console.error('register: --kind none requires --why')
      return 1
    }
    journalAppend({
      ts: new Date().toISOString(),
      kind: 'none',
      client: args.client,
      key: `none-${Date.now()}`,
      why: args.why,
      session: process.env.CLAUDE_SESSION_ID ?? null,
      synced: true,
    })
    console.log(`register: recorded "nothing owed" for ${args.client}`)
    return 0
  }

  const check = validateCapture(args)
  if (!check.ok) {
    console.error(`register: REFUSED (${check.error})`)
    if (check.error === 'quote_not_found' && check.nearest) {
      console.error(`  the quote does not appear in ${args.source}`)
      console.error(`  nearest text: ...${check.nearest}...`)
    }
    if (check.error === 'engagements_repo_absent') {
      console.error(`  ${engagementsDir()} is not checked out; cannot verify the quote.`)
      console.error('  This fails closed on purpose: an unverifiable citation is not a citation.')
    }
    if (check.error === 'quote_too_short') {
      console.error(`  a ${check.words}-word quote matches too much; give at least ${MIN_QUOTE_WORDS}.`)
    }
    return 1
  }

  const entityId = lookupEntityId(args.client)
  const row = {
    obligation_id: crypto.randomUUID(),
    customer_slug: args.client,
    entity_id: entityId,
    stable_key: args.key,
    kind: args.kind,
    what: args.what,
    origin: 'captured',
    origin_source: check.sourceKind,
    source_kind: check.sourceKind,
    source_ref: args.source,
    source_quote: args.quote,
    date_quote: args['date-quote'] ?? null,
    window_start: args['window-start'] ?? null,
    window_end: args['window-end'] ?? null,
    due_at: args.due ?? null,
    links_json: args.links ?? null,
    created_by_session: process.env.CLAUDE_SESSION_ID ?? null,
  }

  const entry = {
    ts: new Date().toISOString(),
    client: args.client,
    kind: args.kind,
    key: args.key,
    row,
    synced: false,
  }
  journalAppend(entry)

  if (!entityId) {
    console.log(`register: journaled ${args.client}/${args.key} (UNSYNCED — D1 unreachable)`)
    console.log('  run `register sync` once connectivity returns.')
    return 0
  }

  const result = runD1(buildUpsertSql(row))
  if (!result.ok) {
    console.log(`register: journaled ${args.client}/${args.key} (UNSYNCED — write failed)`)
    console.error(`  ${result.error.split('\n')[0]}`)
    return 0
  }

  journalAppend({ ...entry, synced: true })
  console.log(`register: recorded ${args.client}/${args.kind}/${args.key}`)
  return 0
}

function cmdSync() {
  const pending = unsyncedEntries()
  if (pending.length === 0) {
    console.log('register: nothing to sync')
    return 0
  }
  let synced = 0
  for (const entry of pending) {
    if (!entry.row) continue
    const row = entry.row.entity_id ? entry.row : { ...entry.row, entity_id: lookupEntityId(entry.client) }
    if (!row.entity_id) continue
    const result = runD1(buildUpsertSql(row))
    if (result.ok) {
      journalAppend({ ...entry, row, synced: true })
      synced += 1
    }
  }
  // Denominator, not just a success count: "synced 3" hides how many did not.
  console.log(`register: synced ${synced} of ${pending.length} pending`)
  return synced === pending.length ? 0 : 1
}

function cmdList(args) {
  const conditions = [`state NOT IN ('closed','cancelled','void')`]
  if (args.client) conditions.push(`customer_slug = ${sqlLiteral(args.client)}`)
  const result = runD1(
    `SELECT customer_slug, kind, stable_key, state, due_at, what FROM client_obligations ` +
      `WHERE ${conditions.join(' AND ')} ORDER BY due_at IS NULL, due_at;`
  )
  if (!result.ok) {
    console.error('register: cannot read the register')
    return 1
  }
  console.log(result.stdout)
  return 0
}

export function main(argv) {
  const [command, ...rest] = argv
  const args = parseArgs(rest)
  switch (command) {
    case 'add':
      return cmdAdd(args)
    case 'sync':
      return cmdSync()
    case 'list':
      return cmdList(args)
    default:
      usage()
      return 1
  }
}

const invokedDirectly = process.argv[1] && process.argv[1].endsWith('register.mjs')
if (invokedDirectly) {
  process.exit(main(process.argv.slice(2)))
}
