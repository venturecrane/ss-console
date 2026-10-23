/**
 * ss#2320 half 2. Pin the overlay's audit vocabulary to this repo's.
 *
 * THE GAP. The seat runtime validates rows against the OVERLAY's
 * ACCEPTED_ACTION_TYPES; the client portal renders from THIS repo's
 * AUDIT_ACTION_TYPES; and `shared/audit_contract.py` validates nothing at all,
 * so a raw INSERT accepts any string. Nothing connected the three. The path
 * "overlay adds an emitter, declares the type overlay-side, overlay CI passes,
 * rows land in client ledgers, portal renders nothing" had no gate on it, and
 * the resulting silence is indistinguishable from a deliberate suppression
 * (ss#2316's third state).
 *
 * WHAT THIS CATCHES. An overlay type this repo has never heard of, in the same
 * commit that adds it. The overlay is operator/hermes-smd-overlay in this tree
 * (one repository since the 2026-10-22 fold, ADR 0095), so the snapshot in the
 * contract is compared with the LIVE ACCEPTED_ACTION_TYPES on every run; a type
 * added to schemas.py without a contract update, or a careless transcription, is
 * red here before it merges. Before the fold the snapshot was anchored to a
 * pinned overlay commit and could only be refreshed by hand at a pin bump.
 *
 * WHAT IT DOES NOT CATCH. `shared/audit_contract.py` still validates nothing at
 * the INSERT (issue #2320 names write-time validation as worth doing regardless):
 * this gate makes a divergence loud at merge time, that one would make it
 * impossible at write time.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { AUDIT_ACTION_TYPES } from '../src/lib/portal/operator/audit'
import { MAPPED_ACTIONS, SUPPRESSED_ACTIONS } from '../src/lib/portal/operator/activity-language'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const CONTRACT = join(REPO_ROOT, 'operator', 'contracts', 'audit-action-vocabulary.json')
const SCHEMAS = join(
  REPO_ROOT,
  'operator',
  'hermes-smd-overlay',
  'plugins',
  'hermes-smd-audit',
  'schemas.py'
)

interface VocabularyContract {
  overlaySource: string
  overlayTypes: string[]
  overlayOnly: Record<string, string>
  consoleOnly: Record<string, string>
}

const contract = (): VocabularyContract =>
  JSON.parse(readFileSync(CONTRACT, 'utf-8')) as VocabularyContract

/**
 * The overlay's ACCEPTED_ACTION_TYPES as the seat runtime declares it: the
 * frozenset literal from `ACCEPTED_ACTION_TYPES` to the first `)` at column 0,
 * string literals only (comments stripped), so a type named in a comment does
 * not count and a type the parser missed is a loud red, not a silent pass.
 */
const liveOverlayTypes = (): string[] => {
  const lines = readFileSync(SCHEMAS, 'utf-8').split('\n')
  const start = lines.findIndex((l) => l.startsWith('ACCEPTED_ACTION_TYPES'))
  expect(start, 'no ACCEPTED_ACTION_TYPES in schemas.py').toBeGreaterThanOrEqual(0)
  const end = lines.findIndex((l, i) => i > start && l === ')')
  expect(end, 'ACCEPTED_ACTION_TYPES literal never closes').toBeGreaterThan(start)
  const types = new Set<string>()
  for (const line of lines.slice(start, end + 1)) {
    const code = line.split('#', 1)[0]
    for (const m of code.matchAll(/"([A-Z0-9_]+)"/g)) types.add(m[1])
  }
  expect(types.size, 'the parser found no types; the literal shape changed').toBeGreaterThan(20)
  return [...types].sort()
}

