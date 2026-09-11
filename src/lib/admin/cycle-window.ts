/**
 * The allowance window: a billing cycle, or the calendar month when no cycle is
 * authored (ss#2618). The TypeScript half of a three-way agreement.
 *
 * This is a PORT of `operator/workspace_broker/cycle_window.py`, and the two are
 * held together by `operator/contracts/cycle_window_fixture.json`, whose sha256
 * is pinned on every side. The third implementation is the laptop pipeline in
 * the private engagements repo.
 *
 * Before 2026-09-11 all three keyed on `created_at[:7]`, and the laptop derived
 * that prefix from local time while the seat used UTC — so two of the three
 * already disagreed about when a period began. One algorithm, one fixture, three
 * pins, is the answer to that.
 *
 * THE RULE (Stripe's own): a cycle starts on the anchor day, clamped to the last
 * day of a short month, and RETURNS to the anchor day afterwards — Jan 31 → Feb
 * 28 → Mar 31, never Feb 28 → Mar 28. Each boundary is computed from the anchor
 * independently, because chaining from the previous end is how that gets broken.
 *
 * Everything is UTC, matching `created_at` (`audit_ledger._iso_utc`). A cycle
 * turns at UTC midnight, which is 5pm the previous day in Phoenix — deliberate,
 * and the instant the firm is actually billed on.
 */

const DAY_START = 'T00:00:00.000Z'
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export const ANCHOR_SETTING = 'chronology_package_cycle_anchor_day'
export const EFFECTIVE_FROM_SETTING = 'chronology_package_cycle_effective_from'

export interface CycleWindow {
  start: string
  end: string
  /** PROSE for a human. Never parse it. */
  label: string
  anchored: boolean
}

/** Authored but unreadable — deliberately NOT the same answer as absent. */
export class AnchorInvalid extends Error {}

function daysInMonth(year: number, month1: number): number {
  return new Date(Date.UTC(year, month1, 0)).getUTCDate()
}

function clampDay(year: number, month1: number, day: number): Date {
  return new Date(Date.UTC(year, month1 - 1, Math.min(day, daysInMonth(year, month1))))
}

function nextMonth(year: number, month1: number): [number, number] {
  return month1 === 12 ? [year + 1, 1] : [year, month1 + 1]
}

function prevMonth(year: number, month1: number): [number, number] {
  return month1 === 1 ? [year - 1, 12] : [year, month1 - 1]
}

function atDayStart(d: Date): string {
  return `${d.toISOString().slice(0, 10)}${DAY_START}`
}

function labelFor(start: Date, end: Date, anchored: boolean): string {
  if (!anchored) {
    return `${start.getUTCFullYear()}-${String(start.getUTCMonth() + 1).padStart(2, '0')}`
  }
  const last = new Date(end.getTime() - 86400000) // `end` is exclusive
  return `the cycle ending ${MONTHS[last.getUTCMonth()]} ${last.getUTCDate()}`
}

/**
 * The authored anchor day, or null when no cycle is authored.
 *
 * Throws `AnchorInvalid` when the key is present but unreadable. This is where
 * the surfaces actually diverge: `"15"` (quoted in YAML) passes the open scalar
 * validator and projects to D1 as a string, so a reader that coerces gets 15
 * while one that type-checks gets nothing — same authored file, two windows.
 */
export function resolveAnchor(settings: unknown): number | null {
  if (typeof settings !== 'object' || settings === null) return null
  const raw = settings as Record<string, unknown>
  if (!(ANCHOR_SETTING in raw)) return null
  const value = raw[ANCHOR_SETTING]
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 31) {
    throw new AnchorInvalid(`${ANCHOR_SETTING}: must be a whole number from 1 to 31`)
  }
  return value
}

/** The date before which no window may reach. See the Python twin for why. */
export function resolveEffectiveFrom(settings: unknown): string | null {
  if (typeof settings !== 'object' || settings === null) return null
  const raw = settings as Record<string, unknown>
  if (!(EFFECTIVE_FROM_SETTING in raw)) return null
  const value = raw[EFFECTIVE_FROM_SETTING]
  if (typeof value !== 'string' || !DATE_RE.test(value)) {
    throw new AnchorInvalid(`${EFFECTIVE_FROM_SETTING}: must be a date, YYYY-MM-DD`)
  }
  const [y, m, d] = value.split('-').map(Number)
  const probe = new Date(Date.UTC(y, m - 1, d))
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) {
    throw new AnchorInvalid(`${EFFECTIVE_FROM_SETTING}: must be a real date, YYYY-MM-DD`)
  }
  return value
}

/** The window `now` falls in. `now` is an ISO-8601 UTC timestamp. */
export function cycleWindow(
  now: string,
  anchorDay: number | null = null,
  effectiveFrom: string | null = null
): CycleWindow {
  const [ny, nm, nd] = now.slice(0, 10).split('-').map(Number)
  const today = new Date(Date.UTC(ny, nm - 1, nd))

  let start: Date
  let end: Date
  let anchored: boolean

  if (anchorDay === null) {
    start = new Date(Date.UTC(ny, nm - 1, 1))
    const [ey, em] = nextMonth(ny, nm)
    end = new Date(Date.UTC(ey, em - 1, 1))
    anchored = false
  } else {
    const thisOne = clampDay(ny, nm, anchorDay)
    const [sy, sm] = today >= thisOne ? [ny, nm] : prevMonth(ny, nm)
    start = clampDay(sy, sm, anchorDay)
    const [ey, em] = nextMonth(sy, sm)
    end = clampDay(ey, em, anchorDay)
    anchored = true
  }

  const label = labelFor(start, end, anchored)
  if (effectiveFrom !== null) {
    const [fy, fm, fd] = effectiveFrom.split('-').map(Number)
    const floor = new Date(Date.UTC(fy, fm - 1, fd))
    // The first cycle after authoring is short: nothing created before the
    // firm's cycle began is re-partitioned into it.
    if (floor > start) start = floor
  }
  return { start: atDayStart(start), end: atDayStart(end), label, anchored }
}
