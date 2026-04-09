import { describe, it, expect } from 'vitest'
import {
  generateCandidateSlots,
  expandBusyByBuffer,
  subtractBusyRanges,
  applyNoticeAndLookahead,
  formatSlotLabel,
  groupSlotsByLocalDate,
  type SlotRange,
} from '../../src/lib/booking/availability'
import { BOOKING_CONFIG, type BookingConfig } from '../../src/lib/booking/config'

/**
 * Availability engine pure-function tests.
 *
 * Phoenix is permanent UTC-7 (no DST). The default config has Mon-Fri 9-16
 * working hours, 30-minute slots, 15-minute buffers, 24h min notice, 14d
 * lookahead. We verify each invariant in isolation.
 */

const TZ_PHOENIX = 'America/Phoenix'

// 2026-04-13 (Monday) at 09:00 Phoenix = 16:00 UTC
const MONDAY_9AM_UTC = '2026-04-13T16:00:00.000Z'
const MONDAY_930AM_UTC = '2026-04-13T16:30:00.000Z'
const MONDAY_10AM_UTC = '2026-04-13T17:00:00.000Z'
const MONDAY_330PM_UTC = '2026-04-13T22:30:00.000Z' // last 30-min slot starts at 15:30 PHX
const MONDAY_4PM_UTC = '2026-04-13T23:00:00.000Z' // 16:00 PHX — past end of window

describe('generateCandidateSlots', () => {
  it('produces 14 slots per workday (9:00 to 16:00, 30-min slots)', () => {
    // Generate for Monday only — full 24h window in UTC
    const from = new Date('2026-04-13T00:00:00Z')
    const to = new Date('2026-04-14T00:00:00Z')
    const slots = generateCandidateSlots(from, to)
    // 9:00 → 15:30 inclusive = 14 thirty-minute starts
    expect(slots.length).toBe(14)
    expect(slots[0].start_utc).toBe(MONDAY_9AM_UTC)
    expect(slots[1].start_utc).toBe(MONDAY_930AM_UTC)
    expect(slots[13].start_utc).toBe(MONDAY_330PM_UTC)
  })

  it('does not produce a slot whose end falls past the window end', () => {
    const from = new Date('2026-04-13T00:00:00Z')
    const to = new Date('2026-04-14T00:00:00Z')
    const slots = generateCandidateSlots(from, to)
    // The last slot starts at 15:30 PHX (22:30 UTC) and ends at 16:00 (23:00 UTC)
    // No slot starting at 16:00 PHX should appear (would end at 16:30, past window end)
    expect(slots.find((s) => s.start_utc === MONDAY_4PM_UTC)).toBeUndefined()
  })

  it('returns empty array for Saturday and Sunday', () => {
    // Saturday 2026-04-11
    const sat = generateCandidateSlots(
      new Date('2026-04-11T00:00:00Z'),
      new Date('2026-04-12T00:00:00Z')
    )
    // Sunday 2026-04-12
    const sun = generateCandidateSlots(
      new Date('2026-04-12T00:00:00Z'),
      new Date('2026-04-13T00:00:00Z')
    )
    expect(sat).toEqual([])
    expect(sun).toEqual([])
  })

  it('Phoenix has no DST — same UTC offset year-round', () => {
    // Generate for a Monday in January
    const winterSlots = generateCandidateSlots(
      new Date('2026-01-12T00:00:00Z'),
      new Date('2026-01-13T00:00:00Z')
    )
    // And for a Monday in July
    const summerSlots = generateCandidateSlots(
      new Date('2026-07-13T00:00:00Z'),
      new Date('2026-07-14T00:00:00Z')
    )
    // Both first slots should be 9am Phoenix = 16:00Z (UTC-7 always)
    expect(winterSlots[0].start_utc).toBe('2026-01-12T16:00:00.000Z')
    expect(summerSlots[0].start_utc).toBe('2026-07-13T16:00:00.000Z')
  })

  it('returns empty array when from >= to', () => {
    const t = new Date('2026-04-13T00:00:00Z')
    expect(generateCandidateSlots(t, t)).toEqual([])
    expect(generateCandidateSlots(t, new Date(t.getTime() - 1000))).toEqual([])
  })

  it('includes slots that overlap the [from, to] window even if starting outside', () => {
    // Window is Monday 17:00Z to Monday 18:00Z (mid-window)
    // Should include slots starting at 17:00 and 17:30 (= 10:00 and 10:30 PHX)
    const slots = generateCandidateSlots(
      new Date('2026-04-13T17:00:00Z'),
      new Date('2026-04-13T18:00:00Z')
    )
    expect(slots.length).toBe(2)
    expect(slots[0].start_utc).toBe('2026-04-13T17:00:00.000Z')
    expect(slots[1].start_utc).toBe('2026-04-13T17:30:00.000Z')
  })
})

