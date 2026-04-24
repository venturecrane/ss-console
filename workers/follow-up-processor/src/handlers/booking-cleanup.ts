/**
 * Booking cleanup handler.
 *
 * Prunes expired booking holds and consumed/expired OAuth state nonces.
 * Idempotent and safe to run multiple times.
 */

/**
 * Delete expired booking holds (>1 hour past expiry) and stale OAuth states
 * (>1 day past expiry). Returns a summary string for logging.
 */
export async function runBookingCleanup(db: D1Database): Promise<string> {
  // Delete expired booking holds — the upsert-when-expired pattern in
  // holds.ts handles the hot path, but this cleans up long-stale rows.
  const holdsResult = await db
    .prepare("DELETE FROM booking_holds WHERE expires_at < datetime('now', '-1 hour')")
    .run()
  const holdsDeleted = holdsResult.meta?.changes ?? 0

  // Delete expired or consumed OAuth state nonces older than 1 day.
  const statesResult = await db
    .prepare("DELETE FROM oauth_states WHERE expires_at < datetime('now', '-1 day')")
    .run()
  const statesDeleted = statesResult.meta?.changes ?? 0

  return `holds=${holdsDeleted}, oauth_states=${statesDeleted}`
}
