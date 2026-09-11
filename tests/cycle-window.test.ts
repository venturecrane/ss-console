/**
 * The console's half of the three-way window agreement (ss#2618).
 *
 * Reads the SAME fixture as `operator/workspace_broker/tests/test_cycle_window.py`
 * and pins the SAME digest. If the two pins ever differ, the fixture moved in one
 * repo and not the other, and the surfaces have silently stopped agreeing about
 * which rows a period holds.
 *
 * Three checks, none of which subsumes the others:
 *  - the READER, where divergence actually lives (a quoted `"15"` reads as 15 on
 *    a coercing surface and as nothing on a type-checking one);
 *  - the ARITHMETIC, against hand-authored expectations, which is what catches a
 *    chained implementation (Feb 28 -> Mar 28 instead of returning to the 31st);
 *  - the TILING PROPERTY, exhaustively, which is what catches gaps and overlaps
 *    that a hand-written table can be wrong about in the same way on every side.
 */
import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  AnchorInvalid,
  cycleWindow,
  resolveAnchor,
  resolveEffectiveFrom,
  ANCHOR_SETTING,
  EFFECTIVE_FROM_SETTING,
} from '../src/lib/admin/cycle-window'

const FIXTURE_PATH = resolve('operator/contracts/cycle_window_fixture.json')

// MUST equal PINNED_CONTENT_SHA256 in
// operator/workspace_broker/tests/test_cycle_window.py and _PINNED_CONTENT_SHA256
// in the engagements pipeline's vendored copy.
const PINNED_CONTENT_SHA256 = 'f7c10e5a2d0481b7720f8efbbdd1b17b4a9baa61898631ed2718a1d52f8ff2dd'

interface ReaderCase {
  why: string
  settings: Record<string, unknown>
  anchor?: number | null
  value?: string | null
  invalid?: boolean
  asymmetric?: { python: string; ts: string }
}
interface WindowCase {
  why: string
  now: string
  anchor: number | null
  effective_from?: string
  start: string
  end: string
  label: string
}

/** `{"__float__": N}` means the native float N - see the fixture readme. JS has
 * one number type, so this collapses to the integer here; the tag exists so the
 * Python side can still tell them apart rather than silently passing a vector
 * that proves nothing. */
function materialise(settings: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(settings ?? {})) {
    if (v !== null && typeof v === 'object' && '__float__' in v) {
      out[k] = Number((v as { __float__: number }).__float__)
    } else out[k] = v
  }
  return out
}

const raw = readFileSync(FIXTURE_PATH)
const fixture = JSON.parse(raw.toString('utf-8')) as {
  reader: ReaderCase[]
  effective_from: ReaderCase[]
  window: WindowCase[]
}

describe('the shared cycle-window fixture', () => {
  it('is the pinned one — both repos or neither', () => {
    const actual = createHash('sha256').update(raw).digest('hex')
    expect(
      actual,
      'cycle_window_fixture.json changed. Update the pin here AND in the Python twin ' +
        'AND in the engagements pipeline, in the same change.'
    ).toBe(PINNED_CONTENT_SHA256)
  })
})

describe('the reader', () => {
  for (const c of fixture.reader) {
    it(c.why.slice(0, 70), () => {
      if (c.asymmetric) {
        // Declared, not hidden: JS has one number type, so it cannot refuse what
        // Python refuses here. The seat is the authority and refuses, so no job
        // runs; the console drift notice covers the display side.
        if (c.asymmetric.ts === 'accepts') {
          expect(() => resolveAnchor(materialise(c.settings))).not.toThrow()
        } else {
          expect(() => resolveAnchor(materialise(c.settings))).toThrow(AnchorInvalid)
        }
        return
      }
      if (c.invalid) {
        expect(() => resolveAnchor(materialise(c.settings))).toThrow(AnchorInvalid)
        // The refusal names the key the firm has to fix.
        try {
          resolveAnchor(materialise(c.settings))
        } catch (e) {
          expect((e as Error).message).toContain(ANCHOR_SETTING)
        }
      } else {
        expect(resolveAnchor(materialise(c.settings))).toBe(c.anchor ?? null)
      }
    })
  }

  for (const c of fixture.effective_from) {
    it(`effective_from: ${c.why.slice(0, 58)}`, () => {
      if (c.invalid) {
        expect(() => resolveEffectiveFrom(materialise(c.settings))).toThrow(AnchorInvalid)
        try {
          resolveEffectiveFrom(materialise(c.settings))
        } catch (e) {
          expect((e as Error).message).toContain(EFFECTIVE_FROM_SETTING)
        }
      } else {
        expect(resolveEffectiveFrom(materialise(c.settings))).toBe(c.value ?? null)
      }
    })
  }

  it('absent is not invalid', () => {
    // Absent = "no cycle authored, meter by calendar month", a known safe state.
    // Invalid = a control the seat cannot honour. Collapsing them lets an
    // anchor of 32 silently revert a firm to calendar months.
    expect(resolveAnchor({})).toBeNull()
    expect(resolveAnchor(null)).toBeNull()
    expect(() => resolveAnchor({ [ANCHOR_SETTING]: 32 })).toThrow(AnchorInvalid)
  })
})

describe('the arithmetic', () => {
  for (const c of fixture.window) {
    it(c.why.slice(0, 70), () => {
      const w = cycleWindow(c.now, c.anchor, c.effective_from ?? null)
      expect({ start: w.start, end: w.end, label: w.label }).toEqual({
        start: c.start,
        end: c.end,
        label: c.label,
      })
    })
  }

  it('emits full timestamps, never bare dates', () => {
    const w = cycleWindow('2026-09-20T12:00:00.000Z', 15)
    expect(w.start).toMatch(/T00:00:00\.000Z$/)
    expect(w.end).toMatch(/T00:00:00\.000Z$/)
  })
})

describe('the tiling property', () => {
  // 32 anchors x 2,191 days x two assertions is ~140k expect calls: 1.3s alone,
  // over the 5s default under full-suite load (it timed out on a pre-push
  // verify, 2026-09-11). The budget is sized to the sweep, not to the machine.
  it(
    'windows tile with no gap or overlap, for every anchor across six years',
    { timeout: 60_000 },
    () => {
      const anchors: (number | null)[] = [...Array.from({ length: 31 }, (_, i) => i + 1), null]
      for (const anchor of anchors) {
        let day = Date.UTC(2024, 0, 1)
        const stop = Date.UTC(2029, 11, 31)
        while (day <= stop) {
          const now = `${new Date(day).toISOString().slice(0, 10)}T12:00:00.000Z`
          const w = cycleWindow(now, anchor)
          expect(w.start <= now && now < w.end, `anchor=${anchor} now=${now}`).toBe(true)
          expect(cycleWindow(w.end, anchor).start, `gap at anchor=${anchor} ${w.end}`).toBe(w.end)
          day += 86400000
        }
      }
    }
  )
})
