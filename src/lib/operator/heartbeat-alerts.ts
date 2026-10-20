/**
 * Heartbeat-driven alerts that are moment-in-time observations rather than
 * standing conditions, raised as Sentry events from the ingest path.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { readAuditWriteFailureState } from '../db/fleet-status'
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
 * The warning names WHY (migration 0131): the per-key rise of the seat's
 * reason histogram since the prior beat, e.g. `refused_not_gateway:CONFIG_WRITE
 * x2`. Rows lost before the seat recorded reasons, or a beat without the
 * histogram, read "reason not recorded" rather than a guess.
 *
 * Best-effort. A failure to read the prior value must never 500 the heartbeat:
 * losing liveness reporting to protect an alert would trade a big signal for a
 * small one.
 */
export async function reportAuditWriteFailureDelta(
  db: D1Database,
  slug: string,
  reported: number | null,
  reportedReasons: Record<string, number> | null = null
): Promise<void> {
  if (reported === null) return
  let prior: number | null
  let priorReasons: Record<string, number> | null
  try {
    const state = await readAuditWriteFailureState(db, slug)
    prior = state.total
    priorReasons = state.reasons
  } catch (err) {
    captureError(err, 'heartbeat-audit-failure-delta')
    return
  }
  const lost = reported - (prior ?? 0)
  if (lost <= 0) return
  const reasons = reasonDelta(reportedReasons, priorReasons)
  const named = Object.entries(reasons)
    .map(([key, n]) => `${key} x${n}`)
    .join(', ')
  captureWarning(
    `Operator seat ${slug} lost ${lost} audit row(s) since the last heartbeat (${named || 'reason not recorded'})`,
    'operator-audit-write-failure',
    { customer_slug: slug, lost, reported_total: reported, prior_total: prior, reasons }
  )
}

/** Per-key rise of a cumulative histogram; keys that did not rise are omitted. */
function reasonDelta(
  reported: Record<string, number> | null,
  prior: Record<string, number> | null
): Record<string, number> {
  const out: Record<string, number> = {}
  if (reported === null) return out
  for (const [key, n] of Object.entries(reported)) {
    const rise = n - (prior?.[key] ?? 0)
    if (rise > 0) out[key] = rise
  }
  return out
}
