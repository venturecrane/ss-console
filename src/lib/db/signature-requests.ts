/**
 * Reads over `signature_requests`, the SignWell signing-flow rows that
 * `src/lib/sow/store.ts` writes.
 *
 * The one read here is the acceptance guard: a quote may be accepted only
 * once its signing flow completed and the signed artifact is persisted. It
 * lives in the data layer that enforces it (`updateQuoteStatus`) rather than
 * in the SOW module above it, so `src/lib/db/quotes.ts` never imports upward
 * (code review 2026-09-10, Architecture 7).
 */

export async function signedArtifactExists(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT 1
       FROM signature_requests
       WHERE org_id = ? AND quote_id = ? AND status = 'completed' AND signed_storage_key IS NOT NULL
       LIMIT 1`
    )
    .bind(orgId, quoteId)
    .first<{ 1: number }>()
  return !!row
}
