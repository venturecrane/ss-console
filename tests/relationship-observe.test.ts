/**
 * Tests for the relationship-surface view-model
 * (src/lib/admin/relationship-observe.ts) — admin Operator console §5.6, ADR 0048.
 *
 * Two contracts matter: (1) parseStandingPreferences defensively parses the
 * opaque config_export payload — skipping rows missing id/name and normalizing
 * optional fields rather than rendering them half-formed; (2) loadStandingPreferences
 * mirrors the runtime-observe discipline — not_enabled WITHOUT a read (no audit
 * noise) when the seam is unconfigured, fail-closed to unreachable on a read
 * error, and empty/items otherwise. The transport + audit are injected stubs.
 */

import { describe, it, expect } from 'vitest'
import {
  parseStandingPreferences,
  loadStandingPreferences,
  parseLearnedPreferences,
  loadLearnedPreferences,
} from '../src/lib/admin/relationship-observe'
import type {
  MachineRuntimeTransport,
  RuntimeReadAudit,
  RuntimeReadActor,
} from '../src/lib/operator/runtime-read'

const actor: RuntimeReadActor = { actor: 'captain@example.com', actorRole: 'admin' }

function countingAudit(): { audit: RuntimeReadAudit; getCalls: () => number } {
  let calls = 0
  return { audit: { record: async () => void (calls += 1) }, getCalls: () => calls }
}

function transportReturning(data: unknown): MachineRuntimeTransport {
  return { read: async () => ({ data }) }
}

const aPerson = (over: Record<string, unknown> = {}) => ({
  id: 'scott-durgan',
  name: 'Scott Durgan',
  role: 'Principal',
  prefers: ['Lead with the material change'],
  avoid: ['Inventing estimates'],
  ...over,
})

describe('parseStandingPreferences', () => {
  it('parses a valid person', () => {
    const out = parseStandingPreferences({ entries: [aPerson()] })
    expect(out).toEqual([
      {
        id: 'scott-durgan',
        name: 'Scott Durgan',
        role: 'Principal',
        prefers: ['Lead with the material change'],
        avoid: ['Inventing estimates'],
      },
    ])
  })

  it('normalizes optional fields (absent role → null, lists → [])', () => {
    const out = parseStandingPreferences({ entries: [{ id: 'p1', name: 'P' }] })
    expect(out[0]).toEqual({ id: 'p1', name: 'P', role: null, prefers: [], avoid: [] })
  })

  it('drops non-string list items', () => {
    const out = parseStandingPreferences({ entries: [aPerson({ prefers: ['ok', 42, ''] })] })
    expect(out[0].prefers).toEqual(['ok'])
  })

  it('skips rows missing id or name', () => {
    expect(parseStandingPreferences({ entries: [{ name: 'no id' }] })).toEqual([])
    expect(parseStandingPreferences({ entries: [{ id: 'no-name' }] })).toEqual([])
  })

  it('is total on malformed payloads', () => {
    expect(parseStandingPreferences(null)).toEqual([])
    expect(parseStandingPreferences({ entries: 'nope' })).toEqual([])
    expect(parseStandingPreferences({ entries: [null, 7] })).toEqual([])
  })
})

describe('loadStandingPreferences', () => {
  it('returns not_enabled WITHOUT a read when unconfigured', async () => {
    const { audit, getCalls } = countingAudit()
    const res = await loadStandingPreferences(
      { transport: transportReturning({ entries: [aPerson()] }), audit },
      'smd',
      actor,
      false
    )
    expect(res).toEqual({ status: 'not_enabled' })
    expect(getCalls()).toBe(0)
  })

  it('classifies people as items', async () => {
    const { audit } = countingAudit()
    const res = await loadStandingPreferences(
      { transport: transportReturning({ entries: [aPerson()] }), audit },
      'smd',
      actor,
      true
    )
    expect(res.status).toBe('items')
    if (res.status === 'items') expect(res.people).toHaveLength(1)
  })

  it('classifies no people as empty', async () => {
    const { audit } = countingAudit()
    const res = await loadStandingPreferences(
      { transport: transportReturning({ entries: [] }), audit },
      'smd',
      actor,
      true
    )
    expect(res).toEqual({ status: 'empty' })
  })

  it('fails closed to unreachable on a read error', async () => {
    const { audit } = countingAudit()
    const transport: MachineRuntimeTransport = {
      read: async () => {
        throw new Error('boom')
      },
    }
    const res = await loadStandingPreferences({ transport, audit }, 'smd', actor, true)
    expect(res.status).toBe('unreachable')
  })
})

const aRow = (over: Record<string, unknown> = {}) => ({
  id: 'row-1',
  customer_slug: 'smd',
  peer_id: 'chris@ashtonprice.com',
  persona_slug: 'default',
  preference: 'Highlight, never draft',
  why: 'He is the litigator and wants control of the words',
  how_to_apply: 'Surface the matter, leave the drafting to him',
  source: 'stated',
  session_id: 'sess-1',
  recorded_at: '2026-06-15T10:00:00Z',
  superseded_by: null,
  ...over,
})

