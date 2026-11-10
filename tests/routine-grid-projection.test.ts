/**
 * routine-grid parser + projection round-trip (ADR 0075, #1308 auto-sync
 * extension).
 *
 * Two concerns:
 *   1. The canonical parser (src/lib/operator/routine-grid.ts) accepts the real
 *      A&P-shaped fixture grid and rejects malformed input with path-named errors.
 *   2. The write → read round-trip
 *      projectRow(projectCustomerYamlToConfigRow(yaml, ctx, grid)) never throws
 *      with the grid present, absent, or a MALFORMED stored value — the read
 *      side must fail SOFT to null (gridless console), never 500 the portal.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import type { CustomerYaml } from '../src/lib/operator/customer-yaml/types'
import { validateRoutineGrid, type RoutineGrid } from '../src/lib/operator/routine-grid'
import { projectCustomerYamlToConfigRow } from '../src/lib/portal/customer-config-projection'
import { projectRow, resolveRoutineGrid } from '../src/lib/portal/customer-config'

const CTX = {
  entityId: 'entity-123',
  orgId: 'org-123',
  gitSha: 'abc123def456',
  syncedAt: '2026-07-14T18:00:00.000Z',
}

// The A&P-shaped fixture (the pilot-smokeball grid as of 2026-11-10, frozen when that
// seat became SMD's demo seat): a realistic 19-row input no live seat has to keep.
const GRID_PATH = resolve('tests/fixtures/ap-shaped-seat/routine-grid.yaml')
const SEAT_YAML_PATH = resolve('tests/fixtures/ap-shaped-seat/customer.yaml')

/** Parse the real grid YAML into an `unknown` for the parser under test. */
function rawGrid(): unknown {
  return parseYaml(readFileSync(GRID_PATH, 'utf-8'))
}

/** The A&P-shaped fixture grid, validated. */
function liveGrid(): RoutineGrid {
  const result = validateRoutineGrid(rawGrid())
  if (!result.ok) {
    throw new Error(
      'pilot-smokeball routine-grid.yaml failed validation: ' + JSON.stringify(result.errors)
    )
  }
  return result.value
}

/** The A&P-shaped fixture customer.yaml as a realistic projection base. */
function seatYaml(): CustomerYaml {
  const result = validate(parseYaml(readFileSync(SEAT_YAML_PATH, 'utf-8')))
  if (!result.ok) {
    throw new Error(
      'pilot-smokeball customer.yaml failed validation: ' + JSON.stringify(result.errors)
    )
  }
  return result.value
}

describe('routine-grid parser: the A&P-shaped fixture grid', () => {
  it('validates and exposes the authored header + all rows', () => {
    const grid = liveGrid()
    expect(grid.adr).toBe('0075')
    expect(grid.seat).toBe('pilot-smokeball')
    expect(grid.persona).toBe('operator')
    // Row count is asserted from the parsed data, not hardcoded in the parser.
    expect(grid.rows.length).toBeGreaterThan(0)
    for (const row of grid.rows) {
      expect(['flag-only', 'prepare-and-route', 'auto-handle']).toContain(row.start_tier)
      expect(['flag-only', 'prepare-and-route', 'auto-handle']).toContain(row.ceiling_tier)
      expect(row.skills.length).toBeGreaterThan(0)
      expect(typeof row.enforcement.content_floor).toBe('boolean')
    }
  })

  it('parses enforcement.exposure_keys as a string map and banned_tools as a list', () => {
    const grid = liveGrid()
    const setup = grid.rows.find((r) => r.routine === 'New matter setup')
    expect(setup).toBeDefined()
    expect(setup!.enforcement.banned_tools).toContain('mcp_smokeball_create_matter')
    for (const value of Object.values(setup!.enforcement.exposure_keys)) {
      expect(typeof value).toBe('string')
    }
  })
})

