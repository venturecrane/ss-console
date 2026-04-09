/**
 * Availability engine — pure functions for slot generation and busy subtraction.
 *
 * Time math is done with `date-fns-tz`. All inputs/outputs are UTC ISO 8601
 * strings or `Date` objects representing absolute moments. Local-time math
 * (working hours per weekday) is converted via the consultant's tz from
 * `BOOKING_CONFIG.consultant.timezone`.
 *
 * Phoenix (America/Phoenix) does not observe DST, so the local→UTC offset
 * is constant year-round, but we still go through `date-fns-tz` to keep the
 * code agnostic of consultant tz for a future multi-consultant world.
 *
 * Pure functions live up top so they're trivially testable. The orchestrator
 * `getAvailableSlots` at the bottom wires them together with D1 + Google
 * freebusy data; that one needs the env binding and is integration-tested.
 */

import { formatInTimeZone, fromZonedTime, toZonedTime } from 'date-fns-tz'
import type { BookingConfig, WeeklyDayKey } from './config'
import { BOOKING_CONFIG } from './config'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SlotRange {
  /** ISO 8601 UTC. Inclusive. */
  start_utc: string
  /** ISO 8601 UTC. Exclusive. */
  end_utc: string
}

const DAY_KEYS: WeeklyDayKey[] = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

// ---------------------------------------------------------------------------
// Pure functions
// ---------------------------------------------------------------------------

/**
 * Generate every candidate slot in [from, to] for the consultant's weekly
 * schedule. Slots are aligned to the start of each working window and step
 * by `config.slot_minutes`. Slots that would extend past a working window's
 * end are dropped.
 *
 * Time semantics: `from` and `to` are absolute UTC moments. The weekly
 * schedule is interpreted in the consultant's timezone — i.e. "Monday 9am"
 * means 9am Phoenix local on each Monday in the range.
 */
export function generateCandidateSlots(
  from: Date,
  to: Date,
  config: BookingConfig = BOOKING_CONFIG
): SlotRange[] {
  if (from >= to) return []

  const tz = config.consultant.timezone
  const slotMs = config.slot_minutes * 60_000

  // Walk one local day at a time, generating slots from each window.
  // We start one day before `from` (in case from is mid-day and the
  // window started earlier) and stop one day after `to`.
  const slots: SlotRange[] = []
  const startLocal = toZonedTime(from, tz)
  const endLocal = toZonedTime(to, tz)

  // Iterate over local calendar dates from startLocal to endLocal inclusive
  const cursor = new Date(
    Date.UTC(startLocal.getFullYear(), startLocal.getMonth(), startLocal.getDate())
  )
  const lastDay = new Date(
    Date.UTC(endLocal.getFullYear(), endLocal.getMonth(), endLocal.getDate())
  )

  while (cursor <= lastDay) {
    const dayKey = DAY_KEYS[cursor.getUTCDay()]
    const windows = config.weekly_schedule[dayKey] ?? []

    for (const window of windows) {
      const [startH, startM] = window.start.split(':').map(Number)
      const [endH, endM] = window.end.split(':').map(Number)

      // Build "YYYY-MM-DDTHH:MM:00" in the consultant's tz, convert to UTC
      const yyyy = cursor.getUTCFullYear()
      const mm = String(cursor.getUTCMonth() + 1).padStart(2, '0')
      const dd = String(cursor.getUTCDate()).padStart(2, '0')
      const hhStart = String(startH).padStart(2, '0')
      const mmStart = String(startM).padStart(2, '0')
      const hhEnd = String(endH).padStart(2, '0')
      const mmEnd = String(endM).padStart(2, '0')

      const windowStartUtc = fromZonedTime(`${yyyy}-${mm}-${dd}T${hhStart}:${mmStart}:00`, tz)
      const windowEndUtc = fromZonedTime(`${yyyy}-${mm}-${dd}T${hhEnd}:${mmEnd}:00`, tz)

      let slotStart = windowStartUtc.getTime()
      const limit = windowEndUtc.getTime()

      while (slotStart + slotMs <= limit) {
        const slotEnd = slotStart + slotMs
        // Only include slots that overlap [from, to]
        if (slotEnd > from.getTime() && slotStart < to.getTime()) {
          slots.push({
            start_utc: new Date(slotStart).toISOString(),
            end_utc: new Date(slotEnd).toISOString(),
          })
        }
        slotStart = slotEnd
      }
    }

    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }

  return slots
}

