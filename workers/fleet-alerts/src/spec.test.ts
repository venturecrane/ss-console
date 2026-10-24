/**
 * spec_unverified (migration 0140). The page can be wrong three ways: silent
 * on a spec that does not verify, paging while a seat is still picking up a
 * just-published pointer, or resolving on a NULL or an unread pointer. Each
 * test names the direction it forbids.
 */

import { describe, it, expect } from 'vitest'
import { conditionLabel, type FleetStatusRow } from './index'
import { evaluateSpec, parseSpecVerdict, pointerSpecSha, type PublishedPointer } from './spec'

const NOW = Date.parse('2026-10-23T12:05:00.000Z')
const OPTS = { nowMs: NOW, redSeconds: 600, graceSeconds: 900 }
const SHA_A = 'a'.repeat(64)
const SHA_B = 'b'.repeat(64)

function row(spec: string | null | undefined, over: Partial<FleetStatusRow> = {}): FleetStatusRow {
  return {
    customer_slug: 'seat-alpha',
    last_heartbeat_ts: '2026-10-23T12:04:00.000Z',
    sticky_stop_level: 'OK',
    sticky_stop_reason: null,
    sticky_stop_condition: null,
    scheduler_ok: null,
    scheduler_max_overdue_seconds: null,
    connectors_json: null,
    connector_check_ok: null,
    connector_token_age_json: null,
    spec_control_json: null,
    spec_control_ok: null,
    webhook_surface_json: null,
    webhook_surface_ok: null,
    gateway_loop_ok: null,
    gateway_loop_age_seconds: null,
    gateway_supervisor_state: null,
    gateway_restarts_last_hour: null,
    spec_json: spec,
    ...over,
  }
}

function verdict(over: Record<string, unknown> = {}): string {
  return JSON.stringify({
    v: 1,
    verdict: 'verified',
    checked_at: '2026-10-23T12:03:00.000Z',
    spec_sha256: SHA_A,
    key_id: '15850dd6829dc7ee',
    compiled_from: '1'.repeat(40),
    unmet_count: 0,
    unmet: [],
    customer_yaml_match: true,
    ...over,
  })
}

const published = (sha: string | null, minutesAgo: number): PublishedPointer => ({
  specSha256: sha,
  uploadedMs: NOW - minutesAgo * 60_000,
})

function state(spec: string | null | undefined, pointer: PublishedPointer, over = {}) {
  const out = evaluateSpec([row(spec, over)], new Map([['seat-alpha', pointer]]), OPTS)
  return out.length ? out[0] : null
}

describe('spec_unverified: holds', () => {
  it('a NULL verdict pushes nothing (never a false RECOVERED)', () => {
    expect(state(null, published(SHA_A, 60))).toBeNull()
    expect(state(undefined, published(SHA_A, 60))).toBeNull()
  })

  it('a corrupt verdict pushes nothing rather than throwing', () => {
    expect(state('{not json', published(SHA_A, 60))).toBeNull()
    expect(state(verdict({ verdict: 'trust-me' }), published(SHA_A, 60))).toBeNull()
  })

  it('a heartbeat past the red threshold holds (heartbeat_red pages for it)', () => {
    const old = { last_heartbeat_ts: '2026-10-23T11:00:00.000Z' }
    expect(state(verdict({ verdict: 'bad_signature' }), published(SHA_A, 60), old)).toBeNull()
  })

  it('an unread pointer holds the comparison', () => {
    expect(state(verdict(), undefined)).toBeNull()
  })

  it('a mismatch inside the grace window holds: the seat is still picking it up', () => {
    expect(state(verdict(), published(SHA_B, 5))).toBeNull()
  })
})

describe('spec_unverified: pages', () => {
  for (const v of [
    'bad_signature',
    'bad_digest',
    'unknown_key',
    'wrong_seat',
    'spec_missing',
    'malformed',
    'stale',
    'unreadable',
  ]) {
    it(`verdict ${v} pages on the first reading, whatever the pointer says`, () => {
      const s = state(verdict({ verdict: v }), published(SHA_A, 60))
      expect(s?.active).toBe(true)
      expect(s?.detail).toContain(`verdict ${v}`)
    })
  }

  it('an unmet requirement pages and names what is unmet', () => {
    const s = state(
      verdict({ verdict: 'unmet_requirement', unmet: ['lane:demand'], unmet_count: 1 }),
      published(SHA_A, 60)
    )
    expect(s?.active).toBe(true)
    expect(s?.detail).toContain('lane:demand')
  })

  it('a seat holding an older spec than the pointer, past the grace, pages', () => {
    const s = state(verdict(), published(SHA_B, 30))
    expect(s?.active).toBe(true)
    expect(s?.detail).toContain('has not picked up the published spec')
  })

  it('an R2 outage on the seat that outlives the grace pages through the comparison', () => {
    expect(state(verdict({ verdict: 'fetch_error' }), published(SHA_B, 30))?.active).toBe(true)
  })

  it('a seat holding a spec with no pointer published pages', () => {
    expect(state(verdict(), null)?.active).toBe(true)
  })
})

describe('spec_unverified: resolves', () => {
  it('a seat that verified the published spec resolves', () => {
    expect(state(verdict(), published(SHA_A, 60))?.active).toBe(false)
  })

  it('nothing published and nothing held is not a page', () => {
    expect(state(verdict({ verdict: 'absent', spec_sha256: null }), null)?.active).toBe(false)
  })

  it('an R2 blip on a seat still holding the published spec does not page', () => {
    expect(state(verdict({ verdict: 'fetch_error' }), published(SHA_A, 60))?.active).toBe(false)
  })
})

describe('spec_unverified: parsing and labels', () => {
  it('reads a pointer digest, never a malformed one', () => {
    expect(pointerSpecSha(JSON.stringify({ spec_sha256: SHA_A }))).toBe(SHA_A)
    expect(pointerSpecSha(JSON.stringify({ spec_sha256: 'short' }))).toBeNull()
    expect(pointerSpecSha('nope')).toBeNull()
  })

  it('parses the stored verdict shape the ingest writes', () => {
    expect(parseSpecVerdict(verdict())?.spec_sha256).toBe(SHA_A)
  })

  it('has a human label for the page', () => {
    expect(conditionLabel('spec_unverified')).toBe('Seat cannot verify its signed config spec')
  })
})