describe('expandBusyByBuffer', () => {
  it('expands each busy range by bufferMinutes on both ends', () => {
    const busy: SlotRange[] = [
      { start_utc: '2026-04-13T16:00:00.000Z', end_utc: '2026-04-13T16:30:00.000Z' },
    ]
    const expanded = expandBusyByBuffer(busy, 15)
    expect(expanded[0].start_utc).toBe('2026-04-13T15:45:00.000Z')
    expect(expanded[0].end_utc).toBe('2026-04-13T16:45:00.000Z')
  })

  it('returns input unchanged when bufferMinutes is 0', () => {
    const busy: SlotRange[] = [
      { start_utc: '2026-04-13T16:00:00.000Z', end_utc: '2026-04-13T16:30:00.000Z' },
    ]
    const expanded = expandBusyByBuffer(busy, 0)
    expect(expanded).toEqual(busy)
  })

  it('does not mutate the input array', () => {
    const original: SlotRange[] = [
      { start_utc: '2026-04-13T16:00:00.000Z', end_utc: '2026-04-13T16:30:00.000Z' },
    ]
    const snapshot = JSON.parse(JSON.stringify(original))
    expandBusyByBuffer(original, 30)
    expect(original).toEqual(snapshot)
  })
})

describe('subtractBusyRanges', () => {
  const candidates: SlotRange[] = [
    { start_utc: MONDAY_9AM_UTC, end_utc: MONDAY_930AM_UTC },
    { start_utc: MONDAY_930AM_UTC, end_utc: MONDAY_10AM_UTC },
    { start_utc: MONDAY_10AM_UTC, end_utc: '2026-04-13T17:30:00.000Z' },
  ]

  it('drops a candidate that fully overlaps a busy range', () => {
    const busy: SlotRange[] = [{ start_utc: MONDAY_9AM_UTC, end_utc: MONDAY_930AM_UTC }]
    const result = subtractBusyRanges(candidates, busy)
    expect(result.find((s) => s.start_utc === MONDAY_9AM_UTC)).toBeUndefined()
    expect(result.length).toBe(2)
  })

  it('drops a candidate that partially overlaps a busy range', () => {
    // Busy range from 9:15 to 9:45 PHX (16:15 to 16:45 UTC) — partial overlap with both 9:00 and 9:30 slots
    const busy: SlotRange[] = [
      { start_utc: '2026-04-13T16:15:00.000Z', end_utc: '2026-04-13T16:45:00.000Z' },
    ]
    const result = subtractBusyRanges(candidates, busy)
    expect(result.length).toBe(1)
    expect(result[0].start_utc).toBe(MONDAY_10AM_UTC)
  })

  it('does NOT drop a candidate when busy ends exactly at slot start (touching)', () => {
    // Busy ends at 9:30, slot starts at 9:30 → overlap is empty → slot is free
    const busy: SlotRange[] = [{ start_utc: MONDAY_9AM_UTC, end_utc: MONDAY_930AM_UTC }]
    const result = subtractBusyRanges(candidates, busy)
    // The 9:30 slot should still exist
    expect(result.find((s) => s.start_utc === MONDAY_930AM_UTC)).toBeDefined()
  })

  it('does NOT drop a candidate when busy starts exactly at slot end', () => {
    // Slot 9:00-9:30, busy 9:30-10:00 → touching, no overlap
    const busy: SlotRange[] = [{ start_utc: MONDAY_930AM_UTC, end_utc: MONDAY_10AM_UTC }]
    const result = subtractBusyRanges(candidates, busy)
    expect(result.find((s) => s.start_utc === MONDAY_9AM_UTC)).toBeDefined()
    // But the 9:30 slot itself overlaps
    expect(result.find((s) => s.start_utc === MONDAY_930AM_UTC)).toBeUndefined()
  })

  it('returns input unchanged when busy is empty', () => {
    expect(subtractBusyRanges(candidates, [])).toEqual(candidates)
  })
})

