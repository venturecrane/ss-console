import { describe, it, expect } from 'vitest'
import { existsSync } from 'fs'
import { resolve } from 'path'
import { LOST_REASONS, isLostReasonCode, lostReasonLabel } from '../src/lib/db/lost-reasons'

describe('lost-reasons: canonical enum (shared with #477)', () => {
  it('module exists', () => {
    expect(existsSync(resolve('src/lib/db/lost-reasons.ts'))).toBe(true)
  })

  it('exports the current canonical values from issue #477 and the entity-detail redesign', () => {
    const values = LOST_REASONS.map((r) => r.value).sort()
    expect(values).toEqual(
      [
        'declined-quote',
        'duplicate',
        'no-budget',
        'no-response',
        'no-signal',
        'not-a-fit',
        'other',
        'out-of-buy-box',
        'out-of-geography',
        'unreachable',
        'wrong-actor',
        'wrong-contact',
      ].sort()
    )
  })

  it('every value has a human-readable label', () => {
    for (const r of LOST_REASONS) {
      expect(typeof r.label).toBe('string')
      expect(r.label.length).toBeGreaterThan(0)
    }
  })

  it('isLostReasonCode narrows unknown input', () => {
    expect(isLostReasonCode('not-a-fit')).toBe(true)
    expect(isLostReasonCode('no-budget')).toBe(true)
    expect(isLostReasonCode('bogus')).toBe(false)
    expect(isLostReasonCode(null)).toBe(false)
    expect(isLostReasonCode(42)).toBe(false)
  })

  it('lostReasonLabel returns the canonical label', () => {
    expect(lostReasonLabel('not-a-fit')).toBe('Not a fit')
    expect(lostReasonLabel('declined-quote')).toBe('Declined quote')
  })
})
