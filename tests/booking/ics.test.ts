import { describe, it, expect } from 'vitest'
import { buildIcs, icsToBase64 } from '../../src/lib/booking/ics'

const baseInput = {
  scheduleId: 'sched_123',
  sequence: 0,
  method: 'REQUEST' as const,
  startUtc: '2026-04-13T16:00:00.000Z',
  durationMinutes: 30,
  title: 'Assessment: Phoenix Plumbing',
  description: 'Operations cleanup intro call.',
  organizerName: 'Scott Durgan',
  organizerEmail: 'scott@smd.services',
  guestName: 'Maria Garcia',
  guestEmail: 'maria@example.com',
}

describe('buildIcs', () => {
  it('produces a valid VCALENDAR/VEVENT block', () => {
    const { ics } = buildIcs(baseInput)
    expect(ics).toContain('BEGIN:VCALENDAR')
    expect(ics).toContain('VERSION:2.0')
    expect(ics).toContain('BEGIN:VEVENT')
    expect(ics).toContain('END:VEVENT')
    expect(ics).toContain('END:VCALENDAR')
  })

  it('uses a stable UID derived from scheduleId', () => {
    const { ics } = buildIcs(baseInput)
    expect(ics).toContain('UID:booking-sched_123@smd.services')
  })

  it('UID is identical across reschedules (only SEQUENCE changes)', () => {
    const v1 = buildIcs({ ...baseInput, sequence: 0 })
    const v2 = buildIcs({ ...baseInput, sequence: 1, startUtc: '2026-04-14T16:00:00.000Z' })
    // Both contain the same UID
    expect(v1.ics).toContain('UID:booking-sched_123@smd.services')
    expect(v2.ics).toContain('UID:booking-sched_123@smd.services')
    // Sequences differ
    expect(v1.ics).toContain('SEQUENCE:0')
    expect(v2.ics).toContain('SEQUENCE:1')
  })

  it('writes DTSTART in UTC (Z suffix)', () => {
    const { ics } = buildIcs(baseInput)
    // 2026-04-13T16:00:00Z → DTSTART:20260413T160000Z
    expect(ics).toMatch(/DTSTART:20260413T160000Z/)
  })

  it('encodes the duration via DURATION:PT30M (the ics package uses duration over DTEND)', () => {
    const { ics } = buildIcs(baseInput)
    expect(ics).toContain('DURATION:PT30M')
  })

  it('METHOD=REQUEST appears for new bookings', () => {
    const { ics, contentType } = buildIcs(baseInput)
    expect(ics).toContain('METHOD:REQUEST')
    expect(contentType).toBe('text/calendar; method=REQUEST')
  })

  it('METHOD=CANCEL flips status to CANCELLED', () => {
    const { ics, contentType } = buildIcs({ ...baseInput, method: 'CANCEL' })
    expect(ics).toContain('METHOD:CANCEL')
    expect(ics).toContain('STATUS:CANCELLED')
    expect(contentType).toBe('text/calendar; method=CANCEL')
  })

  it('includes the organizer and attendee', () => {
    const { ics } = buildIcs(baseInput)
    // Unfold RFC 5545 continuation lines (a CRLF followed by space/tab)
    // before doing string contains, since long names get wrapped.
    const unfolded = ics.replace(/\r?\n[ \t]/g, '')
    expect(unfolded).toContain('Scott Durgan')
    expect(unfolded).toContain('scott@smd.services')
    expect(unfolded).toContain('Maria Garcia')
    expect(unfolded).toContain('maria@example.com')
  })

  it('throws on invalid startUtc', () => {
    expect(() => buildIcs({ ...baseInput, startUtc: 'not-a-date' })).toThrow(/invalid startUtc/)
  })

  it('throws on non-positive duration', () => {
    expect(() => buildIcs({ ...baseInput, durationMinutes: 0 })).toThrow(/durationMinutes/)
    expect(() => buildIcs({ ...baseInput, durationMinutes: -15 })).toThrow(/durationMinutes/)
  })

  it('includes location when provided (e.g., a Meet URL)', () => {
    const { ics } = buildIcs({
      ...baseInput,
      location: 'https://meet.google.com/abc-defg-hij',
    })
    expect(ics).toContain('LOCATION:https://meet.google.com/abc-defg-hij')
  })
})

describe('icsToBase64', () => {
  it('produces a valid base64 string', () => {
    const ics = 'BEGIN:VCALENDAR\nEND:VCALENDAR'
    const b64 = icsToBase64(ics)
    expect(b64).toMatch(/^[A-Za-z0-9+/]+=*$/)
    // Round trip via TextDecoder (UTF-8 aware)
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    expect(decoded).toBe(ics)
  })

  it('round-trips non-ASCII characters (UTF-8 safe)', () => {
    const ics = 'BEGIN:VCALENDAR\nSUMMARY:Meeting with Jos\u00e9 Garc\u00eda\nEND:VCALENDAR'
    const b64 = icsToBase64(ics)
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    expect(decoded).toBe(ics)
  })
})
