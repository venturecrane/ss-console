import { describe, it, expect } from 'vitest'
import { dueDateToStripeTimestamp, isIsoCalendarDate } from './due-date'

describe('isIsoCalendarDate', () => {
  it('accepts a real YYYY-MM-DD date', () => {
    expect(isIsoCalendarDate('2026-09-09')).toBe(true)
    expect(isIsoCalendarDate('2028-02-29')).toBe(true)
  })

  it('rejects non-dates and impossible dates', () => {
    expect(isIsoCalendarDate('')).toBe(false)
    expect(isIsoCalendarDate('09/09/2026')).toBe(false)
    expect(isIsoCalendarDate('2026-9-9')).toBe(false)
    expect(isIsoCalendarDate('2026-02-30')).toBe(false)
    expect(isIsoCalendarDate('2026-13-01')).toBe(false)
    expect(isIsoCalendarDate('2026-09-09T00:00:00Z')).toBe(false)
  })
})

describe('dueDateToStripeTimestamp', () => {
  it('is the end of that day in Phoenix (UTC-7, no DST) by default', () => {
    // 2026-09-09 23:59:59 America/Phoenix = 2026-09-10T06:59:59Z
    expect(dueDateToStripeTimestamp('2026-09-09')).toBe(Date.UTC(2026, 8, 10, 6, 59, 59) / 1000)
    // Phoenix does not observe DST: January carries the same offset.
    expect(dueDateToStripeTimestamp('2026-01-15')).toBe(Date.UTC(2026, 0, 16, 6, 59, 59) / 1000)
  })

  it('honors an explicit timezone, including one that observes DST', () => {
    expect(dueDateToStripeTimestamp('2026-09-09', 'UTC')).toBe(
      Date.UTC(2026, 8, 9, 23, 59, 59) / 1000
    )
    // New York is UTC-4 in September, UTC-5 in January.
    expect(dueDateToStripeTimestamp('2026-09-09', 'America/New_York')).toBe(
      Date.UTC(2026, 8, 10, 3, 59, 59) / 1000
    )
    expect(dueDateToStripeTimestamp('2026-01-15', 'America/New_York')).toBe(
      Date.UTC(2026, 0, 16, 4, 59, 59) / 1000
    )
  })

  it('returns null for anything that is not a calendar date', () => {
    expect(dueDateToStripeTimestamp('')).toBeNull()
    expect(dueDateToStripeTimestamp('next week')).toBeNull()
    expect(dueDateToStripeTimestamp('2026-02-30')).toBeNull()
  })
})
