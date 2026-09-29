/**
 * The ingest's reading of `tool_failures` (ss#2793 follow-on, migration 0119).
 *
 * The parity gate proves the sample lands in its column; this pins the
 * three-tier rule the alerter depends on, each tier in the direction that
 * would page wrongly if it flipped: a junk map must hold (NULL), never resolve;
 * an empty map must be preserved as `{}` (the seat's real "nothing ran");
 * a half entry must be dropped, not defaulted into a verdict; and the tool's
 * own refusal text is bounded by the receiver, not trusted by size.
 */

import { describe, it, expect } from 'vitest'
import { parseObservability } from '../src/lib/operator/heartbeat-parsers'

const beat = (tool_failures: unknown) => parseObservability({ heartbeat_ts: 't', tool_failures })

describe('tool_failures at ingest', () => {
  it('absent, non-object, or array → NULL (hold, never a verdict)', () => {
    expect(beat(undefined).toolFailuresJson).toBeNull()
    expect(beat('nope').toolFailuresJson).toBeNull()
    expect(beat([1]).toolFailuresJson).toBeNull()
    expect(beat(null).toolFailuresJson).toBeNull()
  })

  it('an empty map is preserved as {} (watched, nothing ran today)', () => {
    expect(beat({}).toolFailuresJson).toBe('{}')
  })

  it('keeps a well-formed run entry with canonical UTC timestamps', () => {
    const json = beat({
      voice_note_transcribe: {
        consecutive_failures: 3,
        first_error_ts: '2026-09-29T20:01:12.000Z',
        last_error_ts: '2026-09-29T13:14:40-07:00',
        last_error: 'could not transcribe memo.m4a: No STT provider available.',
      },
    }).toolFailuresJson
    expect(JSON.parse(json!)).toEqual({
      voice_note_transcribe: {
        consecutive_failures: 3,
        first_error_ts: '2026-09-29T20:01:12.000Z',
        last_error_ts: '2026-09-29T20:14:40.000Z',
        last_error: 'could not transcribe memo.m4a: No STT provider available.',
      },
    })
  })

  it('keeps the zero entry that resolves an alert, with its last_ok_ts', () => {
    const json = beat({
      record_store_write: { consecutive_failures: 0, last_ok_ts: '2026-09-29T19:20:07.000Z' },
    }).toolFailuresJson
    expect(JSON.parse(json!)).toEqual({
      record_store_write: { consecutive_failures: 0, last_ok_ts: '2026-09-29T19:20:07.000Z' },
    })
  })

  it('drops an entry with no count and a key that is not a tool name; keeps its neighbours', () => {
    const json = beat({
      voice_note_transcribe: { last_error: 'no count' },
      'Not A Tool': { consecutive_failures: 9 },
      record_store_write: { consecutive_failures: -1 },
      ok_tool: { consecutive_failures: 2, last_error_ts: 'yesterday' },
    }).toolFailuresJson
    expect(JSON.parse(json!)).toEqual({ ok_tool: { consecutive_failures: 2 } })
  })

  it('bounds the refusal text at 200 characters', () => {
    const json = beat({
      t: { consecutive_failures: 1, last_error: 'x'.repeat(500) },
    }).toolFailuresJson
    expect(JSON.parse(json!).t.last_error).toHaveLength(200)
  })

  it('a map with more tools than any seat watches → NULL', () => {
    const big: Record<string, unknown> = {}
    for (let i = 0; i < 33; i++) big[`tool_${i}`] = { consecutive_failures: 0 }
    expect(beat(big).toolFailuresJson).toBeNull()
  })
})