/**
 * Expand every busy range by `bufferMinutes` on both ends. This is the
 * canonical way to enforce a buffer between bookings: instead of trying to
 * apply the buffer at slot generation time (which gets messy with multiple
 * overlapping busy ranges), we widen the busy ranges so any candidate slot
 * that overlaps the *expanded* range is dropped during subtraction.
 *
 * Pure — does not modify input.
 */
export function expandBusyByBuffer(busy: SlotRange[], bufferMinutes: number): SlotRange[] {
  if (bufferMinutes <= 0) return busy.slice()
  const bufferMs = bufferMinutes * 60_000
  return busy.map((b) => ({
    start_utc: new Date(new Date(b.start_utc).getTime() - bufferMs).toISOString(),
    end_utc: new Date(new Date(b.end_utc).getTime() + bufferMs).toISOString(),
  }))
}

/**
 * Drop every candidate slot that overlaps any busy range.
 *
 * Overlap semantics: `[a.start, a.end)` overlaps `[b.start, b.end)` iff
 * `a.start < b.end && b.start < a.end`. A busy range that ENDS exactly at
 * a slot's start does NOT block the slot (touching is fine).
 */
export function subtractBusyRanges(candidates: SlotRange[], busy: SlotRange[]): SlotRange[] {
  if (busy.length === 0) return candidates
  // Convert to numeric millis once for fast comparisons
  const busyNumeric = busy.map((b) => ({
    start: new Date(b.start_utc).getTime(),
    end: new Date(b.end_utc).getTime(),
  }))
  return candidates.filter((c) => {
    const cs = new Date(c.start_utc).getTime()
    const ce = new Date(c.end_utc).getTime()
    for (const b of busyNumeric) {
      if (cs < b.end && b.start < ce) return false
    }
    return true
  })
}

/**
 * Filter candidates to those that fall within the min-notice → max-lookahead
 * window relative to `now`.
 */
export function applyNoticeAndLookahead(
  candidates: SlotRange[],
  now: Date,
  config: BookingConfig = BOOKING_CONFIG
): SlotRange[] {
  const earliest = now.getTime() + config.min_notice_minutes * 60_000
  const latest = now.getTime() + config.max_lookahead_days * 24 * 60 * 60_000
  return candidates.filter((c) => {
    const cs = new Date(c.start_utc).getTime()
    return cs >= earliest && cs <= latest
  })
}

/**
 * Format a UTC slot's start time as a label in the requested viewer tz.
 * Used by `/api/booking/slots` to populate guest-local labels in the
 * response payload.
 */
export function formatSlotLabel(slotStartUtc: string, viewerTz: string): string {
  return formatInTimeZone(new Date(slotStartUtc), viewerTz, 'h:mm a')
}

/**
 * Group slots by their local calendar date in the viewer's tz. Used by
 * `/api/booking/slots` to produce the day-grouped response shape.
 */
export function groupSlotsByLocalDate(
  slots: SlotRange[],
  viewerTz: string
): { date: string; slots: SlotRange[] }[] {
  const groups = new Map<string, SlotRange[]>()
  for (const slot of slots) {
    const key = formatInTimeZone(new Date(slot.start_utc), viewerTz, 'yyyy-MM-dd')
    const existing = groups.get(key)
    if (existing) {
      existing.push(slot)
    } else {
      groups.set(key, [slot])
    }
  }
  // Sort by date ascending
  return Array.from(groups.entries())
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([date, slots]) => ({ date, slots }))
}

// ---------------------------------------------------------------------------
// Orchestrator (D1 + Google freebusy wiring)
// ---------------------------------------------------------------------------

/**
 * Top-level slot generator. Combines:
 *   1. Candidate slots from the consultant's weekly schedule (pure)
 *   2. Min notice + max lookahead filter
 *   3. Busy ranges from Google Calendar `freeBusy.query`
 *   4. Active `booking_holds` (pessimistic 5-minute reservations)
 *   5. Booked `assessments` rows (status='scheduled', non-null scheduled_at)
 *   6. Manual `availability_blocks` rows
 *
 * All busy ranges are expanded by `config.buffer_minutes` before subtraction.
 * The result is a list of UTC slot ranges suitable for serializing to the
 * `/api/booking/slots` response.
 *
 * NOTE: This function is intentionally split from the pure helpers above so
 * the orchestration logic can be integration-tested against miniflare D1 +
 * a mocked Google client, while the math is unit-tested against the helpers.
 */
