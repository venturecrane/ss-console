import { describe, expect, it } from 'vitest'

import { formatDateKey, parseSlotsResponse } from '../src/scripts/slot-picker'

describe('SlotPicker response parsing', () => {
  it('accepts the booking slots response shape', () => {
    const parsed = parseSlotsResponse({
      days: [
        {
          date: '2026-07-08',
          slots: [
            {
              start_utc: '2026-07-08T16:00:00Z',
              end_utc: '2026-07-08T16:30:00Z',
              label: '9:00 AM',
            },
          ],
        },
      ],
    })

    expect(parsed.days?.[0]?.slots[0]?.label).toBe('9:00 AM')
  })

  it('rejects malformed slot payloads before rendering', () => {
    expect(() =>
      parseSlotsResponse({
        days: [{ date: 'not-a-date', slots: [{ start_utc: '', label: '9:00 AM' }] }],
      })
    ).toThrow()
  })

  it('formats local date keys for calendar button matching', () => {
    expect(formatDateKey(new Date(2026, 6, 8))).toBe('2026-07-08')
  })
})
