/**
 * Composition drift guard for the admin home page (src/pages/admin/index.astro).
 *
 * Kept as a source-text test on purpose (review 2026-09-10, Testing 3,
 * 2026-09-11 conversion pass): an Astro page has no handler to invoke, and
 * what this file protects is not logic but reachability, that the launchpad
 * still deep-links the Services and Billing surfaces and still composes the
 * action queue and both revenue shapes after the lead-gen machine's
 * retirement (ADR 0060). The queue's own behaviour is tested where it is
 * built, not here.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

describe('home: admin launchpad composition (drift guard)', () => {
  const source = readFileSync(resolve('src/pages/admin/index.astro'), 'utf-8')

  it('home links to the Services and Billing surfaces', () => {
    expect(source).toContain('/admin/services')
    expect(source).toContain('/admin/billing')
  })

  it('home composes the action queue and both revenue shapes', () => {
    expect(source).toContain('Needs you today')
    expect(source).toContain('buildActionQueue')
    expect(source).toContain('One-time')
    expect(source).toContain('Recurring')
  })
})
