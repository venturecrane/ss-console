/**
 * Role policy schema (ADR 0093).
 *
 * A role's `policy:` block (operator/roles/<role>/role.yaml) is the only
 * authored way to loosen a seat's outbound gates. The overlay resolver treats
 * an unknown key or value as "platform default", which is safe at runtime and
 * silent in review: a typo would ship a seat stricter than intended and nobody
 * would know why its reminders stopped. So the schema is strict here, before
 * merge, with the SAME keys and enums the resolver accepts
 * (hermes-smd-overlay shared/policy_profile.py).
 *
 * Every value looser than the platform default must carry a non-empty
 * `rationale` and an `adr` that names a record existing in docs/adr/. Floors
 * (SSN, A-number, receipt number, case number, case-number-and-date pair) are
 * code and have no key here at all.
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

const ROLES_DIR = 'operator/roles'

/** Each knob: its allowed values, the first being the platform default. */
const KNOBS: Record<string, Record<string, readonly string[]>> = {
  identifier_gate: { recurring_dates: ['block', 'verify_by_month_day'] },
  output_checklist: { profile: ['paralegal', 'household'] },
}
const CITATION_KEYS = ['rationale', 'adr'] as const
const TOP_KEYS = new Set([...Object.keys(KNOBS), ...CITATION_KEYS])

type Policy = Record<string, unknown>

/** Every schema violation in a policy block, as readable strings. */
function policyErrors(policy: unknown): string[] {
  const errors: string[] = []
  if (typeof policy !== 'object' || policy === null || Array.isArray(policy)) {
    return ['policy must be a mapping']
  }
  const p = policy as Policy
  let looser = false
  for (const key of Object.keys(p)) {
    if (!TOP_KEYS.has(key)) errors.push(`unknown key policy.${key}`)
  }
  for (const [section, knobs] of Object.entries(KNOBS)) {
    if (!(section in p)) continue
    const block = p[section]
    if (typeof block !== 'object' || block === null || Array.isArray(block)) {
      errors.push(`policy.${section} must be a mapping`)
      continue
    }
    for (const [knob, value] of Object.entries(block as Policy)) {
      const allowed = knobs[knob]
      if (!allowed) {
        errors.push(`unknown key policy.${section}.${knob}`)
        continue
      }
      if (typeof value !== 'string' || !allowed.includes(value)) {
        errors.push(`policy.${section}.${knob} must be one of ${allowed.join(', ')}`)
        continue
      }
      if (value !== allowed[0]) looser = true
    }
  }
  for (const key of CITATION_KEYS) {
    if (key in p && (typeof p[key] !== 'string' || p[key].trim() === '')) {
      errors.push(`policy.${key} must be a non-empty string`)
    }
  }
  if (looser) {
    for (const key of CITATION_KEYS) {
      if (!(key in p)) errors.push(`a loosening requires policy.${key}`)
    }
    const adr = p.adr
    if (typeof adr === 'string' && adr.trim() !== '') {
      if (!/^docs\/adr\/\d{4}-[a-z0-9-]+\.md$/.test(adr)) {
        errors.push(`policy.adr must be a docs/adr/NNNN-*.md path, got "${adr}"`)
      } else if (!existsSync(resolve(adr))) {
        errors.push(`policy.adr names a record that does not exist: ${adr}`)
      }
    }
  }
  return errors
}

const roles = readdirSync(resolve(ROLES_DIR), { withFileTypes: true })
  .filter((d) => d.isDirectory() && existsSync(resolve(ROLES_DIR, d.name, 'role.yaml')))
  .map((d) => d.name)
  .sort()

describe('role policy schema (ADR 0093)', () => {
  it('has roles to check', () => {
    expect(roles.length).toBeGreaterThan(0)
  })

  for (const role of roles) {
    it(`${role}: policy block uses only known keys and values, and cites any loosening`, () => {
      const doc = parseYaml(readFileSync(resolve(ROLES_DIR, role, 'role.yaml'), 'utf8')) as Record<
        string,
        unknown
      >
      if (!('policy' in doc)) return
      expect(policyErrors(doc.policy)).toEqual([])
    })
  }

  // The validator must be able to fail, in each direction it claims to check.
  it('refuses unknown keys, bad values, and uncited or mis-cited loosenings', () => {
    expect(policyErrors({ identifier_gate: { mode: 'report' } })).toContain(
      'unknown key policy.identifier_gate.mode'
    )
    expect(policyErrors({ vertical_floor: 'off' })).toContain('unknown key policy.vertical_floor')
    expect(policyErrors({ output_checklist: { profile: 'casual' } })).toEqual([
      'policy.output_checklist.profile must be one of paralegal, household',
    ])
    expect(policyErrors({ identifier_gate: { recurring_dates: 'verify_by_month_day' } })).toEqual([
      'a loosening requires policy.rationale',
      'a loosening requires policy.adr',
    ])
    expect(
      policyErrors({
        output_checklist: { profile: 'household' },
        rationale: 'x',
        adr: 'docs/adr/9999-nope.md',
      })
    ).toEqual(['policy.adr names a record that does not exist: docs/adr/9999-nope.md'])
    expect(
      policyErrors({ output_checklist: { profile: 'household' }, rationale: '  ', adr: '0093' })
    ).toEqual([
      'policy.rationale must be a non-empty string',
      'policy.adr must be a docs/adr/NNNN-*.md path, got "0093"',
    ])
  })

  it('accepts the platform default without a citation, and a cited loosening', () => {
    expect(
      policyErrors({
        identifier_gate: { recurring_dates: 'block' },
        output_checklist: { profile: 'paralegal' },
      })
    ).toEqual([])
    expect(
      policyErrors({
        identifier_gate: { recurring_dates: 'verify_by_month_day' },
        rationale: 'why',
        adr: 'docs/adr/0093-seat-policy-profiles.md',
      })
    ).toEqual([])
  })
})
