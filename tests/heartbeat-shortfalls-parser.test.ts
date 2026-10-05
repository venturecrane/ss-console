/**
 * The ingest's reading of the shortfall fields (2026-10-05, migration 0120).
 *
 * Every shortfall event reaches an ops inbox, so the receiver accepts only the
 * closed shape the seat promises: a known class, identifier-shaped tool and
 * routine, a code from the closed vocabulary, a stable key, and a zoned
 * timestamp. Prose (a client's name in a refusal sentence) must not survive.
 */

import { describe, it, expect } from 'vitest'
import { parseObservability } from '../src/lib/operator/heartbeat-parsers'

const beat = (extra: Record<string, unknown>) => parseObservability({ heartbeat_ts: 't', ...extra })

const ev = (over: Record<string, unknown> = {}) => ({
  ts: '2026-10-01T15:37:33+00:00',
  class: 'partial',
  tool: 'mcp_smokeball_file_attachment_pages_to_matter',
  routine: 'combined-post-intake',
  code: 'filed 0 of 52',
  key: 'partial:abc123',
  ...over,
})

describe('shortfalls at ingest', () => {
  it('keeps a well-formed event with a canonical UTC timestamp', () => {
    const parsed = JSON.parse(beat({ shortfalls_json: [ev()] }).shortfallsJson ?? 'null')
    expect(parsed).toEqual([{ ...ev(), ts: '2026-10-01T15:37:33.000Z' }])
  })

  it('accepts a person-initiated event with no routine', () => {
    const parsed = JSON.parse(
      beat({ shortfalls_json: [ev({ routine: null })] }).shortfallsJson ?? '[]'
    )
    expect(parsed[0].routine).toBeNull()
  })

  it('drops an event whose code is prose, so a client name cannot reach email', () => {
    const prose = ev({
      code: "Refused: Jane Testclient's letter, page 3 belongs to another matter",
    })
    expect(beat({ shortfalls_json: [prose, ev()] }).shortfallsJson).toBe(
      JSON.stringify([{ ...ev(), ts: '2026-10-01T15:37:33.000Z' }])
    )
  })

  it('drops an unknown class, a zone-less timestamp, and a missing key', () => {
    const bad = [ev({ class: 'oops' }), ev({ ts: '2026-10-01T15:37:33' }), ev({ key: undefined })]
    expect(beat({ shortfalls_json: bad }).shortfallsJson).toBe('[]')
  })

  it('a non-list or an over-long list is NULL (hold), never a verdict', () => {
    expect(beat({ shortfalls_json: 'nope' }).shortfallsJson).toBeNull()
    expect(
      beat({ shortfalls_json: Array.from({ length: 21 }, () => ev()) }).shortfallsJson
    ).toBeNull()
    expect(beat({}).shortfallsJson).toBeNull()
  })

  it('reads the count and zoned marker, and holds an absent count', () => {
    const o = beat({ shortfalls: 3, shortfalls_last_ts: '2026-10-01T15:37:33Z' })
    expect(o.shortfalls).toBe(3)
    expect(o.shortfallsLastTs).toBe('2026-10-01T15:37:33.000Z')
    expect(beat({}).shortfalls).toBeNull()
  })
})
