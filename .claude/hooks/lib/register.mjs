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
 * THE GROUNDING GATE IS THE POINT. Every row carries a verbatim quote, the
 * quote is string-matched against the source file, and a row whose quote is
 * not found is REFUSED. There is no --force. The gate itself lives in
 * register-grounding.mjs; this file resolves sources and applies it.
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
 * ONE WRITE IMPLEMENTATION. Rows reach D1 through `upsertObligation`
 * (src/lib/db/obligations.ts) and `markDeliveredAttested`
 * (src/lib/db/obligation-capture.ts), over the wrangler-backed D1 handle in
 * scripts/lib/wrangler-d1.ts: the same functions the reconciler and the console
 * use. Until 2026-09-25 this file string-built its own upsert with a hand
 * escaper, and its column list had already drifted from the module's (review
 * 2026-09-25, top action item 5). That is why this file runs under tsx rather
 * than bare node: `.claude/bin/register` execs the repo's pinned
 * node_modules/.bin/tsx, which loads the TypeScript directly with no build
 * step, in one process, so there is no second copy and no IPC shape to keep in
 * sync.
 *
 * DELIVERY IS THE OTHER HALF. `register deliver` marks a captured row
 * delivered by citing the SENT letter that kept the promise, read off the
 * engagements repo's origin/main with the same quote gate. It stops at
 * `delivered`; only a reconcile run can certify `verified`. See cmdDeliver.
 *
 * Exit codes: 0 ok (including "journaled but unsynced"), 1 refused.
 *
 * Env: SS_OBLIGATION_JOURNAL_DIR (default ~/.claude/ss-obligation-journal)
 *      SS_REGISTER_D1_CMD        override the wrangler invocation (tests); called
 *                                as `<cmd> <database> <sql>`
 *      SS_REGISTER_DB            D1 database name (default ss-console-db)
 */

import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import crypto from 'node:crypto'
import { engagementsDir, engagementsRepoPresent, suffixOf } from './engagement-paths.mjs'
import {
  MIN_QUOTE_WORDS,
  checkQuote,
  dateQuoteAnchorsDate,
  dateQuoteForms,
  normalize,
  wordCount,
} from './register-grounding.mjs'
// Shared with the CI reconciler. The .ts imports are why the wrapper runs tsx.
import { seatsOf } from '../../../scripts/lib/seat-clients.mjs'
import { JSON_FIELDS, projectRow, renderTable } from '../../../scripts/lib/register-view.mjs'
import { wranglerD1 } from '../../../scripts/lib/wrangler-d1.ts'
import { upsertObligation } from '../../../src/lib/db/obligations.ts'
import {
  entityIdForSeat,
  findOpenByKey,
  listOwedObligations,
  markDeliveredAttested,
} from '../../../src/lib/db/obligation-capture.ts'

// Re-exported so the CLI stays the single import surface for its own tests and
// for any caller that thinks in terms of `register`, not of where the rendering
// or the quote gate happens to live.
export { JSON_FIELDS, projectRow, renderTable }
export { checkQuote, dateQuoteAnchorsDate, dateQuoteForms, normalize, wordCount }

/** A read that has not answered in twelve seconds is not going to. */
const D1_TIMEOUT_MS = 12_000

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

/**
 * The register's D1 handle. Read from the environment on every call, so a test
 * (or a caller) that sets SS_REGISTER_D1_CMD after import is honoured.
 */
export function registerDb() {
  return wranglerD1({
    database: process.env.SS_REGISTER_DB || 'ss-console-db',
    commandOverride: process.env.SS_REGISTER_D1_CMD || null,
    timeoutMs: D1_TIMEOUT_MS,
  })
}

/** The first line of a failure, which is the line that says why. */
function firstLine(err) {
  return String(err?.message ?? err).split('\n')[0]
}

export function journalDir() {
  return (
    process.env.SS_OBLIGATION_JOURNAL_DIR ||
    path.join(os.homedir(), '.claude', 'ss-obligation-journal')
  )
}

