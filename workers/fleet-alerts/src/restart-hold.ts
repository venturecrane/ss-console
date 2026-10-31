/**
 * Hold the pages a planned release restart causes (2026-10-31).
 *
 * WHY. A release replaces the seat's Machine. On a 1 GB seat the boot can take
 * longer than the 300 s heartbeat threshold, so a routine release paged the
 * Captain with five SEV1 alerts that all meant "the seat is booting":
 * captain-mark on 2026-10-31, when release f3483a0d replaced its Machine about
 * 19:00Z (heartbeat_red, spec_unverified, gateway_supervisor_inert,
 * gateway_loop_wedged, work_overdue), all recovered by 19:18Z.
 *
 * WHAT. While a seat's newest fleet_rollouts row is `deploying` or `smoking`
 * (the controller is replacing the Machine and reading the new boot back), an
 * ACTIVE state of a boot-shaped condition is held: neither opened nor, being
 * active, recovered. An alert already open stays open; a recovery still goes
 * through. Every other condition pages as before.
 *
 * WHY A BOOT THAT NEVER COMES BACK STILL PAGES. The rollout ends: its read-back
 * fails and the row turns `rolled_back`, `failed` or `tooling_error`, which ends
 * the hold, and ./rollout-failed pages that outcome. A controller that dies
 * mid-rollout leaves the row `deploying` forever (the cancelled run of
 * 2026-10-30 did); HOLD_CAP_MS bounds the hold from the row's started_at, so the
 * seat pages at most that long after the rollout began.
 *
 * FAIL-SOFT TOWARD PAGING. A read that fails holds nothing.
 */

/** The rollout states during which the controller is replacing or reading back the Machine. */
export const RESTARTING_STATES = ['deploying', 'smoking'] as const

/** Conditions a booting seat trips because it has no live heartbeat yet. */
export const BOOT_SHAPED_CONDITIONS: ReadonlySet<string> = new Set([
  'heartbeat_red',
  'work_overdue',
  'gateway_loop_wedged',
  'gateway_loop_unprovable',
  'gateway_supervisor_inert',
  'spec_unverified',
])

/** The longest a rollout can hold a seat's pages, measured from its started_at. */
export const HOLD_CAP_MS = 45 * 60 * 1000

interface LatestRolloutRow {
  customer_slug: string
  state: string
  started_at: string | null
}

interface HeldState {
  customer_slug: string
  condition: string
  active: boolean
}

/** Pure: the seats whose newest rollout is restarting them, inside the cap. */
export function restartingSeats(rows: LatestRolloutRow[], nowMs: number): Set<string> {
  const out = new Set<string>()
  for (const r of rows) {
    if (!(RESTARTING_STATES as readonly string[]).includes(r.state)) continue
    const started = typeof r.started_at === 'string' ? Date.parse(r.started_at) : Number.NaN
    if (Number.isNaN(started) || nowMs - started > HOLD_CAP_MS) continue
    out.add(r.customer_slug)
  }
  return out
}

/** Pure: drop the active boot-shaped states of restarting seats; keep everything else. */
export function withoutRestartPages<T extends HeldState>(
  states: T[],
  restarting: Set<string>
): { kept: T[]; held: T[] } {
  const kept: T[] = []
  const held: T[] = []
  for (const s of states) {
    if (s.active && restarting.has(s.customer_slug) && BOOT_SHAPED_CONDITIONS.has(s.condition)) {
      held.push(s)
    } else {
      kept.push(s)
    }
  }
  return { kept, held }
}

/** Each seat's newest rollout, read; an empty set on any failure (page as before). */
export async function readRestartingSeats(db: D1Database, nowMs: number): Promise<Set<string>> {
  try {
    const res = await db
      .prepare(
        `SELECT r.customer_slug, r.state, r.started_at FROM fleet_rollouts r
         JOIN (SELECT customer_slug, MAX(started_at) AS m FROM fleet_rollouts GROUP BY customer_slug) x
           ON r.customer_slug = x.customer_slug AND r.started_at = x.m`
      )
      .all<LatestRolloutRow>()
    return restartingSeats(res.results ?? [], nowMs)
  } catch (err) {
    console.error('[fleet-alerts] restart-hold read failed; holding nothing:', err)
    return new Set()
  }
}