describe('parseLearnedPreferences', () => {
  it('parses a valid active row', () => {
    const out = parseLearnedPreferences({ entries: [aRow()] })
    expect(out).toEqual([
      {
        peerId: 'chris@ashtonprice.com',
        preferences: [
          {
            preference: 'Highlight, never draft',
            why: 'He is the litigator and wants control of the words',
            howToApply: 'Surface the matter, leave the drafting to him',
            source: 'stated',
            recordedAt: '2026-06-15T10:00:00Z',
          },
        ],
      },
    ])
  })

  it("FALSIFIER: skips kind 'memory' rows (a space-mode seat's remembered items, ADR 0091)", () => {
    const out = parseLearnedPreferences({
      entries: [
        { peer_id: 'p1', preference: 'Private note', source: 'stated', kind: 'memory' },
        { peer_id: 'p1', preference: 'Short replies', source: 'stated' },
      ],
    })
    expect(out.map((p) => p.preferences.map((x) => x.preference))).toEqual([['Short replies']])
  })

  it('normalizes optional fields (absent why/how_to_apply/recorded_at → null)', () => {
    const out = parseLearnedPreferences({
      entries: [{ peer_id: 'p1', preference: 'P', source: 'demonstrated' }],
    })
    expect(out[0].preferences[0]).toEqual({
      preference: 'P',
      why: null,
      howToApply: null,
      source: 'demonstrated',
      recordedAt: null,
    })
  })

  it('skips rows missing peer_id or preference', () => {
    expect(parseLearnedPreferences({ entries: [aRow({ peer_id: undefined })] })).toEqual([])
    expect(parseLearnedPreferences({ entries: [aRow({ preference: '' })] })).toEqual([])
  })

  it('filters out superseded rows (non-null superseded_by)', () => {
    const out = parseLearnedPreferences({
      entries: [aRow({ superseded_by: 'row-9', preference: 'old' }), aRow({ preference: 'new' })],
    })
    expect(out).toHaveLength(1)
    expect(out[0].preferences.map((p) => p.preference)).toEqual(['new'])
  })

  it('skips rows with an invalid source', () => {
    expect(parseLearnedPreferences({ entries: [aRow({ source: 'guessed' })] })).toEqual([])
    expect(parseLearnedPreferences({ entries: [aRow({ source: null })] })).toEqual([])
  })

  it('groups multiple rows by peer_id', () => {
    const out = parseLearnedPreferences({
      entries: [
        aRow({ peer_id: 'a', preference: 'a1' }),
        aRow({ peer_id: 'b', preference: 'b1' }),
        aRow({ peer_id: 'a', preference: 'a2' }),
      ],
    })
    const byPeer = Object.fromEntries(out.map((p) => [p.peerId, p.preferences.length]))
    expect(byPeer).toEqual({ a: 2, b: 1 })
  })

  it('sorts a peer preferences newest-first, null recorded_at last', () => {
    const out = parseLearnedPreferences({
      entries: [
        aRow({ preference: 'older', recorded_at: '2026-06-10T00:00:00Z' }),
        aRow({ preference: 'no-date', recorded_at: undefined }),
        aRow({ preference: 'newer', recorded_at: '2026-06-15T00:00:00Z' }),
      ],
    })
    expect(out[0].preferences.map((p) => p.preference)).toEqual(['newer', 'older', 'no-date'])
  })

  it('is total on malformed payloads', () => {
    expect(parseLearnedPreferences(null)).toEqual([])
    expect(parseLearnedPreferences({ entries: 'nope' })).toEqual([])
    expect(parseLearnedPreferences({ entries: [null, 7] })).toEqual([])
  })
})

describe('loadLearnedPreferences', () => {
  it('returns not_enabled WITHOUT a read when unconfigured', async () => {
    const { audit, getCalls } = countingAudit()
    const res = await loadLearnedPreferences(
      { transport: transportReturning({ entries: [aRow()] }), audit },
      'smd',
      actor,
      false
    )
    expect(res).toEqual({ status: 'not_enabled' })
    expect(getCalls()).toBe(0)
  })

  it('classifies people as items', async () => {
    const { audit } = countingAudit()
    const res = await loadLearnedPreferences(
      { transport: transportReturning({ entries: [aRow()] }), audit },
      'smd',
      actor,
      true
    )
    expect(res.status).toBe('items')
    if (res.status === 'items') expect(res.people).toHaveLength(1)
  })

  it('classifies no people as empty', async () => {
    const { audit } = countingAudit()
    const res = await loadLearnedPreferences(
      { transport: transportReturning({ entries: [] }), audit },
      'smd',
      actor,
      true
    )
    expect(res).toEqual({ status: 'empty' })
  })

  it('fails closed to unreachable on a read error', async () => {
    const { audit } = countingAudit()
    const transport: MachineRuntimeTransport = {
      read: async () => {
        throw new Error('boom')
      },
    }
    const res = await loadLearnedPreferences({ transport, audit }, 'smd', actor, true)
    expect(res.status).toBe('unreachable')
  })
})