/**
 * The id of the session recording this row.
 *
 * The harness exports CLAUDE_CODE_SESSION_ID. Until 2026-09-17 this file read
 * CLAUDE_SESSION_ID, which is never set, so `created_by_session` was NULL on
 * every one of the register's first eleven rows -- verified against production
 * D1. Nothing could answer "did the session that read the letter record what it
 * promised", which is the one question /eos Check I has to ask.
 *
 * Subagents inherit the PARENT's id, so this attributes to the session, not the
 * agent within it. That is the wanted granularity: the session is what closes.
 */
export function sessionId() {
  return process.env.CLAUDE_CODE_SESSION_ID ?? null
}

/** Read the first candidate path that is a file. */
function readFirstFile(candidates) {
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

/** Run a read-only command whose stdout is the source text. */
function readCommand(cmd, args, cwd, resolved, maxBuffer) {
  try {
    return { ok: true, text: execFileSync(cmd, args, { cwd, encoding: 'utf8', maxBuffer }), resolved }
  } catch {
    return { ok: false, error: 'source_not_found' }
  }
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
    if (!engagementsRepoPresent()) return { ok: false, error: 'engagements_repo_absent' }
    const suffix = suffixOf(sourceRef) || sourceRef
    return readFirstFile([
      path.resolve(cwd, sourceRef),
      path.join(engagementsDir(), suffix),
      path.join(engagementsDir(), sourceRef),
    ])
  }

  if (sourceKind === 'git') {
    // 'git:<sha>:<path>' -- the content as of that commit, so a later rewrite
    // of the file cannot retroactively ungroundize a row.
    const m = /^git:([0-9a-f]{7,40}):(.+)$/.exec(sourceRef)
    if (!m) return { ok: false, error: 'malformed_git_ref' }
    return readCommand('git', ['show', `${m[1]}:${m[2]}`], cwd, sourceRef, 16 * 1024 * 1024)
  }

  if (sourceKind === 'github') {
    const m = /^([\w.-]+\/[\w.-]+)#(\d+)$/.exec(sourceRef)
    if (!m) return { ok: false, error: 'malformed_github_ref' }
    const args = ['issue', 'view', m[2], '--repo', m[1], '--json', 'title,body']
    args.push('-q', '.title + "\\n" + .body')
    return readCommand('gh', args, cwd, sourceRef, 4 * 1024 * 1024)
  }

  return { ok: false, error: 'unsupported_source_kind' }
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

/** Parse one journal file, skipping corrupt lines: a bad line must not hide the rest. */
function journalFileEntries(file) {
  const out = []
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue
    try {
      out.push({ ...JSON.parse(line), _file: file })
    } catch {
      /* skip the corrupt line */
    }
  }
  return out
}

