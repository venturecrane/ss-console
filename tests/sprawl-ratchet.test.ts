/**
 * The restated-literal ratchet: a fitness function in CI (Ford, Parsons and
 * Kua, "Building Evolutionary Architectures").
 *
 * A boundary-crossing value (a seat path such as /run/smd-jobs, or an
 * environment-variable name) written out by hand in many files is what made
 * the ADR 0103 rename touch 795 files. This test reads the tree the same way
 * the weekly audit does (scripts/sprawl/sources.ts) and fails when:
 *
 *   - a literal now appears in MORE non-test files than
 *     scripts/sprawl/literal-baseline.json allows, or a new literal reaches
 *     five files. Fix: reference the one definition instead of restating it.
 *   - a literal appears in FEWER files than its baseline. That is progress;
 *     lock it in with `npx tsx scripts/sprawl-audit.ts --write-literal-baseline`.
 *
 * The baseline may only go down. Raising a number by hand to pass this test is
 * the one edit it exists to stop; a reviewer should reject it.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { literalCensus, literalRatchet } from '../scripts/sprawl/analyze'
import {
  LITERAL_BASELINE,
  parseLiteralBaseline,
  readSources,
  trackedFiles,
} from '../scripts/sprawl/sources'

describe('restated-literal ratchet', () => {
  const rows = literalCensus(readSources(trackedFiles()))
  const baseline = parseLiteralBaseline(readFileSync(LITERAL_BASELINE, 'utf8'))

  it('reads a non-trivial tree, so an empty census cannot pass by accident', () => {
    expect(rows.length).toBeGreaterThan(20)
  })

  it('no literal is restated in more files than its baseline allows', () => {
    expect(literalRatchet(rows, baseline).rose).toEqual([])
  })

  it('the baseline is lowered whenever a count falls', () => {
    expect(literalRatchet(rows, baseline).fell).toEqual([])
  })
})
