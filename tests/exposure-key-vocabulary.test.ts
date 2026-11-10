/**
 * Exposure-key vocabulary: the routine grid ⇄ seat override-store join (ss#2314).
 *
 * `routine-grid.yaml`'s `enforcement.exposure_keys` are the strings that INDEX
 * the seat's `exposure_override` table (keyed `(customer, persona,
 * action_class)`). They were validated only as "must be a string", so a typo
 * or a drifted name produced two silent failures:
 *
 *   read  — `liveTierOf` missed the key and returned `flag-only`, which is
 *           ALSO the legitimate fail-closed answer for an unauthored key. The
 *           portal rendered a safety posture the Machine was not enforcing.
 *   write — the same string was posted as the override key, and the console
 *           never compared the Machine's `applied` echo to what it sent, so a
 *           change the seat ignored was recorded `status='applied'`.
 *
 * Every test here was run against unfixed code first and observed to fail;
 * the failure output is in the PR body. The fail-CLOSED posture is pinned
 * alongside each loudness assertion — a miss must be noisy AND refused, never
 * noisy and permitted (anti-pattern `imposed-defaults-on-client-posture`).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { readFileSync } from 'fs'
import path, { resolve } from 'path'
import { parse as parseYaml } from 'yaml'
import type { D1Database } from '@cloudflare/workers-types'
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
  type LiveExposure,
} from '../src/lib/operator/entitlement-compiler'
import {
  applyTierChange,
  listEntitlementChanges,
} from '../src/lib/portal/operator/entitlement-change'

const migrationsDir = path.resolve(__dirname, '../migrations')
const AP = resolve('operator/customers/ashton-price')

/** A key no action-class vocabulary contains — one transposition from a real one. */
const TYPO_KEY = 'external_send_cleint'

function gridYaml(): Record<string, unknown> {
  return parseYaml(readFileSync(resolve(AP, 'routine-grid.yaml'), 'utf-8')) as Record<
    string,
    unknown
  >
}

function grid(): RoutineGrid {
  const r = validateRoutineGrid(gridYaml())
  if (!r.ok) throw new Error(`grid invalid: ${JSON.stringify(r.errors)}`)
  return r.value
}

function live(): LiveExposure {
  const r = validate(
    parseYaml(readFileSync(resolve(AP, 'customer.yaml'), 'utf-8')) as Record<string, unknown>
  )
  if (!r.ok) throw new Error('customer.yaml invalid')
  const p = r.value.personas.find((x) => x.slug === 'operator')
  if (!p) throw new Error('operator persona missing')
  return { personaSlug: p.slug, exposure: p.entitlements.exposure }
}

/** The live grid row for `Client verification`, with its send key transposed. */
function typoRow(): RoutineGridRow {
  const row = grid().rows.find((r) => r.routine === 'Client verification')
  if (!row) throw new Error('Client verification row missing from the live grid')
  return {
    ...row,
    enforcement: { ...row.enforcement, exposure_keys: { [TYPO_KEY]: 'draft_for_review' } },
  }
}

/** The same row, key intact, but with that key legitimately unauthored on the seat. */
function unauthoredRow(): RoutineGridRow {
  const row = grid().rows.find((r) => r.routine === 'Client verification')
  if (!row) throw new Error('Client verification row missing from the live grid')
  return row
}

// -------------------------------------------------------------------------
// (a) read side — a key that matches nothing is distinguishable from a key
//     that is legitimately unauthored. Both stay flag-only; only one is a bug.
// -------------------------------------------------------------------------

describe('(a) a grid key absent from the honored vocabulary is loud, not silently flag-only', () => {
  it('reports the unknown key instead of degrading to an indistinguishable flag-only', () => {
    const stripped: LiveExposure = {
      personaSlug: 'operator',
      exposure: { internal_write: 'autonomous' },
    }

    const bad = resolveLiveTier(typoRow(), stripped)
    const legit = resolveLiveTier(unauthoredRow(), stripped)

    // Both are fail-closed — the posture never relaxes because the key is wrong.
    expect(bad.tier).toBe('flag-only')
    expect(legit.tier).toBe('flag-only')

    // But they are no longer the same state: one is a config defect.
    expect(bad.unknownActionClass).toBe(TYPO_KEY)
    expect(legit.unknownActionClass).toBeNull()
    expect(bad).not.toEqual(legit)
  })

  it('liveTierOf keeps its fail-closed contract for callers that only need the tier', () => {
    const stripped: LiveExposure = {
      personaSlug: 'operator',
      exposure: { internal_write: 'autonomous' },
    }
    expect(liveTierOf(typoRow(), stripped)).toBe('flag-only')
  })
})

