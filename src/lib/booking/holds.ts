/**
 * Booking hold primitives — short-lived pessimistic slot reservations.
 *
 * The hold is the first concurrency layer in the /api/booking/reserve flow:
 *   1. INSERT a row in booking_holds with a 5-minute expires_at.
 *   2. The UNIQUE(org_id, slot_start_utc) constraint blocks any concurrent
 *      reserve from acquiring the same slot.
 *   3. After the assessment + sidecar + Google event are committed, the
 *      hold row is deleted (it's no longer needed; the live booking is the
 *      lock).
 *
 * Critical: a plain INSERT against the unique constraint would 409 against
 * an EXPIRED hold left behind by a crashed prior request, returning a
 * misleading "slot just taken" to a legitimate user. We use the upsert-when-
 * expired pattern instead — claim the slot if either no row exists, or if
 * the existing row has already expired.
 *
 * The daily cleanup cron (workers/booking-cleanup/) deletes long-stale
 * holds, but the upsert pattern is what makes the hot path correct under
 * normal operation.
 */

const HOLD_TTL_MINUTES = 5

/** Convert a JS Date to SQLite datetime format ('YYYY-MM-DD HH:MM:SS'). */
export function toSqliteDatetime(d: Date): string {
  return d
    .toISOString()
    .replace('T', ' ')
    .replace(/\.\d{3}Z$/, '')
}

export interface AcquireHoldResult {
  acquired: boolean
  /** The id of the hold row, valid only when `acquired === true`. */
  id?: string
  /** When `acquired === false`, the reason is always 'slot_taken' for now. */
  reason?: 'slot_taken'
}

/**
 * Atomically claim a slot via the upsert-when-expired pattern. Returns
 * `{ acquired: true, id }` if the hold was claimed, or `{ acquired: false,
 * reason: 'slot_taken' }` if a live (non-expired) hold already exists.
 *
 * The implementation uses a single INSERT … ON CONFLICT … DO UPDATE … WHERE
 * statement that:
 *   - Inserts a new row when none exists for (org_id, slot_start_utc)
 *   - Updates the existing row IF its expires_at is in the past
 *   - Does nothing IF the existing row is still live
 *
 * We then check `changes() > 0` (via RETURNING) to confirm acquisition.
 */
export async function acquireHold(
  db: D1Database,
  orgId: string,
  slotStartUtc: string,
  guestEmail: string | null
): Promise<AcquireHoldResult> {
  const id = crypto.randomUUID()
  const now = toSqliteDatetime(new Date())
  const expires = toSqliteDatetime(new Date(Date.now() + HOLD_TTL_MINUTES * 60_000))

  // INSERT … ON CONFLICT … DO UPDATE … WHERE expires_at < now
  // RETURNING id lets us detect whether we won the race.
  const result = await db
    .prepare(
      `INSERT INTO booking_holds (id, org_id, slot_start_utc, expires_at, guest_email, created_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(org_id, slot_start_utc) DO UPDATE SET
         id = excluded.id,
         expires_at = excluded.expires_at,
         guest_email = excluded.guest_email,
         created_at = excluded.created_at
       WHERE booking_holds.expires_at < datetime('now')
       RETURNING id`
    )
    .bind(id, orgId, slotStartUtc, expires, guestEmail, now)
    .first<{ id: string }>()

  if (!result || result.id !== id) {
    // Either the upsert was a no-op (live hold exists) or somehow returned
    // a different row id (shouldn't happen with the WHERE guard).
    return { acquired: false, reason: 'slot_taken' }
  }

  return { acquired: true, id }
}

/**
 * Release a hold by id. Used after the booking is committed and the hold
 * is no longer needed (the live assessment row is now the lock).
 *
 * Idempotent — safe to call even if the row was already removed by cleanup
 * or the upsert pattern.
 */
export async function releaseHold(db: D1Database, holdId: string): Promise<void> {
  await db.prepare('DELETE FROM booking_holds WHERE id = ?').bind(holdId).run()
}

/**
 * Delete all expired hold rows. Called by the daily cleanup cron at
 * `workers/booking-cleanup/`. Returns the number of rows removed for
 * logging.
 */
export async function cleanupExpiredHolds(db: D1Database): Promise<number> {
  const result = await db
    .prepare("DELETE FROM booking_holds WHERE expires_at < datetime('now', '-1 hour')")
    .run()
  return result.meta?.changes ?? 0
}