export function journalEntries() {
  const dir = journalDir()
  if (!fs.existsSync(dir)) return []
  return fs
    .readdirSync(dir)
    .sort()
    .filter((name) => name.endsWith('.jsonl'))
    .flatMap((name) => journalFileEntries(path.join(dir, name)))
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

/** The seat's entity, or null when D1 cannot say (unreachable, or no such seat). */
export async function lookupEntityId(slug) {
  try {
    return await entityIdForSeat(registerDb(), slug)
  } catch {
    return null
  }
}

/**
 * Write one captured row through the console's own upsert.
 *
 * Journal rows written before 2026-09-25 carry an `obligation_id` the old
 * string-built insert used; the module mints its own and keeps the existing
 * id on conflict, so that field is simply not passed.
 */
export async function writeCapturedRow(row) {
  const { obligation_id: _legacyId, ...input } = row
  try {
    await upsertObligation(registerDb(), input)
    return { ok: true }
  } catch (err) {
    return { ok: false, error: firstLine(err) }
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
  register deliver --client <slug> --key <stable-key> \\
      --evidence <sent letter path> --quote "<verbatim from that letter>" [--kind <kind>]
  register sync
  register list [--client <slug>]`)
}

/**
 * The considered pass. An agent that looked and found nothing owed records
 * that, so a silent miss and a deliberate "nothing here" stop being
 * indistinguishable. Journal-only: it is not an obligation.
 */
function recordNothingOwed(args) {
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
    session: sessionId(),
    synced: true,
  })
  console.log(`register: recorded "nothing owed" for ${args.client}`)
  return 0
}

function explainCaptureRefusal(check, args) {
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
}

/** The UpsertObligationInput a grounded capture becomes. */
export function capturedRow(args, sourceKind, entityId) {
  return {
    customer_slug: args.client,
    entity_id: entityId,
    stable_key: args.key,
    kind: args.kind,
    what: args.what,
    origin: 'captured',
    origin_source: sourceKind,
    source_kind: sourceKind,
    source_ref: args.source,
    source_quote: args.quote,
    date_quote: args['date-quote'] ?? null,
    window_start: args['window-start'] ?? null,
    window_end: args['window-end'] ?? null,
    due_at: args.due ?? null,
    links_json: args.links ?? null,
    created_by_session: sessionId(),
  }
}

async function cmdAdd(args) {
  if (!args.client) {
    console.error('register: --client is required')
    return 1
  }
  if (args.kind === 'none') return recordNothingOwed(args)

  const check = validateCapture(args)
  if (!check.ok) {
    explainCaptureRefusal(check, args)
    return 1
  }

  const entityId = await lookupEntityId(args.client)
  const row = capturedRow(args, check.sourceKind, entityId)
  const entry = { ts: new Date().toISOString(), client: args.client, kind: args.kind, key: args.key }
  journalAppend({ ...entry, row, synced: false })

  if (!entityId) {
    console.log(`register: journaled ${args.client}/${args.key} (UNSYNCED — D1 unreachable)`)
    console.log('  run `register sync` once connectivity returns.')
    return 0
  }

  const result = await writeCapturedRow(row)
  if (!result.ok) {
    console.log(`register: journaled ${args.client}/${args.key} (UNSYNCED — write failed)`)
    console.error(`  ${result.error}`)
    return 0
  }

  journalAppend({ ...entry, row, synced: true })
  console.log(`register: recorded ${args.client}/${args.kind}/${args.key}`)
  return 0
}

async function cmdSync() {
  const pending = unsyncedEntries()
  if (pending.length === 0) {
    console.log('register: nothing to sync')
    return 0
  }
  let synced = 0
  for (const entry of pending) {
    if (!entry.row) continue
    const entityId = entry.row.entity_id || (await lookupEntityId(entry.client))
    if (!entityId) continue
    const row = { ...entry.row, entity_id: entityId }
    if ((await writeCapturedRow(row)).ok) {
      journalAppend({ ...entry, row, synced: true })
      synced += 1
    }
  }
  // Denominator, not just a success count: "synced 3" hides how many did not.
  console.log(`register: synced ${synced} of ${pending.length} pending`)
  return synced === pending.length ? 0 : 1
}

// ------------------------------------------------------------------ deliver

/**
 * The states a row can be delivered FROM. Mirrors the `delivered` edges in
 * VALID_TRANSITIONS (src/lib/db/obligations.ts); tests/register-deliver.test.ts
 * pins the two lists equal, and markDeliveredAttested re-checks the edge.
 */
export const DELIVERABLE_FROM = ['open', 'active', 'awaiting_external']

/** The repo the receipt names. The locator must say where, not only what. */
const ENGAGEMENTS_REPO = 'venturecrane/engagements'

/** A git runner pinned to the engagements repo, with the hook environment stripped. */
function engagementsGit(repo) {
  // GIT_* stripped: GIT_DIR and friends win over `-C`, and git exports them to
  // every hook, so run from inside one this would read the WRONG repository and
  // could "find" a letter there.
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('GIT_')))
  return (args) =>
    execFileSync('git', ['-C', repo, ...args], {
      encoding: 'utf8',
      env,
      maxBuffer: 16 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
}

/**
 * Read a letter as it stands on the engagements repo's origin/main.
 *
 * WHY origin/main AND NOT THE FILE ON DISK. A letter sitting in a local
 * checkout may be a draft that was never sent, or a commit on a branch nobody
 * merged. The archive on main is the venture's record of what went to the
 * client, so that is the only place a delivery can be proven from. The fetch
 * comes first, and a fetch that fails REFUSES: an archive we could not read is
 * not an archive that lacks the letter, and it is certainly not one that has it.
 */
export function readArchivedLetter(letterPath) {
  if (!engagementsRepoPresent()) return { ok: false, error: 'engagements_repo_absent' }
  const git = engagementsGit(engagementsDir())
  const suffix = suffixOf(letterPath) || letterPath
  let sha
  try {
    git(['fetch', '--quiet', 'origin', 'main'])
    sha = git(['rev-parse', 'origin/main']).trim()
  } catch {
    return { ok: false, error: 'engagements_unreachable' }
  }
  try {
    const text = git(['show', `${sha}:${suffix}`])
    return { ok: true, text, suffix, locator: `${ENGAGEMENTS_REPO}@${sha.slice(0, 12)}:${suffix}` }
  } catch {
    return { ok: false, error: 'evidence_not_on_main' }
  }
}

/**
 * Validate a delivery. Pure over its inputs: the row and the letter are passed
 * in, so every refusal is testable without a database or a git remote.
 *
 * What it refuses, and why each is a real failure rather than pedantry:
 *   - an IMPORTED row: those close from their own source (the issue, the
 *     alert, the change request) on the nightly run. A hand-marked delivery
 *     would be a second opinion that can disagree with the source.
 *   - the PROMISE cited as the DELIVERY: the letter that said "we will" cannot
 *     be the proof that we did. The easiest wrong evidence to reach for, since
 *     it is already sitting in the row.
 *   - a quote not in the delivery letter: the same grounding gate `add` uses.
 *     It ties the delivery to THIS obligation; any archived letter would
 *     otherwise close any row.
 */
export function validateDelivery(row, letter, quote) {
  if (!row) return { ok: false, error: 'no_such_obligation' }
  if (row.origin !== 'captured') return { ok: false, error: 'imported_rows_close_from_their_source' }
  if (!DELIVERABLE_FROM.includes(row.state)) {
    return { ok: false, error: 'not_deliverable_from_state', state: row.state }
  }
  if (!letter.ok) return { ok: false, error: letter.error }
  const promise = suffixOf(row.source_ref) || row.source_ref
  if (promise === letter.suffix) return { ok: false, error: 'evidence_is_the_promise' }
  const quoteCheck = checkQuote(letter.text, quote ?? '')
  if (!quoteCheck.ok) return { ok: false, ...quoteCheck }
  return { ok: true }
}

function explainRefusal(check, args) {
  console.error(`register: REFUSED (${check.error})`)
  const why = {
    no_such_obligation: `  no open row with key "${args.key}" for ${args.client}; \`register list --client ${args.client}\` shows the keys.`,
    ambiguous_key: `  "${args.key}" matches more than one row for ${args.client}; pass --kind to pick one.`,
    imported_rows_close_from_their_source:
      '  this row was imported; it closes on the nightly run when its source (issue, alert, change request) clears.',
    not_deliverable_from_state: `  the row is "${check.state}"; only ${DELIVERABLE_FROM.join(', ')} rows can be delivered.`,
    engagements_repo_absent: `  ${engagementsDir()} is not checked out; the delivery cannot be proven, so it is refused.`,
    engagements_unreachable: '  could not fetch the engagements repo; an unread archive proves nothing either way.',
    evidence_not_on_main: `  ${args.evidence} is not on engagements origin/main. Merge the letter's archive PR first.`,
    evidence_is_the_promise: '  that is the letter that MADE the promise. Cite the letter that kept it.',
    quote_not_found: `  the quote does not appear in ${args.evidence}${check.nearest ? `\n  nearest text: ...${check.nearest}...` : ''}`,
    quote_too_short: `  a ${check.words}-word quote matches too much; give at least ${MIN_QUOTE_WORDS}.`,
  }[check.error]
  if (why) console.error(why)
  if (check.detail) console.error(`  ${check.detail}`)
}

/** The one non-terminal row a client's key names, or why there is not one. */
async function findOpenRow(args) {
  try {
    const found = await findOpenByKey(registerDb(), {
      seats: seatsOf(args.client),
      key: args.key,
      kind: args.kind || null,
    })
    return found.ok ? found : { ok: false, error: found.error }
  } catch (err) {
    return { ok: false, error: `cannot read the register (${firstLine(err)})` }
  }
}

/** Write the delivery through the module, which guards the state and reads it back. */
async function recordDelivery(row, letter) {
  let write
  try {
    write = await markDeliveredAttested(registerDb(), {
      obligationId: row.obligation_id,
      fromState: row.state,
      surface: 'engagements',
      locator: letter.locator,
    })
  } catch (err) {
    return { ok: false, error: `the write failed (${firstLine(err)})` }
  }
  if (write.ok) return { ok: true }
  if (write.error === 'illegal_transition') {
    return { ok: false, error: `the register refuses ${row.state} -> delivered` }
  }
  const reads = write.reads ? `"${write.reads}"` : 'unreadable'
  return { ok: false, error: `the write did not land: row ${row.obligation_id} reads ${reads}` }
}

/**
 * Mark a letter-captured obligation delivered, citing the letter that kept it.
 *
 * This is the missing half of the register. Until it existed, a captured row
 * had no evidence pointer, the reconciler's probe returned `absent` on its
 * first line, and every promise made in a letter stayed `open` forever however
 * completely it had been kept (found 2026-09-19: A&P's rehearsal-results row,
 * answered in letter 64 the same day it was recorded).
 *
 * It moves the row to `delivered`, never further. `verified` needs a reconcile
 * run (schema CHECK), so the nightly CI run is still what certifies. CI holds
 * no credential for the private engagements repo, so the evidence is ATTESTED:
 * this command is the thing that can see the archive, it reads the letter off
 * origin/main, and the receipt it leaves (the commit-pinned locator plus the
 * probe time) is what CI certifies against -- the same class the schema already
 * defines for Smokeball filings.
 *
 * Online only. A delivery journaled offline and replayed later would certify
 * against an archive read hours earlier; refusing is cheaper than that.
 */
async function cmdDeliver(args) {
  const absent = ['client', 'key', 'evidence', 'quote'].find((field) => !args[field])
  if (absent) {
    console.error(`register: --${absent} is required`)
    usage()
    return 1
  }
  const found = await findOpenRow(args)
  if (!found.ok) {
    if (found.error === 'ambiguous_key') explainRefusal(found, args)
    else console.error(`register: ${found.error}`)
    return 1
  }
  const row = found.row
  const letter = row ? readArchivedLetter(args.evidence) : { ok: false, error: 'no_such_obligation' }
  const check = validateDelivery(row, letter, args.quote)
  if (!check.ok) {
    explainRefusal(check, args)
    return 1
  }
  const recorded = await recordDelivery(row, letter)
  if (!recorded.ok) {
    console.error(`register: ${recorded.error}`)
    return 1
  }
  journalAppend({
    ts: new Date().toISOString(),
    kind: 'delivered',
    client: args.client,
    key: row.stable_key,
    obligation_id: row.obligation_id,
    evidence: letter.locator,
    quote: args.quote,
    session: sessionId(),
    synced: true,
  })
  console.log(`register: delivered ${row.customer_slug}/${row.kind}/${row.stable_key}`)
  console.log(`  evidence ${letter.locator}`)
  console.log('  the nightly reconcile run certifies it (delivered -> verified).')
  return 0
}

async function cmdList(args) {
  // `--client` takes a CLIENT, and a client is not always a seat. Rows are keyed
  // by seat (`customer_slug`), but SMD's three own seats roll up to the
  // `smd-services` client, and that is the name CLAUDE.md and /sos tell people
  // to use. Filtering the seat column by a client name matched zero rows and
  // printed "nothing open" while all three seats had work -- a clean-looking
  // report over open work, which is the exact failure this register exists to
  // end. So expand the client to its seats and match any of them.
  let rows
  try {
    rows = await listOwedObligations(registerDb(), args.client ? seatsOf(args.client) : null)
  } catch (err) {
    // Say WHY. The caller is /sos or a person, and "cannot read" without a
    // reason is indistinguishable from an empty register at a glance.
    console.error(`register: cannot read the register (${firstLine(err)})`)
    return 1
  }
  if (args.json) {
    console.log(JSON.stringify(rows.map((r) => projectRow(r))))
    return 0
  }
  console.log(renderTable(rows))
  return 0
}

export async function main(argv) {
  const [command, ...rest] = argv
  const args = parseArgs(rest)
  switch (command) {
    case 'add':
      return cmdAdd(args)
    case 'deliver':
      return cmdDeliver(args)
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
  process.exitCode = await main(process.argv.slice(2))
}
