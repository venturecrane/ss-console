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
 *
 * The second block is the same ratchet for ideas built more than once (the job
 * lane list, the broker socket client, the money formatter, ...): each concept
 * in scripts/sprawl/concepts.ts may not live in more places than
 * scripts/sprawl/concept-baseline.json allows, and a count that fell must be
 * locked in with `npx tsx scripts/sprawl-audit.ts --write-concept-baseline`.
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { literalCensus, literalRatchet } from '../scripts/sprawl/analyze'
import {
  CONCEPTS,
  CONCEPT_BASELINE,
  conceptCensus,
  conceptRatchet,
} from '../scripts/sprawl/concepts'
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

describe('built-N-times concept ratchet', () => {
  const rows = conceptCensus(readSources(trackedFiles()))
  const baseline = parseLiteralBaseline(readFileSync(CONCEPT_BASELINE, 'utf8'), CONCEPT_BASELINE)

  it('every concept has a baseline row and every row a concept', () => {
    expect(Object.keys(baseline).sort()).toEqual(CONCEPTS.map((c) => c.id).sort())
  })

  it('reads a non-trivial tree, so an empty census cannot pass by accident', () => {
    // A detector gone blind shows as a fall below its baseline (the last case).
    expect(rows.reduce((n, r) => n + r.count, 0)).toBeGreaterThan(100)
  })

  it('no concept lives in more places than its baseline allows', () => {
    expect(conceptRatchet(rows, baseline).rose).toEqual([])
  })

  it('the baseline is lowered whenever a count falls', () => {
    expect(conceptRatchet(rows, baseline).fell).toEqual([])
  })
})
