/**
 * tool_failing:<tool> (ss#2793 follow-on). The tri-state a page can be wrong in
 * two directions: paging on one bad recording, or emitting RECOVERED on the way
 * into an outage. Each test names the direction it forbids.
 */

import { describe, it, expect } from 'vitest'
import { conditionLabel, evaluateConditions, type FleetStatusRow } from './index'
import {
  TOOL_FAILING_MIN_FAILURES,
  parseToolFailuresMap,
  toolFailingConditions,
} from './tool-failing'

const NOW = Date.parse('2026-09-29T20:30:00.000Z')
const VOICE = 'tool_failing:voice_note_transcribe'

function row(overrides: Partial<FleetStatusRow>): FleetStatusRow {
  return {
    customer_slug: 'scott',
    last_heartbeat_ts: '2026-09-29T20:29:00.000Z',
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
    ...overrides,
  }
}

const failing = (n: number) =>
  JSON.stringify({
    voice_note_transcribe: {
      consecutive_failures: n,
      first_error_ts: '2026-09-29T20:01:12.000Z',
      last_error_ts: '2026-09-29T20:14:40.000Z',
      last_error: 'could not transcribe open-house-note.m4a: No STT provider available.',
    },
  })

describe('tool_failing:<tool>', () => {
  it('NULL map pushes nothing (whole-map hold); so does an absent column', () => {
    expect(toolFailingConditions(row({ tool_failures_json: null }))).toEqual([])
    expect(toolFailingConditions(row({}))).toEqual([])
  })

  it('a tool absent from the map pushes nothing: it did not run, which is neither failure nor recovery', () => {
    const out = toolFailingConditions(
      row({
        tool_failures_json: JSON.stringify({ record_store_write: { consecutive_failures: 0 } }),
      })
    )
    expect(out.some((c) => c.condition === VOICE)).toBe(false)
    expect(out.find((c) => c.condition === 'tool_failing:record_store_write')?.active).toBe(false)
  })

  it('one or two failures hold: no page, and no false RECOVERED on the way into an outage', () => {
    for (const n of [1, TOOL_FAILING_MIN_FAILURES - 1]) {
      const out = toolFailingConditions(row({ tool_failures_json: failing(n) }))
      expect(
        out.some((c) => c.condition === VOICE),
        `n=${n}`
      ).toBe(false)
    }
  })

  it('three consecutive failures page, naming the run and the tool’s own last error', () => {
    const out = toolFailingConditions(row({ tool_failures_json: failing(3) }))
    const c = out.find((x) => x.condition === VOICE)
    expect(c?.active).toBe(true)
    expect(c?.detail).toContain(
      '3 consecutive voice_note_transcribe failures since 2026-09-29T20:01:12.000Z'
    )
    expect(c?.detail).toContain('No STT provider available')
    expect(c?.detail).toContain('Auto-resolves on the next successful call')
  })

  it('a zero entry resolves (pushed inactive), and only a zero entry does', () => {
    const out = toolFailingConditions(
      row({
        tool_failures_json: JSON.stringify({
          voice_note_transcribe: {
            consecutive_failures: 0,
            last_ok_ts: '2026-09-29T20:20:00.000Z',
          },
        }),
      })
    )
    const c = out.find((x) => x.condition === VOICE)
    expect(c?.active).toBe(false)
    expect(c?.detail).toContain('succeeded at 2026-09-29T20:20:00.000Z')
  })

  it('a corrupt map degrades to null (hold), never a throw that aborts the fleet loop', () => {
    expect(parseToolFailuresMap('{not json')).toBeNull()
    expect(parseToolFailuresMap('[1,2]')).toBeNull()
    expect(toolFailingConditions(row({ tool_failures_json: '{not json' }))).toEqual([])
  })

  it('an entry without a count is dropped (that tool holds); the last error is bounded', () => {
    const map = parseToolFailuresMap(
      JSON.stringify({
        voice_note_transcribe: { last_error: 'no count' },
        record_store_write: { consecutive_failures: 4, last_error: 'x'.repeat(500) },
      })
    )
    expect(map).not.toBeNull()
    expect(map?.voice_note_transcribe).toBeUndefined()
    expect(map?.record_store_write.last_error).toHaveLength(200)
  })

  it('rides evaluateConditions beside the other per-row conditions', () => {
    const out = evaluateConditions([row({ tool_failures_json: failing(5) })], NOW, 300)
    expect(out.find((c) => c.condition === VOICE)?.active).toBe(true)
  })

  it('labels by the tool name', () => {
    expect(conditionLabel(VOICE)).toBe('Tool failing on every recent call: voice_note_transcribe')
  })
})
