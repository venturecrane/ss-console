/**
 * The Captain acts that take a letter-captured row OFF its path to `verified`:
 * `cancel` (the client released the promise) and `reopen` (a recorded delivery
 * did not hold). Split out of register.mjs on 2026-10-13, when `reopen` would
 * have pushed that file past the 500-line ceiling; register.mjs dispatches here
 * and passes its shared helpers in, so there is no import cycle.
 *
 * `register reopen`: `deliver` moves a captured row to `delivered` on a sent
 * letter. Found 2026-10-13: a delivered draft was withdrawn after it went out,
 * and the row had no way back. `cancel` refuses from `delivered` (a kept
 * promise cannot be released), and the nightly reconcile would have certified
 * it `verified`. The state machine already allows `delivered -> active`; reopen
 * is the command for that one edge and no other. Same evidence discipline as
 * `cancel`: the letter is read off the archive's origin/main, it cannot be the
 * letter that made the promise, and the quote must be in it. A Captain act:
 * the CLI does not decide a delivery failed, it records the letter that shows
 * it did. After a reopen the row is owed work again; `cancel` it if the
 * client's letter also released it.
 */

import {
  markCancelledAttested,
  markReopenedAttested,
} from '../../../src/lib/db/obligation-capture.ts'
import {
  explainRefusal,
  readArchivedLetter,
  validateCancellation,
  validateReopen,
} from './register-close.mjs'

/** The first line of a failure, which is the line that says why. */
function firstLine(err) {
  return String(err?.message ?? err).split('\n')[0]
}

// ------------------------------------------------------------------- cancel

/** Write the cancellation through the module, which guards the state and reads it back. */
async function recordCancellation(db, row, letter) {
  let write
  try {
    write = await markCancelledAttested(db, {
      obligationId: row.obligation_id,
      fromState: row.state,
      surface: 'engagements',
      locator: letter.locator,
      disposition: `client withdrew the ask: ${letter.locator}`,
    })
  } catch (err) {
    return { ok: false, error: `the write failed (${firstLine(err)})` }
  }
  if (write.ok) return { ok: true }
  if (write.error === 'illegal_transition') {
    return { ok: false, error: `the register refuses ${row.state} -> cancelled` }
  }
  const reads = write.reads ? `"${write.reads}"` : 'unreadable'
  return { ok: false, error: `the write did not land: row ${row.obligation_id} reads ${reads}` }
}

/**
 * Retire a letter-captured obligation the client withdrew, citing the client's
 * letter that withdrew it.
 *
 * `deliver` closes a promise that was kept; this closes one that was released.
 * Without it a withdrawn ask sat `open` until the age ladder called it
 * critical, and the only ways out were a reconciler that refuses captured rows
 * or a hand-written UPDATE nobody could audit (found 2026-09-28, A&P letter
 * 145). Same evidence discipline as `deliver`: the letter is read off the
 * archive's origin/main, it cannot be the letter that made the promise, and
 * the quote must be in it. The row goes to `cancelled` with a disposition that
 * names the client's letter; it never passes through `delivered`. A Captain
 * act: the CLI does not decide a promise is released, it records that the
 * client said so.
 */
export async function cmdCancel(args, cli) {
  const absent = ['client', 'key', 'evidence', 'quote'].find((field) => !args[field])
  if (absent) {
    console.error(`register: --${absent} is required`)
    cli.usage()
    return 1
  }
  const found = await cli.findOpenRow(args)
  if (!found.ok) {
    if (found.error === 'ambiguous_key') explainRefusal(found, args)
    else console.error(`register: ${found.error}`)
    return 1
  }
  const row = found.row
  const letter = row ? readArchivedLetter(args.evidence) : { ok: false, error: 'no_such_obligation' }
  const check = validateCancellation(row, letter, args.quote)
  if (!check.ok) {
    explainRefusal(check, args)
    return 1
  }
  const recorded = await recordCancellation(cli.registerDb(), row, letter)
  if (!recorded.ok) {
    console.error(`register: ${recorded.error}`)
    return 1
  }
  cli.journalAppend({
    ts: new Date().toISOString(),
    kind: 'cancelled',
    client: args.client,
    key: row.stable_key,
    obligation_id: row.obligation_id,
    evidence: letter.locator,
    quote: args.quote,
    session: cli.sessionId(),
    synced: true,
  })
  console.log(`register: cancelled ${row.customer_slug}/${row.kind}/${row.stable_key}`)
  console.log(`  the client withdrew it: ${letter.locator}`)
  return 0
}

// ------------------------------------------------------------------- reopen


/** Write the reopening through the module, which guards the state and reads it back. */
async function recordReopen(db, row, letter, why) {
  let write
  try {
    write = await markReopenedAttested(db, {
      obligationId: row.obligation_id,
      fromState: row.state,
      disposition: `delivery reopened: ${why.trim()} (${letter.locator})`,
    })
  } catch (err) {
    return { ok: false, error: `the write failed (${firstLine(err)})` }
  }
  if (write.ok) return { ok: true }
  if (write.error === 'illegal_transition') {
    return { ok: false, error: `the register refuses ${row.state} -> active` }
  }
  const reads = write.reads ? `"${write.reads}"` : 'unreadable'
  return { ok: false, error: `the write did not land: row ${row.obligation_id} reads ${reads}` }
}

/**
 * @param {object} args parsed CLI flags
 * @param {object} cli  { findOpenRow, registerDb, journalAppend, sessionId, usage }
 */
export async function cmdReopen(args, cli) {
  const absent = ['client', 'key', 'evidence', 'quote', 'why'].find((field) => !args[field])
  if (absent) {
    console.error(`register: --${absent} is required`)
    cli.usage()
    return 1
  }
  const found = await cli.findOpenRow(args)
  if (!found.ok) {
    if (found.error === 'ambiguous_key') explainRefusal(found, args)
    else console.error(`register: ${found.error}`)
    return 1
  }
  const row = found.row
  const letter = row ? readArchivedLetter(args.evidence) : { ok: false, error: 'no_such_obligation' }
  const check = validateReopen(row, letter, args.quote, args.why)
  if (!check.ok) {
    explainRefusal(check, args)
    return 1
  }
  const recorded = await recordReopen(cli.registerDb(), row, letter, String(args.why))
  if (!recorded.ok) {
    console.error(`register: ${recorded.error}`)
    return 1
  }
  cli.journalAppend({
    ts: new Date().toISOString(),
    kind: 'reopened',
    client: args.client,
    key: row.stable_key,
    obligation_id: row.obligation_id,
    evidence: letter.locator,
    quote: args.quote,
    why: args.why,
    session: cli.sessionId(),
    synced: true,
  })
  console.log(`register: reopened ${row.customer_slug}/${row.kind}/${row.stable_key}`)
  console.log(`  the delivery did not hold: ${letter.locator}`)
  console.log('  the row is active (owed) again; cancel it if the client also released it.')
  return 0
}
