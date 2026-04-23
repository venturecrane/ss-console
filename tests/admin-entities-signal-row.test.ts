/**
 * Static checks for the admin Signal row redesign (issue #462).
 *
 * Verifies the file contains the expected redesign markers — stretched
 * anchor wrapping the row, chip-based top-problem rendering sourced from
 * pipeline metadata, and conditional rendering (never placeholder text)
 * for outreach_angle, website, phone, and last_activity_at.
 *
 * These checks are intentionally string-based rather than DOM-rendered:
 * they defend against accidental Pattern A/B regressions across future
 * AI-authored edits without requiring a full Astro render harness.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const source = readFileSync(resolve('src/pages/admin/entities/index.astro'), 'utf-8')

describe('admin entities index — Signal row redesign (#462)', () => {
  it('hydrates signal rows with pipeline metadata via the DAL helper', () => {
    expect(source).toContain('getSignalMetadataForEntities')
  })

  it('only fetches signal metadata when viewing the Signal tab', () => {
    // Avoid paying the query cost on Prospect/other stages.
    expect(source).toMatch(/filterStage === 'signal'/)
  })

  it('uses a stretched anchor so the whole row is clickable', () => {
    expect(source).toMatch(/absolute inset-0/)
    expect(source).toMatch(/aria-label=\{`Open \$\{e\.name\}`\}/)
  })

  it('keeps action buttons / website / phone interactive above the stretched link', () => {
    // z-10 on the buttons and inline links layers them above the anchor
    expect(source).toMatch(/relative z-10/)
  })

  it('renders top-problem chips only when pipeline metadata supplied them', () => {
    // Guarded by `problems.length > 0` so an empty list renders nothing.
    expect(source).toMatch(/problems\.length > 0/)
  })

  it('renders outreach angle with 2-line truncation when present', () => {
    expect(source).toMatch(/outreachAngle/)
    expect(source).toMatch(/line-clamp-2/)
  })

  it('renders last activity date only when at least one context entry exists', () => {
    // `lastActivity ? <Last activity> : <created_at>` — falls back to
    // the entity's own created_at rather than inventing a placeholder.
    expect(source).toMatch(/Last activity/)
    expect(source).toMatch(/lastActivity \? \(/)
  })

  it('does not emit placeholder fallback strings for missing signal fields', () => {
    // Pattern B guard — no "TBD" / "No signal" style invented labels.
    expect(source).not.toMatch(/TBD|No signal|Unknown problem/i)
  })

  it('preserves the existing Promote + Dismiss forms on the Signal row', () => {
    expect(source).toMatch(/\/promote`/)
    expect(source).toMatch(/\/dismiss`/)
  })
})