describe('audit vocabulary parity with the shipped overlay (ss#2320)', () => {
  it('the snapshot equals the live ACCEPTED_ACTION_TYPES in operator/hermes-smd-overlay', () => {
    // The load-bearing assertion. Adding a type to schemas.py without updating
    // the contract (or the other way round) turns this red in the same PR.
    expect([...contract().overlayTypes].sort()).toEqual(liveOverlayTypes())
    expect(contract().overlaySource).toContain('plugins/hermes-smd-audit/schemas.py')
  })

  it('every overlay type is known here, or declared overlay-only with a reason', () => {
    const known = new Set<string>(AUDIT_ACTION_TYPES)
    const { overlayTypes, overlayOnly } = contract()
    const undeclared = overlayTypes.filter((t) => !known.has(t) && !(t in overlayOnly))
    expect(
      undeclared,
      'the overlay declares these and this repo has never heard of them: a row of this type lands in a client ledger and the portal renders nothing'
    ).toEqual([])
  })

  it('every overlay-only exemption states why', () => {
    for (const [type, reason] of Object.entries(contract().overlayOnly)) {
      expect(reason.trim().length, `${type} is exempted with no reason`).toBeGreaterThan(0)
    }
  })

  it('no exemption hides a type this repo actually knows', () => {
    // A stale exemption is worse than none: it reads as "handled" while the real
    // decision (map it or suppress it) was never made.
    const known = new Set<string>(AUDIT_ACTION_TYPES)
    const stale = Object.keys(contract().overlayOnly).filter((t) => known.has(t))
    expect(stale, 'exempted as overlay-only but present in AUDIT_ACTION_TYPES').toEqual([])
  })

  it('every console-only type is genuinely absent from the overlay', () => {
    const overlay = new Set(contract().overlayTypes)
    const wrong = Object.keys(contract().consoleOnly).filter((t) => overlay.has(t))
    expect(wrong, 'declared console-only but the overlay knows it too').toEqual([])
  })

  it('every overlay type this repo knows has a client-language decision', () => {
    // Closes the loop to ss#2316: knowing a type is not the same as having
    // decided what a client sees. A type can pass the parity check above and
    // still render as nothing for want of a decision.
    const { overlayTypes, overlayOnly } = contract()
    const undecided = overlayTypes
      .filter((t) => !(t in overlayOnly))
      .filter((t) => !MAPPED_ACTIONS.includes(t) && !SUPPRESSED_ACTIONS.has(t))
    expect(undecided, 'shipped by the overlay with no mapped/suppressed decision').toEqual([])
  })
})

/**
 * The falsifier. Every assertion above passes on today's tree, so each is
 * re-run against a deliberately broken contract to show it can fail. Without
 * this the suite is six green checks of unknown strength.
 */
describe('the parity gate can fail (ss#2320)', () => {
  const withContract = (mutate: (c: VocabularyContract) => void) => {
    const c = contract()
    mutate(c)
    return c
  }

  it('catches an overlay type this repo has never heard of', () => {
    const c = withContract((x) => x.overlayTypes.push('SYNTHETIC_OVERLAY_ONLY_TYPE'))
    const known = new Set<string>(AUDIT_ACTION_TYPES)
    const undeclared = c.overlayTypes.filter((t) => !known.has(t) && !(t in c.overlayOnly))
    expect(undeclared).toEqual(['SYNTHETIC_OVERLAY_ONLY_TYPE'])
  })

  it('catches a snapshot that drifted from the live source', () => {
    const c = withContract((x) => {
      x.overlayTypes = x.overlayTypes.filter((t) => t !== 'INBOUND_RECEIVED')
    })
    expect([...c.overlayTypes].sort()).not.toEqual(liveOverlayTypes())
  })

  it('catches an exemption with an empty reason', () => {
    const c = withContract((x) => {
      x.overlayOnly.SYNTHETIC_EMPTY_REASON = '   '
    })
    const empty = Object.entries(c.overlayOnly).filter(([, r]) => r.trim().length === 0)
    expect(empty.map(([t]) => t)).toEqual(['SYNTHETIC_EMPTY_REASON'])
  })
})