describe('expansion + subtraction enforces buffer', () => {
  it('a 30-min booking with 15-min buffer blocks the slot before it AND after it', () => {
    // Suppose Scott has a booking 10:00-10:30 PHX (17:00-17:30 UTC).
    // 15-min buffer expands that to 9:45-10:45 PHX (16:45-17:45 UTC).
    // The 9:30 slot (16:30-17:00 UTC) should be blocked because 16:30 < 17:45 && 16:45 < 17:00.
    // The 10:30 slot (17:30-18:00 UTC) should be blocked because 17:30 < 17:45 && 16:45 < 18:00.
    const candidates: SlotRange[] = [
      { start_utc: '2026-04-13T16:00:00.000Z', end_utc: '2026-04-13T16:30:00.000Z' }, // 9:00
      { start_utc: '2026-04-13T16:30:00.000Z', end_utc: '2026-04-13T17:00:00.000Z' }, // 9:30
      { start_utc: '2026-04-13T17:00:00.000Z', end_utc: '2026-04-13T17:30:00.000Z' }, // 10:00 — already booked
      { start_utc: '2026-04-13T17:30:00.000Z', end_utc: '2026-04-13T18:00:00.000Z' }, // 10:30
      { start_utc: '2026-04-13T18:00:00.000Z', end_utc: '2026-04-13T18:30:00.000Z' }, // 11:00
    ]
    const busy: SlotRange[] = [
      { start_utc: '2026-04-13T17:00:00.000Z', end_utc: '2026-04-13T17:30:00.000Z' },
    ]
    const expanded = expandBusyByBuffer(busy, 15)
    const result = subtractBusyRanges(candidates, expanded)

    // 9:00 should remain (16:30 boundary just touches the 16:45 expanded busy start)
    expect(result.find((s) => s.start_utc === '2026-04-13T16:00:00.000Z')).toBeDefined()
    // 9:30, 10:00, 10:30 all blocked
    expect(result.find((s) => s.start_utc === '2026-04-13T16:30:00.000Z')).toBeUndefined()
    expect(result.find((s) => s.start_utc === '2026-04-13T17:00:00.000Z')).toBeUndefined()
    expect(result.find((s) => s.start_utc === '2026-04-13T17:30:00.000Z')).toBeUndefined()
    // 11:00 should remain (18:00 starts exactly when expanded busy ends 17:45 — no overlap)
    expect(result.find((s) => s.start_utc === '2026-04-13T18:00:00.000Z')).toBeDefined()
  })
})

