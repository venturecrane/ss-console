/**
 * routine_names drift gate (#2222).
 *
 * Both seats author a top-level `routine_names:` map (skill slug -> the
 * routine's firm-legible name). Its runtime consumer is the operator-introduce
 * skill, which reads it off the seat's own /var/lib/smd-config/customer.yaml
 * so an admin can ask "what are your routines?" and hear the names the firm
 * was actually promised. The names originate in the routine-grid rows (which
 * themselves trace verbatim to the client letter), so this gate pins the map
 * to the grid the same way the grid is pinned to the letter:
 *
 *   1. every routine_names key is a skill bound on that seat's persona;
 *   2. every skill named by a routine-grid row has a routine_names entry;
 *   3. the entry's value equals the row's `routine:` byte-for-byte.
 *
 * Without (3), the introduce reply and the routines detail document the firm
 * holds can drift apart, which is exactly the card falsifier ("a schedule
 * that contradicts the document the firm holds") in name form.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { parse as parseYaml } from 'yaml'

// Literal path constants (never interpolated) so the paths are auditable and
// the semgrep path-traversal rule has nothing to flag.
const SEAT_PATHS = {
  'ashton-price': {
    yaml: resolve('operator/customers/ashton-price/customer.yaml'),
    grid: resolve('operator/customers/ashton-price/routine-grid.yaml'),
  },
} as const

const SEATS = Object.keys(SEAT_PATHS) as Array<keyof typeof SEAT_PATHS>

interface GridRow {
  routine: string
  skills: string[]
}

function loadSeat(slug: keyof typeof SEAT_PATHS) {
  const raw = parseYaml(readFileSync(SEAT_PATHS[slug].yaml, 'utf-8')) as Record<string, unknown>
  const grid = parseYaml(readFileSync(SEAT_PATHS[slug].grid, 'utf-8')) as { rows: GridRow[] }
  const routineNames = (raw.routine_names ?? {}) as Record<string, string>
  const personas = (raw.personas ?? []) as Array<{ skills?: Array<{ name: string }> }>
  const boundSkills = new Set(personas.flatMap((p) => (p.skills ?? []).map((s) => s.name)))
  return { routineNames, grid, boundSkills }
}

describe('routine_names <-> routine-grid drift gate', () => {
  for (const slug of SEATS) {
    describe(slug, () => {
      const { routineNames, grid, boundSkills } = loadSeat(slug)

      it('authors a non-empty routine_names map', () => {
        expect(Object.keys(routineNames).length).toBeGreaterThan(0)
      })

      it('every routine_names key is a skill bound on this seat', () => {
        for (const key of Object.keys(routineNames)) {
          expect(
            boundSkills.has(key),
            `${slug}: routine_names key '${key}' is not a bound skill`
          ).toBe(true)
        }
      })

      it('every grid-row skill has an entry carrying the row name verbatim', () => {
        for (const row of grid.rows) {
          for (const skill of row.skills) {
            expect(
              routineNames[skill],
              `${slug}: grid row '${row.routine}' names skill '${skill}' with no routine_names entry`
            ).toBeDefined()
            expect(
              routineNames[skill],
              `${slug}: routine_names['${skill}'] must equal the grid row's routine name byte-for-byte`
            ).toBe(row.routine)
          }
        }
      })
    })
  }
})