// -------------------------------------------------------------------------
// (b) write side — an override the seat cannot honor is refused up front, and
//     an unacknowledged change is never recorded as applied.
// -------------------------------------------------------------------------

describe('(b) an override under an unknown key is refused, never silently ignored', () => {
  it('compileTierChange rejects a row whose send class is outside the vocabulary', () => {
    const g = grid()
    const patched: RoutineGrid = {
      ...g,
      rows: g.rows.map((r) => (r.routine === 'Client verification' ? typoRow() : r)),
    }
    const result = compileTierChange(patched, live(), {
      routine: 'Client verification',
      targetTier: 'prepare-and-route',
      vertical: 'law-firm',
    })

    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('unreachable')
    expect(result.rejections.map((r) => r.code)).toContain('unknown_exposure_key')
    expect(result.rejections.map((r) => r.message).join(' ')).toContain(TYPO_KEY)
  })

  it('a gate 200 whose applied echo does not match the request records nothing', async () => {
    const db: D1Database = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    const g = grid()
    const row = g.rows.find((r) => r.ceiling_tier === 'auto-handle' && r.routine !== '')
    if (!row) throw new Error('no graduating row in the live grid')

    // The Machine answers 200 but acknowledges an action class that is not the
    // one we sent — exactly what a key the seat dropped on the floor looks like.
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ applied: [], persona: 'operator', updated_at: '2026-08-12T00:00:00Z' }),
          { status: 200 }
        )
    )

    const outcome = await applyTierChange(
      db,
      {
        OPERATOR_MCP_WEBHOOK_SECRET: 'test-master-secret',
        OPERATOR_RUNTIME_READ_URL: 'https://{app}.fly.test',
      },
      { grid: g, live: live() },
      {
        entityId: 'e1',
        customerSlug: 'ashton-price',
        routine: row.routine,
        targetTier: 'auto-handle',
        reason: 'clean cycles',
        vertical: 'law-firm',
        actor: { userId: 'u1', email: 'admin@firm.example', role: 'principal' },
        source: 'portal',
      }
    )

    expect(outcome.kind).toBe('failed')
    expect(await listEntitlementChanges(db, 'ashton-price')).toHaveLength(0)
  })
})

// -------------------------------------------------------------------------
// (c) authoring time — the typo never ships.
// -------------------------------------------------------------------------

describe('(c) a typo in exposure_keys fails config validation', () => {
  it('rejects an action class outside the honored vocabulary', () => {
    const raw = gridYaml()
    const rows = raw.rows as Record<string, unknown>[]
    const target = rows.find(
      (r) => (r.enforcement as Record<string, unknown>)?.exposure_keys !== undefined
    )
    if (!target) throw new Error('no row with exposure_keys in the live grid')
    ;(target.enforcement as Record<string, unknown>).exposure_keys = {
      [TYPO_KEY]: 'draft_for_review',
    }

    const result = validateRoutineGrid(raw)
    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('unreachable')
    expect(result.errors.map((e) => e.code)).toContain('InvalidActionClass')
    expect(result.errors.map((e) => e.path).join(' ')).toContain(TYPO_KEY)
  })

  it('rejects a ceiling value outside the ceiling vocabulary', () => {
    const raw = gridYaml()
    const rows = raw.rows as Record<string, unknown>[]
    const target = rows.find(
      (r) => (r.enforcement as Record<string, unknown>)?.exposure_keys !== undefined
    )
    if (!target) throw new Error('no row with exposure_keys in the live grid')
    ;(target.enforcement as Record<string, unknown>).exposure_keys = {
      external_send_client: 'mostly_autonomous',
    }

    const result = validateRoutineGrid(raw)
    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('unreachable')
    expect(result.errors.map((e) => e.code)).toContain('InvalidActionCeiling')
  })

  it('the live shipped grids still validate (the guard constrains, it does not break them)', () => {
    expect(validateRoutineGrid(gridYaml()).ok).toBe(true)
    const pilot = parseYaml(
      readFileSync(resolve('tests/fixtures/ap-shaped-seat/routine-grid.yaml'), 'utf-8')
    )
    expect(validateRoutineGrid(pilot).ok).toBe(true)
  })
})

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})
