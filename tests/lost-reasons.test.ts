import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'fs'
import { resolve } from 'path'
import { LOST_REASONS, isLostReason, labelForLostReason } from '../src/lib/db/lost-reasons'

describe('lost-reasons: canonical enum (shared with #477)', () => {
  it('module exists', () => {
    expect(existsSync(resolve('src/lib/db/lost-reasons.ts'))).toBe(true)
  })

  it('exports the seven canonical values from issue #477', () => {
    const values = LOST_REASONS.map((r) => r.value).sort()
    expect(values).toEqual(
      [
        'declined-quote',
        'no-budget',
        'no-response',
        'not-a-fit',
        'other',
        'unreachable',
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

  it('isLostReason narrows unknown input', () => {
    expect(isLostReason('not-a-fit')).toBe(true)
    expect(isLostReason('no-budget')).toBe(true)
    expect(isLostReason('bogus')).toBe(false)
    expect(isLostReason(null)).toBe(false)
    expect(isLostReason(42)).toBe(false)
  })

  it('labelForLostReason returns the canonical label', () => {
    expect(labelForLostReason('not-a-fit')).toBe('Not a fit')
    expect(labelForLostReason('declined-quote')).toBe('Declined quote')
  })

  it('source does not invent extra values (single source of truth)', () => {
    // Guard: the CLAUDE.md constraint says do NOT invent a second list.
    // If #477 extends this file, that's fine — but no other source file
    // should declare the enum.
    const repoFiles = [
      'src/lib/db/entities-bulk.ts',
      'src/pages/api/admin/entities/bulk.ts',
      'src/pages/admin/entities/index.astro',
    ]
    for (const f of repoFiles) {
      const src = readFileSync(resolve(f), 'utf-8')
      // These files must reference the canonical module, not re-declare.
      expect(src).toContain('lost-reasons')
    }
  })
})
