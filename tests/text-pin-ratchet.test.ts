/**
 * The source-text pin ratchet: a fitness function in CI (Ford, Parsons and Kua,
 * "Building Evolutionary Architectures"), the same strict shape as
 * tests/sprawl-ratchet.test.ts.
 *
 * A source-text pin is a test that reads a repo source or doc file as text and
 * asserts on its wording instead of running the code it covers (a change-
 * detector test). It fails when a sentence is reworded and stays green when the
 * behaviour breaks. scripts/sprawl/text-pins.ts counts them per assertion; this
 * test fails when:
 *
 *   - a test file now holds MORE pins than scripts/sprawl/text-pin-baseline.json
 *     allows, or a file not in the baseline holds any. Fix: run the code and
 *     assert on its effect, or delete the assertion. A deliberate policy scan
 *     (a rule over a tree, a parity check between two artifacts) goes on
 *     scripts/sprawl/text-pin-allowlist.ts with one line saying what it enforces,
 *     and that entry is reviewed like code.
 *   - a file holds FEWER pins than its baseline. That is progress; lock it in
 *     with `npx tsx scripts/sprawl-audit.ts --write-text-pin-baseline`.
 *
 * The baseline may only go down. List the sites with
 * `npx tsx scripts/sprawl-audit.ts --text-pins`.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { textPinCensus, textPinRatchet } from '../scripts/sprawl/analyze'
import {
  TEXT_PIN_BASELINE,
  parseLiteralBaseline,
  readTestFiles,
  trackedFiles,
} from '../scripts/sprawl/sources'
import { TEXT_PIN_POLICY_SCANS } from '../scripts/sprawl/text-pin-allowlist'
import { sourceTextPins } from '../scripts/sprawl/text-pins'

describe('source-text pin ratchet', () => {
  const tests = readTestFiles(trackedFiles())
  const allow = new Set(Object.keys(TEXT_PIN_POLICY_SCANS))
  const census = textPinCensus(tests, allow)
  const baseline = parseLiteralBaseline(readFileSync(TEXT_PIN_BASELINE, 'utf8'), TEXT_PIN_BASELINE)

  it('reads the whole suite, so an empty census cannot pass by accident', () => {
    expect(tests.length).toBeGreaterThan(1000)
  })

  it('still sees the allowlisted policy scans, so a blind detector cannot pass', () => {
    const byPath = new Map(tests.map((f) => [f.path, f.text]))
    const blind = [...allow].filter((p) => sourceTextPins(p, byPath.get(p) ?? '') === 0)
    // An allowlisted file the detector reads no source text in is either gone or
    // no longer a scan: remove its entry.
    expect(blind).toEqual([])
  })

  it('no test file gains a source-text pin past its baseline', () => {
    expect(textPinRatchet(census, baseline).rose).toEqual([])
  })

  it('the baseline is lowered whenever a count falls', () => {
    expect(textPinRatchet(census, baseline).fell).toEqual([])
  })
})
