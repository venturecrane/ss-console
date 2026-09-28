/**
 * The evidence half of the register: how a captured row LEAVES the list.
 *
 * `deliver` closes a promise that was kept; `cancel` retires one the client
 * released. Both read the letter they cite off the engagements repo's
 * origin/main, refuse the letter that made the promise, and require a quote
 * the letter contains. Split out of register.mjs on 2026-09-28 when `cancel`
 * pushed that file past the 500-line ceiling; register.mjs re-exports these
 * so callers and tests keep one import surface.
 */

import { execFileSync } from 'node:child_process'
import { engagementsDir, engagementsRepoPresent, suffixOf } from './engagement-paths.mjs'
import { MIN_QUOTE_WORDS, checkQuote } from './register-grounding.mjs'

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

// ------------------------------------------------------------------- cancel

/**
 * The states a row can be cancelled FROM. Mirrors the `cancelled` edges in
 * VALID_TRANSITIONS; tests/register-cancel.test.ts pins the two lists equal.
 * `parked` is here and not in DELIVERABLE_FROM on purpose: a stalled promise
 * can still be released by the client, but it cannot have been kept.
 */
export const CANCELLABLE_FROM = ['open', 'active', 'awaiting_external', 'parked']

/**
 * Validate a cancellation. Same shape as validateDelivery, and the same gate
 * against the same wrong evidence: an imported row (its source withdraws it on
 * the nightly run), a row already settled, the letter that MADE the promise
 * offered as the letter that released it, and a quote the letter does not
 * contain. The evidence here is the CLIENT's letter withdrawing the ask; our
 * own decision to drop a promise is not a cancellation and has no command.
 */
export function validateCancellation(row, letter, quote) {
  if (!row) return { ok: false, error: 'no_such_obligation' }
  if (row.origin !== 'captured') return { ok: false, error: 'imported_rows_close_from_their_source' }
  if (!CANCELLABLE_FROM.includes(row.state)) {
    return { ok: false, error: 'not_cancellable_from_state', state: row.state }
  }
  if (!letter.ok) return { ok: false, error: letter.error }
  const promise = suffixOf(row.source_ref) || row.source_ref
  if (promise === letter.suffix) return { ok: false, error: 'evidence_is_the_promise' }
  const quoteCheck = checkQuote(letter.text, quote ?? '')
  if (!quoteCheck.ok) return { ok: false, ...quoteCheck }
  return { ok: true }
}

export function explainRefusal(check, args) {
  console.error(`register: REFUSED (${check.error})`)
  const why = {
    no_such_obligation: `  no open row with key "${args.key}" for ${args.client}; \`register list --client ${args.client}\` shows the keys.`,
    ambiguous_key: `  "${args.key}" matches more than one row for ${args.client}; pass --kind to pick one.`,
    imported_rows_close_from_their_source:
      '  this row was imported; it closes on the nightly run when its source (issue, alert, change request) clears.',
    not_deliverable_from_state: `  the row is "${check.state}"; only ${DELIVERABLE_FROM.join(', ')} rows can be delivered.`,
    not_cancellable_from_state: `  the row is "${check.state}"; only ${CANCELLABLE_FROM.join(', ')} rows can be cancelled.`,
    engagements_repo_absent: `  ${engagementsDir()} is not checked out; the delivery cannot be proven, so it is refused.`,
    engagements_unreachable: '  could not fetch the engagements repo; an unread archive proves nothing either way.',
    evidence_not_on_main: `  ${args.evidence} is not on engagements origin/main. Merge the letter's archive PR first.`,
    evidence_is_the_promise:
      '  that is the letter that MADE the promise. Cite the letter that kept it, or the client letter that released it.',
    quote_not_found: `  the quote does not appear in ${args.evidence}${check.nearest ? `\n  nearest text: ...${check.nearest}...` : ''}`,
    quote_too_short: `  a ${check.words}-word quote matches too much; give at least ${MIN_QUOTE_WORDS}.`,
  }[check.error]
  if (why) console.error(why)
  if (check.detail) console.error(`  ${check.detail}`)
}
