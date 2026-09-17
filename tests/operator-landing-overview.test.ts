import { describe, it, expect } from 'vitest'
import {
  resolveOperatorOverview,
  formatPublishedDate,
  jobLines,
} from '../src/lib/portal/operator/facets/overview/overview'
import type { CustomerConfigRow, PersonaConfig } from '../src/lib/portal/customer-config'

/**
 * Operator landing overview resolver (console blueprint §5 — the one-pager).
 * Every summary must derive from the same shared facet resolvers the chapters
 * render; counts are pure arithmetic over authored tiers, never invented.
 * Fixtures carry NO vertical vocabulary.
 */

function persona(p: Partial<PersonaConfig>): PersonaConfig {
  return {
    slug: 'p',
    status: 'active',
    name: 'X',
    title: null,
    signature_html: null,
    tone: [],
    send_as: null,
    entitlements: { exposure: {} },
    skills: [],
    channel_bindings: [],
    ...p,
  }
}

function gridRow(p: Record<string, unknown>): Record<string, unknown> {
  return {
    routine: 'A routine',
    letter_section: 'Section one',
    skills: [],
    start_tier: 'flag-only',
    ceiling_tier: 'flag-only',
    start_verbatim: 'x',
    ceiling_verbatim: 'x',
    enforcement: {
      initiation: 'manual',
      exposure_keys: {},
      content_floor: false,
      banned_tools: [],
      notes: '',
    },
    ...p,
  }
}

function config(p: Partial<Record<string, unknown>>): CustomerConfigRow {
  return {
    personas: [],
    routine_grid: null,
    connectors: null,
    scope: null,
    synced_at: '2026-07-14T23:59:59.000Z',
    ...p,
  } as unknown as CustomerConfigRow
}

describe('formatPublishedDate', () => {
  it('formats the projection timestamp as a long UTC date', () => {
    expect(formatPublishedDate('2026-07-14T23:59:59.000Z')).toBe('July 14, 2026')
  })
  it('is null for missing or unparseable input (the stamp line never fabricates)', () => {
    expect(formatPublishedDate(null)).toBeNull()
    expect(formatPublishedDate(undefined)).toBeNull()
    expect(formatPublishedDate('not-a-date')).toBeNull()
  })
})

describe('jobLines', () => {
  it('derives grid counts into the headline + tier detail (zero tiers omitted)', () => {
    const lines = jobLines({
      kind: 'grid',
      duties: 19,
      stages: 8,
      handles: 2,
      prepares: 14,
      surfaces: 3,
    })
    expect(lines).toEqual({
      headline: '19 duties across 8 stages of your work.',
      detail: '2 handled on its own · 14 prepared for a person · 3 surfaced for you',
    })
    expect(
      jobLines({ kind: 'grid', duties: 1, stages: 1, handles: 0, prepares: 1, surfaces: 0 })
    ).toEqual({
      headline: '1 duty across 1 stage of your work.',
      detail: '1 prepared for a person',
    })
  })
  it('is null when there is nothing to summarize (block absent, empty-chapter rule)', () => {
    expect(jobLines({ kind: 'skills', count: 0 })).toBeNull()
    expect(
      jobLines({ kind: 'grid', duties: 0, stages: 0, handles: 0, prepares: 0, surfaces: 0 })
    ).toBeNull()
  })
  it('summarizes gridless seats by configured-duty count', () => {
    expect(jobLines({ kind: 'skills', count: 7 })).toEqual({
      headline: '7 duties configured.',
      detail: null,
    })
  })
})

describe('resolveOperatorOverview', () => {
  it('derives grid job numbers, authority, systems, and rosters from the shared resolvers', () => {
    const model = resolveOperatorOverview(
      config({
        routine_grid: {
          adr: '0075',
          seat: 's',
          persona: 'p',
          source_letter: 'x',
          rows: [
            gridRow({ routine: 'One', start_tier: 'prepare-and-route' }),
            gridRow({ routine: 'Two', letter_section: 'Section two', start_tier: 'auto-handle' }),
            gridRow({ routine: 'Three', start_tier: 'flag-only' }),
          ],
        },
        // `autonomous` writing, so the dial-less rows in this fixture resolve to
        // their authored tiers; a held write caps them at prepare-and-route.
        personas: [
          persona({ slug: 'p', entitlements: { exposure: { internal_write: 'autonomous' } } }),
        ],
        connectors: { PracticeManagement: { adapter: 'x' }, Email: { adapter: 'y' } },
        scope: {
          inbound_allow_from: ['@firm.example'],
          outbound_roster: [{ address: 'a@b.example', class: 'client' }],
        },
      })
    )
    expect(model.job).toEqual({
      kind: 'grid',
      duties: 3,
      stages: 2,
      handles: 1,
      prepares: 1,
      surfaces: 1,
    })
    expect(model.authority).toEqual([
      { label: 'Writing inside your systems', sentence: 'Handles it on its own' },
    ])
    expect(model.systems).toEqual(['Email', 'Practice management'])
    expect(model.respondsTo).toEqual(['@firm.example'])
    expect(model.writesToCount).toBe(1)
    expect(model.publishedOn).toBe('July 14, 2026')
  })

  it('is fully empty-safe for a null config (nothing fabricated)', () => {
    const model = resolveOperatorOverview(null)
    expect(model.job).toEqual({ kind: 'skills', count: 0 })
    expect(model.authority).toEqual([])
    expect(model.systems).toEqual([])
    expect(model.respondsTo).toEqual([])
    expect(model.writesToCount).toBe(0)
    expect(model.publishedOn).toBeNull()
  })
})
