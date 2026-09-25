/**
 * The letter-capture lane of the obligation register (ADR 0088).
 *
 * `.claude/bin/register` records obligations stated in client letters and
 * marks them delivered by citing the letter that kept them. Until 2026-09-25 it
 * did that with its own string-built SQL shipped to `wrangler d1 execute`: a
 * second write implementation of `client_obligations` beside
 * `upsertObligation`, whose column lists had already drifted (review
 * 2026-09-25, top action item 5). The CLI now runs under tsx and writes through
 * this module and `obligations.ts`, over the same wrangler-backed D1 handle the
 * reconciler uses (`scripts/lib/wrangler-d1.ts`).
 *
 * Nothing here decides grounding. The quote gate lives in the CLI because it
 * reads files and git; this module only reads and writes rows.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  checkTransition,
  getObligation,
  type Obligation,
  type ObligationState,
} from './obligations'

/** The columns `register list` renders. `what` is prose and never leaves the terminal table. */
export type OwedObligation = Pick<
  Obligation,
  | 'obligation_id'
  | 'customer_slug'
  | 'kind'
  | 'stable_key'
  | 'state'
  | 'due_at'
  | 'created_at'
  | 'what'
>

/** The columns a delivery decision needs. */
export type CapturedRowRef = Pick<
  Obligation,
  'obligation_id' | 'customer_slug' | 'kind' | 'stable_key' | 'origin' | 'state' | 'source_ref'
>

function placeholders(values: readonly unknown[]): string {
  return values.map(() => '?').join(', ')
}

/**
 * The entity a seat belongs to, or null when the seat has no config row.
 *
 * A captured row needs it (`entity_id` is NOT NULL), and a seat the console
 * does not know is a row the CLI must journal rather than write.
 */
export async function entityIdForSeat(db: D1Database, seat: string): Promise<string | null> {
  const row = await db
    .prepare('SELECT entity_id FROM customer_configs WHERE customer_slug = ?')
    .bind(seat)
    .first<{ entity_id: string | null }>()
  return row?.entity_id ?? null
}

/**
 * Every obligation still owed, optionally narrowed to a set of seats.
 *
 * `verified` is excluded with the terminal states: a reconcile run has probed
 * the real surface and found the work done, so it is no longer owed. Leaving
 * it in made /sos count kept promises as open ones. `delivered` stays in: it
 * is our claim, and it is on the list until a run certifies it.
 */
export async function listOwedObligations(
  db: D1Database,
  seats: readonly string[] | null
): Promise<OwedObligation[]> {
  const scoped = seats && seats.length > 0
  const sql =
    `SELECT obligation_id, customer_slug, kind, stable_key, state, due_at, created_at, what ` +
    `FROM client_obligations WHERE state NOT IN ('verified','closed','cancelled','void')` +
    (scoped ? ` AND customer_slug IN (${placeholders(seats)})` : '') +
    ` ORDER BY due_at IS NULL, due_at, created_at`
  const rows = await db
    .prepare(sql)
    .bind(...(scoped ? seats : []))
    .all<OwedObligation>()
  return rows.results ?? []
}

export type OpenRowLookup =
  { ok: true; row: CapturedRowRef | null } | { ok: false; error: 'ambiguous_key' }

/**
 * The one non-terminal row a client's key names, or why there is not one.
 *
 * Keys are unique per (seat, kind), so a key alone can name two rows; the
 * caller disambiguates with `kind` rather than this function guessing.
 */
export async function findOpenByKey(
  db: D1Database,
  input: { seats: readonly string[]; key: string; kind?: string | null }
): Promise<OpenRowLookup> {
  const values: unknown[] = [...input.seats, input.key]
  let sql =
    `SELECT obligation_id, customer_slug, kind, stable_key, origin, state, source_ref ` +
    `FROM client_obligations WHERE customer_slug IN (${placeholders(input.seats)}) ` +
    `AND stable_key = ? AND state NOT IN ('closed','cancelled','void')`
  if (input.kind) {
    sql += ' AND kind = ?'
    values.push(input.kind)
  }
  const rows = await db
    .prepare(sql)
    .bind(...values)
    .all<CapturedRowRef>()
  const found = rows.results ?? []
  if (found.length > 1) return { ok: false, error: 'ambiguous_key' }
  return { ok: true, row: found[0] ?? null }
}

export type DeliveryWrite =
  | { ok: true }
  | { ok: false; error: 'illegal_transition' }
  | { ok: false; error: 'did_not_land'; reads: ObligationState | null }

/**
 * Mark a captured row delivered on ATTESTED evidence, then READ IT BACK.
 *
 * The UPDATE is guarded on the state the caller read, so a row another session
 * moved in between is not overwritten. An UPDATE that matched nothing reports
 * success just the same, so the read-back is what makes "delivered" mean the
 * register says so. `verified` is never set here: only a reconcile run
 * certifies (0117 CHECK, and `checkTransition` below refuses it too).
 */
export async function markDeliveredAttested(
  db: D1Database,
  input: { obligationId: string; fromState: ObligationState; surface: string; locator: string }
): Promise<DeliveryWrite> {
  const legal = checkTransition(input.fromState, 'delivered', null)
  if (!legal.ok) return { ok: false, error: 'illegal_transition' }

  await db
    .prepare(
      `UPDATE client_obligations SET state = 'delivered', evidence_class = 'attested', ` +
        `evidence_surface = ?, evidence_locator = ?, evidence_last_verified_at = datetime('now') ` +
        `WHERE obligation_id = ? AND state = ?`
    )
    .bind(input.surface, input.locator, input.obligationId, input.fromState)
    .run()

  const after = await getObligation(db, input.obligationId)
  if (!after || after.state !== 'delivered' || after.evidence_locator !== input.locator) {
    return { ok: false, error: 'did_not_land', reads: after?.state ?? null }
  }
  return { ok: true }
}
