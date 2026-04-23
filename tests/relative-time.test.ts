import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { relativeTime } from '../src/lib/admin/relative-time'

/**
 * Narrow behavior tests for the admin relative-time formatter.
 * Pinned to a fixed "now" so output is deterministic in CI.
 */
describe('admin/relative-time', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-23T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns null for null / undefined / empty string', () => {
    expect(relativeTime(null)).toBeNull()
    expect(relativeTime(undefined)).toBeNull()
    expect(relativeTime('')).toBeNull()
  })

  it('returns null for unparseable input', () => {
    expect(relativeTime('not-a-date')).toBeNull()
  })

  it('renders "just now" for the very recent past', () => {
    expect(relativeTime('2026-04-23T11:45:00Z')).toBe('just now')
  })

  it('renders "Xh ago" within the same day', () => {
    expect(relativeTime('2026-04-23T09:00:00Z')).toBe('3h ago')
  })

  it('renders "Xd ago" for multi-day age', () => {
    expect(relativeTime('2026-04-21T12:00:00Z')).toBe('2d ago')
  })

  it('treats future timestamps as "just now" (does not render negative ages)', () => {
    expect(relativeTime('2026-04-23T13:00:00Z')).toBe('just now')
  })
})
