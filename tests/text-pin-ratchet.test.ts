/**
 * The source-text pin ratchet: a fitness function in CI (Ford, Parsons and Kua,
 * "Building Evolutionary Architectures"), the same strict shape as
 * tests/sprawl-ratchet.test.ts.
 *
 * A source-text pin is a test that reads a repo source or doc file as text and
 * asserts on its wording instead of running the code it covers (a change-
 * detector test). It fails when a sentence is reworded and stays green when the
 * behaviour breaks. scripts/sprawl/text-pins.ts counts them per assertion.
 *
 * Every pin needs a REASON and a CAP:
 *
 *   - the reason: its file is on scripts/sprawl/text-pin-allowlist.ts, either a
 *     named guard (policy scan, contract, security or order, client content,
 *     model-read rule) or a known detector false positive, with one line why;
 *   - the cap: scripts/sprawl/text-pin-baseline.json holds every such file's
 *     count. A file whose count rises fails, guard files included, so a new pin
 *     inside an allowlisted file is caught like one anywhere else. A count that
 *     falls fails too, until the baseline is lowered with
 *     `npx tsx scripts/sprawl-audit.ts --write-text-pin-baseline`.
 *
 * Fix a failure by running the code and asserting on its effect, or deleting
 * the assertion. A deliberate new guard is a reviewed allowlist entry plus a
 * raised cap in the same PR, and the reviewer reads both. List the sites with
 * `npx tsx scripts/sprawl-audit.ts --text-pins`.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { textPinCensus, textPinRatchet, unexplainedPinFiles } from '../scripts/sprawl/analyze'
import {
  TEXT_PIN_BASELINE,
  parseLiteralBaseline,
  readTestFiles,
  trackedFiles,
} from '../scripts/sprawl/sources'
import {
  TEXT_PIN_FALSE_POSITIVES,
  TEXT_PIN_POLICY_SCANS,
} from '../scripts/sprawl/text-pin-allowlist'

describe('source-text pin ratchet', () => {
  const tests = readTestFiles(trackedFiles())
  const census = textPinCensus(tests)
  const reasons = new Set([
    ...Object.keys(TEXT_PIN_POLICY_SCANS),
    ...Object.keys(TEXT_PIN_FALSE_POSITIVES),
  ])
  const baseline = parseLiteralBaseline(readFileSync(TEXT_PIN_BASELINE, 'utf8'), TEXT_PIN_BASELINE)

  it('reads the whole suite, so an empty census cannot pass by accident', () => {
    expect(tests.length).toBeGreaterThan(1000)
  })

  it('still sees every listed file, so a blind detector or a stale entry cannot pass', () => {
    // A listed file the detector reads no source text in is gone, no longer a
    // scan, or no longer a false positive: remove its entry.
    expect([...reasons].filter((p) => !census[p]).sort()).toEqual([])
  })

  it('every file holding pins says why (allowlist or known false positive)', () => {
    expect(unexplainedPinFiles(census, reasons)).toEqual([])
  })

  it('no test file gains a source-text pin past its cap, allowlisted guards included', () => {
    expect(textPinRatchet(census, baseline).rose).toEqual([])
  })

  it('the baseline is lowered whenever a count falls', () => {
    expect(textPinRatchet(census, baseline).fell).toEqual([])
  })
})
