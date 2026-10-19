/**
 * A role's persona prompt reaches the seat (ADR 0090).
 *
 * The overlay's translate renders /app/roles/<seat.role>/<persona> verbatim into
 * every profile's SOUL.md, and fails the boot when a seat names a role whose
 * package or persona file is missing. That makes three things on this side
 * load-bearing, each pinned here:
 *
 *   1. The image carries operator/roles/ at /app/roles/ (the overlay's default
 *      roles dir). Without the COPY, every role seat would fail its boot.
 *   2. Every role the validator accepts (SEAT_ROLES) has a package whose
 *      role.yaml `persona:` is a bare filename that exists. The overlay refuses
 *      a path, so a nested or missing persona is a boot failure, not a default.
 *   3. Every persona carries a `## Safety rules` section, the part the overlay
 *      test proves arrives verbatim.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { SEAT_ROLES } from '../src/lib/operator/customer-yaml/types'

const DOCKERFILE = readFileSync(resolve('operator/templates/Dockerfile'), 'utf8')

describe('role persona delivery', () => {
  it('the image copies operator/roles/ to /app/roles/', () => {
    expect(DOCKERFILE).toMatch(/^COPY operator\/roles\/ \/app\/roles\/$/m)
  })

  for (const role of SEAT_ROLES) {
    describe(`role ${role}`, () => {
      const dir = resolve('operator/roles', role)
      const manifestPath = resolve(dir, 'role.yaml')

      it('has a role.yaml naming itself', () => {
        expect(existsSync(manifestPath)).toBe(true)
        const manifest = parseYaml(readFileSync(manifestPath, 'utf8')) as {
          role: string
          persona: unknown
        }
        expect(manifest.role).toBe(role)
      })

      it('names a bare persona file that exists and carries safety rules', () => {
        const manifest = parseYaml(readFileSync(manifestPath, 'utf8')) as { persona: unknown }
        expect(typeof manifest.persona).toBe('string')
        const persona = manifest.persona as string
        expect(persona).toMatch(/^[A-Za-z0-9][A-Za-z0-9._-]*$/)
        const personaPath = resolve(dir, persona)
        expect(existsSync(personaPath)).toBe(true)
        const body = readFileSync(personaPath, 'utf8')
        expect(body.trim().length).toBeGreaterThan(0)
        // The couples persona names its section "A few rules that always hold"
        // since it was rewritten as an assistant (2026-10-19).
        expect(body).toMatch(/^## (Safety rules|A few rules that always hold)$/m)
      })
    })
  }
})