export async function getAvailableSlots(
  env: {
    DB: D1Database
  },
  orgId: string,
  fromUtc: Date,
  toUtc: Date,
  fetchGoogleBusy: (from: Date, to: Date) => Promise<SlotRange[]>,
  now: Date = new Date(),
  config: BookingConfig = BOOKING_CONFIG
): Promise<SlotRange[]> {
  // 1. Generate candidates from weekly schedule
  const candidates = generateCandidateSlots(fromUtc, toUtc, config)

  // 2. Apply min notice / max lookahead
  const eligible = applyNoticeAndLookahead(candidates, now, config)
  if (eligible.length === 0) return []

  // 3-6. Collect busy ranges from all sources in parallel
  const [googleBusy, holds, scheduled, blocks] = await Promise.all([
    fetchGoogleBusy(fromUtc, toUtc),
    fetchActiveHolds(env.DB, orgId, fromUtc, toUtc, now),
    fetchScheduledAssessments(env.DB, orgId, fromUtc, toUtc, config.slot_minutes),
    fetchAvailabilityBlocks(env.DB, orgId, fromUtc, toUtc),
  ])

  // Combine all busy ranges and expand by buffer
  const allBusy = [...googleBusy, ...holds, ...scheduled, ...blocks]
  const expanded = expandBusyByBuffer(allBusy, config.buffer_minutes)

  // 7. Subtract from candidates
  return subtractBusyRanges(eligible, expanded)
}

async function fetchActiveHolds(
  db: D1Database,
  orgId: string,
  fromUtc: Date,
  toUtc: Date,
  now: Date
): Promise<SlotRange[]> {
  const result = await db
    .prepare(
      `SELECT slot_start_utc FROM booking_holds
        WHERE org_id = ?
          AND expires_at > ?
          AND slot_start_utc >= ?
          AND slot_start_utc < ?`
    )
    .bind(orgId, now.toISOString(), fromUtc.toISOString(), toUtc.toISOString())
    .all<{ slot_start_utc: string }>()
  // Holds reserve a slot of slot_minutes; we represent them as zero-duration
  // ranges at the slot start because the buffer-expansion will widen them
  // around the start. But that's wrong: the actual hold occupies a full slot.
  // Use slot_start_utc + BOOKING_CONFIG.slot_minutes as the end.
  const slotMs = BOOKING_CONFIG.slot_minutes * 60_000
  return result.results.map((r) => ({
    start_utc: r.slot_start_utc,
    end_utc: new Date(new Date(r.slot_start_utc).getTime() + slotMs).toISOString(),
  }))
}

async function fetchScheduledAssessments(
  db: D1Database,
  orgId: string,
  fromUtc: Date,
  toUtc: Date,
  slotMinutes: number
): Promise<SlotRange[]> {
  const result = await db
    .prepare(
      `SELECT scheduled_at FROM assessments
        WHERE org_id = ?
          AND status = 'scheduled'
          AND scheduled_at IS NOT NULL
          AND scheduled_at >= ?
          AND scheduled_at < ?`
    )
    .bind(orgId, fromUtc.toISOString(), toUtc.toISOString())
    .all<{ scheduled_at: string }>()
  const slotMs = slotMinutes * 60_000
  return result.results.map((r) => ({
    start_utc: r.scheduled_at,
    end_utc: new Date(new Date(r.scheduled_at).getTime() + slotMs).toISOString(),
  }))
}

async function fetchAvailabilityBlocks(
  db: D1Database,
  orgId: string,
  fromUtc: Date,
  toUtc: Date
): Promise<SlotRange[]> {
  const result = await db
    .prepare(
      `SELECT start_utc, end_utc FROM availability_blocks
        WHERE org_id = ?
          AND end_utc > ?
          AND start_utc < ?`
    )
    .bind(orgId, fromUtc.toISOString(), toUtc.toISOString())
    .all<{ start_utc: string; end_utc: string }>()
  return result.results
}
