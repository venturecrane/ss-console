/**
 * The read-side check on `personas_json` (src/lib/portal/persona-config-shape.ts).
 *
 * Until 2026-09-25 projectRow cast the parsed column to PersonaConfig[]; a row
 * of the wrong shape reached the portal typed as right (review 2026-09-25,
 * Code Quality 2). These tests pin both halves of the replacement:
 *
 *   1. Every seat's authored customer.yaml, projected by the canonical mapper,
 *      reads back through the check unchanged (the check is lossless on the
 *      shape the projection writes, so it cannot 500 a real seat's portal).
 *   2. A row that drifts from that shape is refused with the path of the
 *      first mismatch, instead of being cast.
 *
 * What would make this false: a projection field the check does not carry
 * (1 fails on deep equality), or a check that accepts a wrong type (2).
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import { projectCustomerYamlToConfigRow } from '../src/lib/portal/customer-config-projection'
import { projectRow } from '../src/lib/portal/customer-config'
import { PersonaShapeError, readPersonaConfigs } from '../src/lib/portal/persona-config-shape'

const CTX = { entityId: 'e', orgId: 'o', gitSha: 'sha', syncedAt: '2026-09-25T00:00:00.000Z' }
const CUSTOMERS = resolve('operator/customers')
const SEATS = readdirSync(CUSTOMERS).filter(
  (slug) => !slug.startsWith('_') && existsSync(resolve(CUSTOMERS, slug, 'customer.yaml'))
)

describe('personas_json read-side check: every seat round-trips', () => {
  it('finds the seats', () => {
    expect(SEATS.length).toBeGreaterThan(0)
  })

  for (const slug of SEATS) {
    it(`${slug}: the projected personas read back unchanged`, () => {
      const result = validate(
        parseYaml(readFileSync(resolve(CUSTOMERS, slug, 'customer.yaml'), 'utf-8'))
      )
      if (!result.ok) throw new Error(`${slug} customer.yaml failed validation`)
      const row = projectCustomerYamlToConfigRow(result.value, CTX)
      const written: unknown = JSON.parse(row.personas_json)
      expect(projectRow(row).personas).toEqual(written)
    })
  }
})

const valid = {
  slug: 'operator',
  status: 'active',
  name: 'Operator',
  title: null,
  signature_html: null,
  tone: ['plain'],
  send_as: { send_identity: { provider: 'msgraph', address: 'op@example.com' } },
  entitlements: {
    exposure: { external_send: 'draft_for_review' },
    exposure_ceiling: { external_send: 'confirm' },
  },
  skills: [
    {
      name: 'inbox-triage',
      initiation: { manual: true, scheduled: false, webhook: true },
      settings: { chase_cadence_days: 5 },
    },
  ],
  cron: [{ skill: 'inbox-triage', schedule: '0 7 * * *' }],
  channel_bindings: [{ integration: 'gmail', channels: ['inbox'] }],
}

describe('personas_json read-side check: drift is refused and named', () => {
  it('accepts the projected shape, and a row predating cron', () => {
    expect(readPersonaConfigs([valid])).toEqual([valid])
    const { cron: _cron, ...preCron } = valid
    expect(readPersonaConfigs([preCron])[0].cron).toBeUndefined()
  })

  const cases: Array<[string, unknown, string]> = [
    ['a non-array column', { ...valid }, 'personas is not an array'],
    ['a missing tone', [{ ...valid, tone: undefined }], 'personas[0].tone is not an array'],
    ['an unknown status', [{ ...valid, status: 'paused' }], 'personas[0].status'],
    [
      'an exposure value outside the ceiling set',
      [{ ...valid, entitlements: { exposure: { external_send: 'yolo' } } }],
      'personas[0].entitlements.exposure.external_send',
    ],
    [
      'a skill initiation that is not three booleans',
      [{ ...valid, skills: [{ name: 's', initiation: { manual: 'yes' } }] }],
      'personas[0].skills[0].initiation.manual',
    ],
    [
      'a send provider nobody wired',
      [{ ...valid, send_as: { send_identity: { provider: 'smtp', address: 'a@b.c' } } }],
      'personas[0].send_as.send_identity.provider',
    ],
  ]

  for (const [label, value, path] of cases) {
    it(`refuses ${label}`, () => {
      expect(() => readPersonaConfigs(value)).toThrow(PersonaShapeError)
      expect(() => readPersonaConfigs(value)).toThrow(path)
    })
  }

  it('skips an exposure key this build does not know rather than refusing the row', () => {
    const newer = { ...valid, entitlements: { exposure: { future_class: 'confirm' } } }
    expect(readPersonaConfigs([newer])[0].entitlements.exposure).toEqual({})
  })
})
