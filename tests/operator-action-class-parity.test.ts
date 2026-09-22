/**
 * Cross-language action-class parity (ADR 0075).
 *
 * The TS authoring surface (`ACCEPTED_ACTION_CLASSES` / `SEND_ACTION_CLASSES`) and
 * the Python enforcement adapter (`operator/adapter/trust_ceiling.py`'s
 * `ActionClass`) must agree on the action-class vocabulary — the overlay
 * materializer carries the authored strings across the seam to the runtime
 * `enforce()` call. If a send class exists on one side and not the other, an
 * authored ceiling is silently dropped or a runtime send resolves to an
 * unhandled class. This pins the agreement by reading the Python enum directly.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  ACCEPTED_ACTION_CLASSES,
  EXPOSURE_ACTION_CLASSES,
  SEND_ACTION_CLASSES,
} from '../src/lib/operator/customer-yaml/types'

const ADAPTER_PATH = resolve('operator/adapter/trust_ceiling.py')

/** Extract the `ActionClass` enum's string `.value`s from the Python adapter. */
function pythonActionClassValues(): string[] {
  const src = readFileSync(ADAPTER_PATH, 'utf-8')
  const start = src.indexOf('class ActionClass')
  expect(start, 'ActionClass enum not found in adapter').toBeGreaterThan(-1)
  const after = src.slice(start)
  // The enum body ends at the next top-level `class ` (EnforcementDecision).
  const endRel = after.indexOf('\nclass ')
  const body = endRel > -1 ? after.slice(0, endRel) : after
  // Enum members are indented `NAME = "value"`; a top-level module constant has no
  // leading whitespace and is excluded by the `^\s+` anchor.
  const re = /^\s+[A-Z_]+\s*=\s*"([a-z_]+)"/gm
  const out: string[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(body)) !== null) out.push(m[1])
  return out
}

describe('action-class parity: TS ⇄ Python adapter (ADR 0075)', () => {
  const pyValues = pythonActionClassValues()

  it('extracts a non-trivial enum (sanity)', () => {
    expect(pyValues).toContain('external_send')
    expect(pyValues.length).toBeGreaterThanOrEqual(6)
  })

  it('every Python ActionClass value is an accepted TS action class', () => {
    const accepted = new Set<string>(ACCEPTED_ACTION_CLASSES as readonly string[])
    for (const v of pyValues) {
      expect(accepted.has(v), `Python enum value "${v}" missing from ACCEPTED_ACTION_CLASSES`).toBe(
        true
      )
    }
  })

  it('the send classes agree between TS SEND_ACTION_CLASSES and the Python enum', () => {
    // SEND_ACTION_CLASSES are the RECIPIENT-AXIS classes: the ones the recipient
    // classifier resolves a send to. external_send_as_staff (ADR 0089) is not
    // one: it is selected by a `from` on the call, never by the recipient, and
    // it has its own confirm-only rule. Excluded by name so a new recipient-axis
    // class still has to appear on both sides.
    const pySend = new Set(
      pyValues.filter((v) => v.startsWith('external_send') && v !== 'external_send_as_staff')
    )
    expect(pySend).toEqual(new Set<string>(SEND_ACTION_CLASSES as readonly string[]))
  })

  /**
   * ss#2314: `EXPOSURE_ACTION_CLASSES` is the key set the seat's override
   * store will honor, and it now gates what `routine-grid.yaml` may author.
   * The seat computes its own `_OVERRIDABLE_ACTIONS`
   * (`shared/exposure_override.py`) as the Python enum minus `READ` and
   * `REFUSED`. This pins the same derivation against the in-repo adapter, so
   * a class added or renamed on the Python side cannot leave the authoring
   * gate silently checking a stale vocabulary.
   *
   * LIMIT, stated so this test is not read as more than it is: the adapter
   * this reads is ss-console's copy. The overlay's `shared/action_classes.py`
   * lives in another repo and is not reachable from CI here — drift between
   * those two Python files is out of this instrument's range.
   */
  it('EXPOSURE_ACTION_CLASSES equals the Python enum minus read/refused', () => {
    const expected = new Set(pyValues.filter((v) => v !== 'read' && v !== 'refused'))
    expect(new Set<string>(EXPOSURE_ACTION_CLASSES as readonly string[])).toEqual(expected)
  })

  it('EXPOSURE_ACTION_CLASSES is derived from ACCEPTED_ACTION_CLASSES, not transcribed', () => {
    expect([...EXPOSURE_ACTION_CLASSES]).toEqual(
      ACCEPTED_ACTION_CLASSES.filter((c) => c !== 'read')
    )
  })
})
