/**
 * selftest_failing: the seat's in-image self-test reported a failed check
 * (migration 0137/0138).
 *
 * THE GAP. A seat was judged only by the boot smoke a person ran from a laptop
 * at provision and release, so a seat that drifted between releases was found
 * by whoever next looked. The image now runs the same check list as root at
 * boot and hourly (operator/templates/selftest.sh) and the seat ships the
 * verdict on every heartbeat as `selftest` (stored in selftest_json).
 *
 * THE DECISION, the tool-failing tri-state with one asymmetry:
 *   selftest_json NULL            push nothing (hold: no run yet, or an image
 *                                 without the self-test).
 *   failed_count === 0            push inactive (resolves).
 *   failed_count > 0, run_kind boot or stale
 *                                 push ACTIVE on this first reading. A boot run
 *                                 is the one the image judges strictly (busy
 *                                 lanes and vendor probes fail there rather
 *                                 than warn), and an hour of silence after a
 *                                 bad boot is the defect this exists to end.
 *                                 `stale` is the seat saying its loop died or
 *                                 its results are unreadable, already three
 *                                 hours of nothing.
 *   failed_count > 0, run_kind hourly
 *                                 ACTIVE once two consecutive runs failed
 *                                 (failed_streak >= 2); on the first failed
 *                                 hourly run, inactive. A single hourly red is
 *                                 a race as often as a fault, and the previous
 *                                 run was clean, so nothing is open to resolve
 *                                 wrongly.
 */

import type { ConditionState, FleetStatusRow } from './index'

/** Consecutive failed hourly runs before the page. */
export const SELFTEST_HOURLY_STREAK = 2

const ID_RE = /^[a-z0-9-]{1,80}$/

export interface SelftestVerdict {
  run_kind: 'boot' | 'hourly' | 'stale'
  finished_at: string
  failed_count: number
  warned_count: number
  failed_streak: number
  total: number
  failed: string[]
  image_ref: string | null
}

function nonNegInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
}

function ids(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null
  return value.every((v) => typeof v === 'string' && ID_RE.test(v)) ? (value as string[]) : null
}

/**
 * Parse a row's selftest_json defensively (the Worker's own trust boundary:
 * one corrupt row degrades to null, a hold, and never aborts the fleet loop).
 */
export function parseSelftestVerdict(json: string | null | undefined): SelftestVerdict | null {
  if (json === null || json === undefined) return null
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return null
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const kind = r.run_kind
  const failed = ids(r.failed)
  const counts = [r.failed_count, r.warned_count, r.failed_streak, r.total].map(nonNegInt)
  if (kind !== 'boot' && kind !== 'hourly' && kind !== 'stale') return null
  if (typeof r.finished_at !== 'string' || failed === null || counts.includes(null)) return null
  const [failedCount, warnedCount, streak, total] = counts as number[]
  return {
    run_kind: kind,
    finished_at: r.finished_at,
    failed_count: failedCount,
    warned_count: warnedCount,
    failed_streak: streak,
    total,
    failed,
    image_ref: typeof r.image_ref === 'string' ? r.image_ref : null,
  }
}

function pages(v: SelftestVerdict): boolean {
  if (v.failed_count === 0) return false
  return v.run_kind !== 'hourly' || v.failed_streak >= SELFTEST_HOURLY_STREAK
}

function detail(slug: string, v: SelftestVerdict): string {
  if (v.failed_count === 0) {
    return `the ${v.run_kind} self-test at ${v.finished_at} passed (${v.total} checks, ${v.warned_count} warning(s))`
  }
  const shown = v.failed.join(', ')
  const more =
    v.failed_count > v.failed.length ? ` and ${v.failed_count - v.failed.length} more` : ''
  return (
    `${v.failed_count} check(s) failed in the ${v.run_kind} self-test at ${v.finished_at}: ${shown}${more}. ` +
    `Consecutive failed runs: ${v.failed_streak}. Image: ${v.image_ref ?? 'not reported'}. ` +
    `Each check's detail is on the seat: fly ssh console -a hermes-${slug} -C "cat /run/smd-selftest/results.json". ` +
    'Auto-resolves on the next run with no failed check.'
  )
}

/** The selftest_failing state for one row, or none (hold). */
export function selftestConditions(row: FleetStatusRow): ConditionState[] {
  const v = parseSelftestVerdict(row.selftest_json)
  if (v === null) return []
  return [
    {
      customer_slug: row.customer_slug,
      condition: 'selftest_failing',
      active: pages(v),
      detail: detail(row.customer_slug, v),
    },
  ]
}