describe('routine-grid parser: rejection with path-named errors', () => {
  it('rejects a non-object root', () => {
    const result = validateRoutineGrid('not a grid')
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors[0].code).toBe('TypeMismatch')
      expect(result.errors[0].path).toBe('$')
    }
  })

  it('rejects a tier outside the closed vocabulary', () => {
    const raw = rawGrid() as { rows: Array<Record<string, unknown>> }
    raw.rows[0].start_tier = 'sometimes'
    const result = validateRoutineGrid(raw)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      const err = result.errors.find((e) => e.path === 'rows[0].start_tier')
      expect(err).toBeDefined()
      expect(err!.code).toBe('EnumViolation')
    }
  })

  it('rejects a row missing a required field', () => {
    const raw = rawGrid() as { rows: Array<Record<string, unknown>> }
    delete raw.rows[0].routine
    const result = validateRoutineGrid(raw)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      const err = result.errors.find((e) => e.path === 'rows[0].routine')
      expect(err).toBeDefined()
      expect(err!.code).toBe('MissingField')
    }
  })

  it('rejects a missing top-level field with a named path', () => {
    const raw = rawGrid() as Record<string, unknown>
    delete raw.persona
    const result = validateRoutineGrid(raw)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors.some((e) => e.path === 'persona' && e.code === 'MissingField')).toBe(
        true
      )
    }
  })
})

describe('routine-grid projection round-trip', () => {
  it('grid PRESENT: round-trips through projectRow without throwing', () => {
    const row = projectCustomerYamlToConfigRow(seatYaml(), CTX, liveGrid())
    expect(row.routine_grid_json).not.toBeNull()
    expect(() => projectRow(row)).not.toThrow()
    const config = projectRow(row)
    expect(config.routine_grid).not.toBeNull()
    expect(config.routine_grid!.rows.length).toBe(liveGrid().rows.length)
    expect(config.routine_grid!.persona).toBe('operator')
  })

  it('grid ABSENT: projects null and resolves to the gridless fallback', () => {
    // Both the undefined arg and an explicit null must project to a null column.
    for (const grid of [undefined, null]) {
      const row = projectCustomerYamlToConfigRow(seatYaml(), CTX, grid)
      expect(row.routine_grid_json).toBeNull()
      expect(() => projectRow(row)).not.toThrow()
      expect(projectRow(row).routine_grid).toBeNull()
    }
  })

  it('grid MALFORMED (bad JSON in the column): resolves to null, never throws', () => {
    const row = projectCustomerYamlToConfigRow(seatYaml(), CTX, liveGrid())
    row.routine_grid_json = '{ this is not valid json'
    expect(() => projectRow(row)).not.toThrow()
    expect(projectRow(row).routine_grid).toBeNull()
  })

  it('grid SHAPE-INVALID (valid JSON, fails the grid validator): resolves to null', () => {
    const row = projectCustomerYamlToConfigRow(seatYaml(), CTX, liveGrid())
    // Valid JSON, but rows is the wrong type — the validator rejects it.
    row.routine_grid_json = JSON.stringify({
      adr: '0075',
      seat: 's',
      persona: 'operator',
      source_letter: 'x',
      rows: 'nope',
    })
    expect(() => projectRow(row)).not.toThrow()
    expect(projectRow(row).routine_grid).toBeNull()
  })
})

describe('resolveRoutineGrid: defensive resolver contract', () => {
  it('null / undefined / empty-ish inputs resolve to null', () => {
    expect(resolveRoutineGrid(null)).toBeNull()
    expect(resolveRoutineGrid(undefined)).toBeNull()
    expect(resolveRoutineGrid('')).toBeNull()
    expect(resolveRoutineGrid('null')).toBeNull()
  })

  it('a well-formed serialized grid resolves back to a typed grid', () => {
    const grid = liveGrid()
    const resolved = resolveRoutineGrid(JSON.stringify(grid))
    expect(resolved).not.toBeNull()
    expect(resolved!.rows.length).toBe(grid.rows.length)
  })
})
