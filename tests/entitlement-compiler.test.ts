/**
 * Routine-tier → enforcement compiler (#2003).
 *
 * Exercised against the LIVE ashton-price grid + customer.yaml (not fixtures):
 * the compiler's whole job is to be correct about this seat's committed
 * ceilings, so the client artifacts are the test inputs. Synthetic rows cover
 * the floor path (no vertical floor exists today, ADR 0073).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'
import {
  validateRoutineGrid,
  type RoutineGrid,
  type RoutineGridRow,
} from '../src/lib/operator/routine-grid'
import {
  compileTierChange,
  liveTierOf,
  resolveLiveTier,
  selectableTiers,
  sendActionClassOf,
  type LiveExposure,
} from '../src/lib/operator/entitlement-compiler'

const AP = resolve('operator/customers/ashton-price')

function grid(): RoutineGrid {
  const result = validateRoutineGrid(
    parseYaml(readFileSync(resolve(AP, 'routine-grid.yaml'), 'utf-8'))
  )
  if (!result.ok) throw new Error(`grid invalid: ${JSON.stringify(result.errors)}`)
  return result.value
}

function liveExposure(): LiveExposure {
  const raw = parseYaml(readFileSync(resolve(AP, 'customer.yaml'), 'utf-8')) as Record<
    string,
    unknown
  >
  const result = validate(raw)
  if (!result.ok) throw new Error(`customer.yaml invalid: ${JSON.stringify(result.errors)}`)
  const persona = result.value.personas.find((p) => p.slug === 'operator')
  if (!persona) throw new Error('operator persona missing')
  return {
    personaSlug: persona.slug,
    exposure: persona.entitlements.exposure,
  }
}

const rowNamed = (g: RoutineGrid, name: string): RoutineGridRow => {
  const row = g.rows.find((r) => r.routine === name)
  if (!row) throw new Error(`row "${name}" not in grid`)
  return row
}

describe('send-class discovery + live tier', () => {
  it('a flag-only row authors no send class (structural: no draft/send tool)', () => {
    const row = rowNamed(grid(), 'Served discovery caught')
    expect(sendActionClassOf(row)).toBeNull()
    expect(liveTierOf(row, liveExposure())).toBe('flag-only')
  })

  it('a dial-less row reads its writing ceiling, never collapsed to flag-only and never over-claimed', () => {
    // 2026-09-17, two corrections in one place. Every row without a send class
    // used to resolve to flag-only, so prepare-and-route work-product routines
    // and the internal-record chronology read as "Surfaces it" on a client
    // page. The first fix returned the grid's authored tier unconditionally,
    // which is the worse error in the other direction: it would claim
    // "Handles it" while the seat held `internal_write: refused`. The level is
    // the lower of the two.
    const g = grid()
    const dialLess = g.rows.filter((r) => sendActionClassOf(r) === null)
    expect(
      dialLess.filter((r) => r.start_tier !== 'flag-only').length,
      'grid must carry dial-less rows above flag-only'
    ).toBeGreaterThan(0)

    const writing = (ceiling: string): LiveExposure => ({
      personaSlug: 'operator',
      exposure: { internal_write: ceiling },
    })

    for (const row of dialLess) {
      // Authorized to write on its own: the authored level stands.
      expect(liveTierOf(row, writing('autonomous')), row.routine).toBe(row.start_tier)
      // Held for a person: never above prepare-and-route, whatever the grid says.
      const held = resolveLiveTier(row, writing('draft_for_review'))
      expect(held.notAuthorized, row.routine).toBe(false)
      expect(held.tier === 'auto-handle', `${row.routine} must not claim auto-handle`).toBe(false)
      // Refused, and unauthored, are not levels at all.
      for (const off of [writing('refused'), { personaSlug: 'operator', exposure: {} }]) {
        expect(resolveLiveTier(row, off).notAuthorized, row.routine).toBe(true)
      }
      expect(selectableTiers(row), row.routine).toEqual([row.start_tier])
    }

    expect(liveTierOf(rowNamed(g, 'Medical chronology'), writing('autonomous'))).toBe('auto-handle')
    expect(liveTierOf(rowNamed(g, 'Medical chronology'), writing('draft_for_review'))).toBe(
      'prepare-and-route'
    )
    const lowered = compileTierChange(g, liveExposure(), {
      routine: 'Medical chronology',
      targetTier: 'flag-only',
      vertical: 'law-firm',
    })
    expect(lowered.ok).toBe(false)
    if (!lowered.ok) expect(lowered.rejections.map((r) => r.code)).toContain('no_graduation_path')
  })

  it('a graduating row authors its own send class', () => {
    const row = rowNamed(grid(), 'Client verification')
    expect(sendActionClassOf(row)).toBe('external_send_client')
  })

  it('live tier derives from live config, never from the grid start_tier', () => {
    const row = rowNamed(grid(), 'Client verification')
    // The seat now authors external_send_client: draft_for_review, so live
    // tier and the letter's start_tier agree. They are still computed
    // independently — this build FOUND them disagreeing (the key was
    // unauthored, so the routine sat at fail-closed flag-only while the
    // letter said prepare-and-route). A seat whose key is removed drops to
    // flag-only again, which is what the fail-closed assertion below pins.
    expect(liveTierOf(row, liveExposure())).toBe('prepare-and-route')
    expect(row.start_tier).toBe('prepare-and-route')

    const stripped: LiveExposure = {
      personaSlug: 'operator',
      exposure: { internal_write: 'draft_for_review' },
    }
    expect(liveTierOf(row, stripped)).toBe('flag-only')

    // An explicit `refused` value is the runtime dial's flag-only (the
    // override store has no delete verb; deauthorize is expressed as refused).
    // Found live by the ss#2003 probe: the page rendered prepare-and-route
    // while the Machine correctly held refused.
    const refusedOverride: LiveExposure = {
      personaSlug: 'operator',
      exposure: { ...liveExposure().exposure, [sendActionClassOf(row)!]: 'refused' },
    }
    expect(liveTierOf(row, refusedOverride)).toBe('flag-only')
  })
})

describe('letter ceiling is non-raisable from this path', () => {
  it('rejects a target above the committed ceiling', () => {
    const g = grid()
    const row = g.rows.find((r) => r.ceiling_tier === 'prepare-and-route' && sendActionClassOf(r))
    expect(row, 'grid must carry a send-bearing row capped at prepare-and-route').toBeTruthy()
    const result = compileTierChange(g, liveExposure(), {
      routine: row!.routine,
      targetTier: 'auto-handle',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejections.map((r) => r.code)).toContain('above_letter_ceiling')
    expect(result.rejections[0].message).toContain('commitment change')
  })

  it('rejects any raise on a routine with no send class', () => {
    const result = compileTierChange(grid(), liveExposure(), {
      routine: 'Served discovery caught',
      targetTier: 'prepare-and-route',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejections.map((r) => r.code)).toContain('no_graduation_path')
  })

  it('selectableTiers never offers a tier the compiler would reject', () => {
    const g = grid()
    const live = liveExposure()
    for (const row of g.rows) {
      for (const tier of selectableTiers(row)) {
        const result = compileTierChange(g, live, {
          routine: row.routine,
          targetTier: tier,
          vertical: 'law-firm',
        })
        expect(result.ok, `${row.routine} → ${tier} was offered but rejected`).toBe(true)
      }
    }
  })
})

describe('compiled deltas', () => {
  it('graduating a routine to its ceiling emits one exposure change', () => {
    const g = grid()
    const row = g.rows.find((r) => r.ceiling_tier === 'auto-handle' && sendActionClassOf(r))!
    const result = compileTierChange(g, liveExposure(), {
      routine: row.routine,
      targetTier: 'auto-handle',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.delta.noop).toBe(false)
    expect(result.delta.exposureChanges).toHaveLength(1)
    const change = result.delta.exposureChanges[0]
    expect(change.actionClass).toBe(sendActionClassOf(row))
    expect(change.from).toBe('draft_for_review')
    expect(change.to).toBe('autonomous')
    expect(change.direction).toBe('raise')
    expect(change.personaSlug).toBe('operator')
  })

  it('the seat now sits at the letter start tier for its graduating routines', () => {
    const g = grid()
    const live = liveExposure()
    for (const row of g.rows) {
      if (sendActionClassOf(row) === null) continue
      expect(liveTierOf(row, live), `${row.routine} live tier`).toBe(row.start_tier)
    }
  })

  it('a request already satisfied compiles to a no-op with no changes', () => {
    const result = compileTierChange(grid(), liveExposure(), {
      routine: 'Served discovery caught',
      targetTier: 'flag-only',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.delta.noop).toBe(true)
    expect(result.delta.exposureChanges).toEqual([])
  })

  it('lowering back to flag-only REMOVES the key (fail-closed, ADR 0035)', () => {
    const g = grid()
    const row = g.rows.find((r) => r.ceiling_tier === 'auto-handle' && sendActionClassOf(r))!
    const sendClass = sendActionClassOf(row)!
    // Simulate a seat that has already graduated this routine.
    const graduated: LiveExposure = {
      personaSlug: 'operator',
      exposure: { internal_write: 'draft_for_review', [sendClass]: 'autonomous' },
    }
    const result = compileTierChange(g, graduated, {
      routine: row.routine,
      targetTier: 'flag-only',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    const change = result.delta.exposureChanges[0]
    expect(result.delta.fromTier).toBe('auto-handle')
    expect(change.from).toBe('autonomous')
    expect(change.to).toBeNull()
    expect(change.direction).toBe('deauthorize')
  })
})

describe('input guards', () => {
  it('rejects an unknown routine', () => {
    const result = compileTierChange(grid(), liveExposure(), {
      routine: 'Nonexistent routine',
      targetTier: 'flag-only',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejections[0].code).toBe('unknown_routine')
  })

  it('rejects a tier outside the closed vocabulary', () => {
    const result = compileTierChange(grid(), liveExposure(), {
      routine: 'Client verification',
      targetTier: 'full-autonomy',
      vertical: 'law-firm',
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejections[0].code).toBe('invalid_tier')
  })

  it('rejects when the live persona is not the grid persona', () => {
    const result = compileTierChange(
      grid(),
      { personaSlug: 'someone-else', exposure: {} },
      { routine: 'Client verification', targetTier: 'flag-only', vertical: 'law-firm' }
    )
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.rejections.map((r) => r.code)).toContain('persona_missing')
  })
})
