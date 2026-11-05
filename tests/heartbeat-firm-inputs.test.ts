/**
 * The `firm_inputs` heartbeat field (firm inputs adopt live, migration 0149).
 *
 * A firm's lane configuration published to R2 reaches a running seat through
 * the root firm-inputs applier, and the way to confirm it landed is `fleet
 * status` reading `fleet_status.firm_inputs_json` back against the vault's
 * ETag. So the column must hold exactly what the seat said on its newest beat:
 * a validated `{lane: {etag8, verdict, applied_at}}` map, NULL when the beat
 * said nothing or something malformed, never a partial map. Storage through the
 * real handler is pinned by the parity gate (heartbeat-field-parity.test.ts,
 * from the manifest's sample); this pins the shape.
 *
 * FALSIFIER: accept an unknown lane, a free-text verdict, the validator's
 * detail line, or a partial map, and a case below fails.
 */

import { describe, it, expect } from 'vitest'
import { parseFirmInputs } from '../src/lib/operator/heartbeat-firm-inputs'

const OK = { etag8: '1a2b3c4d', verdict: 'fetched', applied_at: '2026-11-04T12:00:00Z' }

describe('parseFirmInputs', () => {
  it('stores a valid map canonically, keys sorted', () => {
    expect(parseFirmInputs({ records: OK, demand: OK })).toBe(
      JSON.stringify({ demand: OK, records: OK })
    )
    expect(parseFirmInputs({})).toBe('{}')
  })

  it('keeps a rejected lane with the refused version, and a lane with no live copy', () => {
    const rejected = { ...OK, verdict: 'rejected', rejected_etag8: 'badbadba' }
    expect(JSON.parse(parseFirmInputs({ records: rejected }) ?? 'null')).toEqual({
      records: rejected,
    })
    const none = { etag8: null, verdict: 'rejected', applied_at: null, rejected_etag8: 'badbadba' }
    expect(JSON.parse(parseFirmInputs({ medchron: none }) ?? 'null')).toEqual({ medchron: none })
  })

  it('stores NULL for anything malformed, never a partial map', () => {
    for (const v of [undefined, null, 'records', 3, [OK], true])
      expect(parseFirmInputs(v)).toBeNull()
    expect(parseFirmInputs({ records: OK, negotiation: OK })).toBeNull()
    expect(parseFirmInputs({ records: { ...OK, verdict: 'applied' } })).toBeNull()
    expect(parseFirmInputs({ records: { ...OK, etag8: '1A2B3C4D' } })).toBeNull()
    expect(parseFirmInputs({ records: { ...OK, etag8: '1a2b3c4' } })).toBeNull()
    expect(parseFirmInputs({ records: { ...OK, applied_at: 'yesterday' } })).toBeNull()
    expect(parseFirmInputs({ records: { ...OK, rejected_etag8: 'badbadba' } })).toBeNull()
    // The validator's words stay on the seat.
    expect(parseFirmInputs({ records: { ...OK, detail: 'records-firm.yaml: x' } })).toBeNull()
  })
})
