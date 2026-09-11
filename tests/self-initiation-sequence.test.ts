/**
 * self_initiation drift gate (ss#2220 rescoped).
 *
 * Both seats author a top-level `self_initiation:` block — the ordered list of
 * acts one Operator-admin request ("initialize yourself") runs, plus the
 * document-library location the conductor's status probe reads. Its runtime
 * consumer is the operator-self-initiation skill, which reads the block off
 * the seat's own /var/lib/smd-config/customer.yaml at turn time (the
 * routine_names materialization shape).
 *
 * The failure class this pins: an authored-but-unbound sequence act. The
 * conductor delegates each act by reading /app/skills/<slug>/SKILL.md and the
 * seat refuses skills its personas do not bind, so a sequence naming an
 * unbound skill fails silently at the exact moment the firm is being onboarded.
 *
 *   1. any seat that binds operator-self-initiation authors a non-empty
 *      sequence (a conductor with nothing to conduct is a config error, not a
 *      fail-closed state — fail-closed is not binding the conductor at all);
 *   2. every sequence act is a skill bound AND enabled on that seat's persona;
 *   3. every sequence act has a skill body under operator/skills/;
 *   4. document_library, when authored, carries a non-empty folder_name (the
 *      probe's search key; matter_hint stays optional until the blessing
 *      fixes a location).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'fs'
import { resolve } from 'path'
import { parse as parseYaml } from 'yaml'

// Literal path constants (never interpolated) so the paths are auditable.
const SEAT_PATHS = {
  'pilot-smokeball': resolve('operator/customers/pilot-smokeball/customer.yaml'),
  'ashton-price': resolve('operator/customers/ashton-price/customer.yaml'),
} as const

const SEATS = Object.keys(SEAT_PATHS) as Array<keyof typeof SEAT_PATHS>

const SKILL_DIRS = new Set(readdirSync(resolve('operator/skills')))

const CONDUCTOR = 'operator-self-initiation'

interface SelfInitiation {
  sequence?: string[]
  document_library?: {
    matter_hint?: string
    folder_name?: string
    matter_number?: string
    templates?: Record<string, string>
  }
}

/** The renderer's document classes (smokeball_connector/docx_format.py DOCUMENT_CLASSES). */
const DOCUMENT_CLASSES = [
  'discovery_set',
  'discovery_response',
  'demand_letter',
  'mediation_brief',
  'memo',
  'letter',
] as const

function loadSeat(slug: keyof typeof SEAT_PATHS) {
  const raw = parseYaml(readFileSync(SEAT_PATHS[slug], 'utf-8')) as Record<string, unknown>
  const selfInitiation = (raw.self_initiation as SelfInitiation | undefined) ?? null
  const personas = (raw.personas ?? []) as Array<{
    skills?: Array<{ name: string; enabled?: boolean }>
  }>
  const bindings = new Map<string, boolean>()
  for (const persona of personas) {
    for (const s of persona.skills ?? []) {
      bindings.set(s.name, s.enabled !== false)
    }
  }
  return { selfInitiation, bindings }
}

describe('self_initiation <-> seat-binding drift gate', () => {
  for (const slug of SEATS) {
    describe(slug, () => {
      const { selfInitiation, bindings } = loadSeat(slug)
      const bindsConductor = bindings.get(CONDUCTOR) === true

      it('a seat binding the conductor authors a non-empty sequence', () => {
        if (!bindsConductor) return
        expect(
          selfInitiation?.sequence?.length ?? 0,
          `${slug}: binds ${CONDUCTOR} but authors no self_initiation.sequence — ` +
            `a conductor with nothing to conduct is a config error`
        ).toBeGreaterThan(0)
      })

      it('every sequence act is bound and enabled on this seat', () => {
        for (const act of selfInitiation?.sequence ?? []) {
          expect(
            bindings.get(act),
            `${slug}: self_initiation.sequence names '${act}' which is not bound+enabled — ` +
              `the conductor would fail this act at the firm's onboarding`
          ).toBe(true)
        }
      })

      it('every sequence act has a skill body', () => {
        for (const act of selfInitiation?.sequence ?? []) {
          expect(
            SKILL_DIRS.has(act),
            `${slug}: self_initiation.sequence names '${act}' with no body under operator/skills/`
          ).toBe(true)
        }
      })

      it('document_library, when authored, carries the probe search key', () => {
        const lib = selfInitiation?.document_library
        if (!lib) return
        expect(
          lib.folder_name && lib.folder_name.trim().length > 0,
          `${slug}: self_initiation.document_library authored without folder_name — ` +
            `the status probe has no search key`
        ).toBe(true)
      })

      it('document_library format-template keys are well-shaped for the renderer (#2448)', () => {
        const lib = selfInitiation?.document_library
        if (!lib) return
        if (lib.matter_number !== undefined) {
          expect(
            typeof lib.matter_number === 'string' && lib.matter_number.trim().length > 0,
            `${slug}: document_library.matter_number must be a non-empty string (the library matter's number)`
          ).toBe(true)
        }
        if (lib.templates !== undefined) {
          for (const [cls, name] of Object.entries(lib.templates)) {
            expect(
              (DOCUMENT_CLASSES as readonly string[]).includes(cls),
              `${slug}: document_library.templates names unknown class '${cls}' (known: ${DOCUMENT_CLASSES.join(', ')})`
            ).toBe(true)
            expect(
              typeof name === 'string' && name.trim().length > 0,
              `${slug}: document_library.templates.${cls} must be a non-empty file name`
            ).toBe(true)
          }
          expect(
            lib.matter_number !== undefined,
            `${slug}: document_library.templates authored without matter_number — the renderer cannot resolve the library matter`
          ).toBe(true)
        }
      })
    })
  }
})
