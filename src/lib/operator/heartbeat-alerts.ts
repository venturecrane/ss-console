/**
 * Heartbeat-driven alerts that are moment-in-time observations rather than
 * standing conditions, raised as Sentry events from the ingest path.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { readAuditWriteFailures } from '../db/fleet-status'
import { captureError, captureWarning } from '../observability/sentry'

/**
 * Raise a Sentry event when a seat's cumulative audit-write-failure count rises
 * (#2498).
 *
 * The count is monotonic on the Fly volume and survives reboots, so a rise
 * means new rows were lost since the previous beat. Three cases the comparison
 * deliberately distinguishes:
 *
 *   - `reported` NULL — the seat cannot answer (no `.smd` directory; the audit
 *     plugin has never registered). Nothing to compare. Silence, not zero.
 *   - `prior` NULL and `reported` > 0 — the first beat that carries a count,
 *     and it is not zero. We have just LEARNED of failures; that is a rise.
 *   - `reported` < `prior` — the tally file was removed or the volume replaced.
 *     Not a rise; report nothing rather than an alarming negative.
 *
 * Best-effort. A failure to read the prior value must never 500 the heartbeat:
 * losing liveness reporting to protect an alert would trade a big signal for a
 * small one.
 */
export async function reportAuditWriteFailureDelta(
  db: D1Database,
  slug: string,
  reported: number | null
): Promise<void> {
  if (reported === null) return
  let prior: number | null
  try {
    prior = await readAuditWriteFailures(db, slug)
  } catch (err) {
    captureError(err, 'heartbeat-audit-failure-delta')
    return
  }
  const lost = reported - (prior ?? 0)
  if (lost <= 0) return
  captureWarning(
    `Operator seat ${slug} lost ${lost} audit row(s) since the last heartbeat`,
    'operator-audit-write-failure',
    { customer_slug: slug, lost, reported_total: reported, prior_total: prior }
  )
}