describe('applyNoticeAndLookahead', () => {
  // Use a cloned config so we don't depend on the live one
  const cfg: BookingConfig = {
    ...BOOKING_CONFIG,
    min_notice_minutes: 24 * 60, // 24h
    max_lookahead_days: 14,
  }

  it('drops slots earlier than min_notice from now', () => {
    const now = new Date('2026-04-12T16:00:00Z')
    const slots: SlotRange[] = [
      { start_utc: '2026-04-13T12:00:00Z', end_utc: '2026-04-13T12:30:00Z' }, // 20h from now — too soon
      { start_utc: '2026-04-13T16:00:00Z', end_utc: '2026-04-13T16:30:00Z' }, // exactly 24h — boundary
      { start_utc: '2026-04-13T17:00:00Z', end_utc: '2026-04-13T17:30:00Z' }, // 25h — fine
    ]
    const result = applyNoticeAndLookahead(slots, now, cfg)
    expect(result.length).toBe(2)
    expect(result[0].start_utc).toBe('2026-04-13T16:00:00Z')
  })

  it('drops slots later than max_lookahead_days from now', () => {
    const now = new Date('2026-04-12T16:00:00Z')
    const slots: SlotRange[] = [
      { start_utc: '2026-04-25T16:00:00Z', end_utc: '2026-04-25T16:30:00Z' }, // 13d — fine
      { start_utc: '2026-04-26T16:00:00Z', end_utc: '2026-04-26T16:30:00Z' }, // exactly 14d — boundary, allowed
      { start_utc: '2026-04-27T16:00:00Z', end_utc: '2026-04-27T16:30:00Z' }, // 15d — too far
    ]
    const result = applyNoticeAndLookahead(slots, now, cfg)
    expect(result.length).toBe(2)
    expect(result.find((s) => s.start_utc === '2026-04-27T16:00:00Z')).toBeUndefined()
  })

  it('applies both min_notice and max_lookahead together', () => {
    const now = new Date('2026-04-12T16:00:00Z')
    const slots: SlotRange[] = [
      { start_utc: '2026-04-12T20:00:00Z', end_utc: '2026-04-12T20:30:00Z' }, // 4h — too soon
      { start_utc: '2026-04-15T16:00:00Z', end_utc: '2026-04-15T16:30:00Z' }, // 3d — fine
      { start_utc: '2026-05-01T16:00:00Z', end_utc: '2026-05-01T16:30:00Z' }, // 19d — too far
    ]
    const result = applyNoticeAndLookahead(slots, now, cfg)
    expect(result.length).toBe(1)
    expect(result[0].start_utc).toBe('2026-04-15T16:00:00Z')
  })
})

describe('formatSlotLabel', () => {
  it('renders a Phoenix slot in Phoenix tz as 9:00 AM', () => {
    expect(formatSlotLabel(MONDAY_9AM_UTC, TZ_PHOENIX)).toBe('9:00 AM')
  })

  it('renders the same UTC moment in NY as 12:00 PM (PHX is UTC-7, NY is UTC-4 in DST)', () => {
    // 16:00 UTC = 9:00 AM PHX = 12:00 PM NY (April is in EDT, UTC-4)
    expect(formatSlotLabel(MONDAY_9AM_UTC, 'America/New_York')).toBe('12:00 PM')
  })
})

describe('groupSlotsByLocalDate', () => {
  it('groups slots into local-date buckets in the viewer tz', () => {
    const slots: SlotRange[] = [
      { start_utc: '2026-04-13T16:00:00Z', end_utc: '2026-04-13T16:30:00Z' }, // Mon 9am PHX
      { start_utc: '2026-04-13T17:00:00Z', end_utc: '2026-04-13T17:30:00Z' }, // Mon 10am PHX
      { start_utc: '2026-04-14T16:00:00Z', end_utc: '2026-04-14T16:30:00Z' }, // Tue 9am PHX
    ]
    const result = groupSlotsByLocalDate(slots, TZ_PHOENIX)
    expect(result.length).toBe(2)
    expect(result[0].date).toBe('2026-04-13')
    expect(result[0].slots.length).toBe(2)
    expect(result[1].date).toBe('2026-04-14')
    expect(result[1].slots.length).toBe(1)
  })

  it('a slot near midnight UTC may land on a different local date', () => {
    // 06:00 UTC on 2026-04-14 = 23:00 on 2026-04-13 in PHX (UTC-7)
    const slots: SlotRange[] = [
      { start_utc: '2026-04-14T06:00:00Z', end_utc: '2026-04-14T06:30:00Z' },
    ]
    const result = groupSlotsByLocalDate(slots, TZ_PHOENIX)
    expect(result[0].date).toBe('2026-04-13')
  })
})
