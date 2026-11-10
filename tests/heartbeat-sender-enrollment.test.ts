/**
 * The `sender_enrollment` heartbeat field (ADR 0106, migration 0151).
 *
 * A guide seat that switches invite-code enrollment on ships how many people
 * are enrolled, how many attempts failed in the trailing day, and whether the
 * broker has locked the seat. The enrollment_guessing pager acts on the column,
 * so it must hold exactly what the seat said on its newest beat: a validated
 * `{enrolled, failed_24h, locked}` object, NULL when the beat said nothing or
 * something malformed, never a partial object. Storage through the real handler
 * is pinned by the parity gate (heartbeat-field-parity.test.ts, from the
 * manifest's sample); this pins the shape.
 *
 * FALSIFIER: accept a missing key, an extra key, a negative or fractional
 * count, or a non-boolean `locked`, and a case below fails.
 */

import { describe, it, expect } from 'vitest'
import { parseSenderEnrollment } from '../src/lib/operator/heartbeat-sender-enrollment'

describe('parseSenderEnrollment', () => {
  it('stores a valid object canonically, keys in a fixed order', () => {
    expect(parseSenderEnrollment({ locked: false, failed_24h: 3, enrolled: 2 })).toBe(
      '{"enrolled":2,"failed_24h":3,"locked":false}'
    )
    expect(parseSenderEnrollment({ enrolled: 0, failed_24h: 0, locked: true })).toBe(
      '{"enrolled":0,"failed_24h":0,"locked":true}'
    )
  })

  it('stores NULL for an absent or non-object field', () => {
    for (const v of [undefined, null, 'x', 3, [], true]) {
      expect(parseSenderEnrollment(v)).toBeNull()
    }
  })

  it('refuses the whole object on any bad member', () => {
    const ok = { enrolled: 1, failed_24h: 0, locked: false }
    const bad: unknown[] = [
      { failed_24h: 0, locked: false },
      { ...ok, extra: 1 },
      { ...ok, enrolled: -1 },
      { ...ok, failed_24h: 1.5 },
      { ...ok, failed_24h: '3' },
      { ...ok, failed_24h: 2_000_000 },
      { ...ok, locked: 'true' },
      { ...ok, locked: 1 },
    ]
    for (const v of bad) expect(parseSenderEnrollment(v)).toBeNull()
  })
})
