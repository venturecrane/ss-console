/**
 * Policy inputs: the console half of the seat x gate matrix (ADR 0093).
 *
 * WHAT THIS PINS. What a seat may send is decided by code floors in the overlay
 * plus the `policy:` block of the role the seat selects
 * (`operator/roles/<seat.role>/role.yaml`). The overlay resolves every seat
 * with its real resolver against a hand-maintained expected matrix
 * (overlay tests/contract/policy_matrix_expected.json), so that ANY change to
 * what a seat allows shows up in review as a seat x gate diff. The overlay
 * cannot read this repo at test time, so the inputs it resolves come from here:
 * each live seat's `seat.role` and that role's `policy:` block, verbatim.
 *
 * WHY A CHECKED-IN FILE, NOT A LIVE READ. Same reason as the gate-coverage
 * snapshot (tests/gate-coverage-snapshot.test.ts): a fixture the overlay keeps
 * by hand drifts the moment a seat or a role is edited here, and silently, in
 * the direction that matters (a seat that gains a role, or a role that gains a
 * loosening). Regenerating from the real files and failing on drift makes the
 * two repos move together.
 *
 * REGENERATE:
 *
 *   UPDATE_POLICY_MATRIX=1 npx vitest run tests/policy-matrix.test.ts
 *
 * Then COPY operator/contracts/policy-inputs.overlay.json over the overlay's
 * tests/contract/policy_inputs.json in the SAME change. The overlay has no way
 * to pull it, and its matrix test is what turns the new inputs into a reviewed
 * seat x gate diff.
 *
 * Which seats: every operator/customers/<slug>/customer.yaml except the
 * underscore-prefixed provisioning scaffolds and `<slug>.decommissioned.<date>`
 * tombstones (the rule tests/shipped-customer-configs.test.ts uses).
 *
 * @see docs/adr/0093-seat-policy-profiles.md
 * @see tests/role-policy.test.ts (what a role's `policy:` block may contain)
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { execFileSync } from 'node:child_process'
import { parse as parseYaml } from 'yaml'

const CUSTOMERS_DIR = 'operator/customers'
const ROLES_DIR = 'operator/roles'
const ARTIFACT_PATH = 'operator/contracts/policy-inputs.overlay.json'

const COMMENT =
  'Policy inputs for every live seat, generated from ss-console operator/customers/*/customer.yaml ' +
  '(seat.role) and operator/roles/<role>/role.yaml (the policy: block, verbatim) by ' +
  'tests/policy-matrix.test.ts (ADR 0093). Underscore-prefixed scaffolds and .decommissioned. ' +
  'tombstones are excluded. role is null when the seat selects no role; role_policy is null when ' +
  'the role authors no policy: block (both resolve to the platform default). Do not hand-edit: ' +
  'regenerate with UPDATE_POLICY_MATRIX=1, then copy this file over hermes-smd-overlay ' +
  'tests/contract/policy_inputs.json in the same change, where tests/test_policy_matrix.py ' +
  'resolves it against tests/contract/policy_matrix_expected.json.'

interface SeatPolicyInputs {
  readonly role: string | null
  readonly role_policy: Record<string, unknown> | null
}

const isLiveSeatDir = (name: string): boolean =>
  !name.startsWith('_') && !name.includes('.decommissioned.')

function seatDirs(): string[] {
  return readdirSync(resolve(CUSTOMERS_DIR), { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort()
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null
}

function rolePolicy(role: string): Record<string, unknown> | null {
  const path = resolve(ROLES_DIR, role, 'role.yaml')
  // A seat naming a role with no role.yaml is a seat the overlay resolves to the
  // platform default. That is safe, but it is also almost certainly a typo, so
  // it fails here rather than shipping as a silently strict seat.
  if (!existsSync(path))
    throw new Error(`seat.role "${role}" has no ${ROLES_DIR}/${role}/role.yaml`)
  const doc = asRecord(parseYaml(readFileSync(path, 'utf8')))
  return asRecord(doc?.policy) ?? null
}

function regenerateSeats(): Record<string, SeatPolicyInputs> {
  const out: Record<string, SeatPolicyInputs> = {}
  for (const slug of seatDirs()) {
    if (!isLiveSeatDir(slug)) continue
    const doc = asRecord(
      parseYaml(readFileSync(resolve(CUSTOMERS_DIR, slug, 'customer.yaml'), 'utf8'))
    )
    const seat = asRecord(doc?.seat)
    const role = typeof seat?.role === 'string' ? seat.role : null
    out[slug] = { role, role_policy: role === null ? null : rolePolicy(role) }
  }
  return out
}

/** origin/main, not HEAD: this string is read in the OTHER repo, and a squash-
 *  merged feature-branch sha is a dead reference (see gate-coverage-snapshot). */
function consoleSha(): string {
  for (const rev of ['origin/main', 'HEAD']) {
    try {
      return execFileSync('git', ['rev-parse', rev], { encoding: 'utf8', stdio: 'pipe' }).trim()
    } catch {
      continue
    }
  }
  return 'unknown'
}

function render(seats: Record<string, SeatPolicyInputs>, generatedFrom: string): string {
  const ordered: Record<string, SeatPolicyInputs> = {}
  for (const slug of Object.keys(seats).sort()) ordered[slug] = seats[slug]
  return (
    JSON.stringify({ _comment: COMMENT, generated_from: generatedFrom, seats: ordered }, null, 2) +
    '\n'
  )
}

const normalize = (json: string): string =>
  json.replace(/("generated_from": )"[^"]*"/, '$1"<provenance-normalized>"')

const SEATS = regenerateSeats()

describe('policy inputs (ADR 0093)', () => {
  it('covers every live seat and no scaffold', () => {
    const all = seatDirs()
    expect(Object.keys(SEATS).length).toBeGreaterThan(0)
    expect(all.filter((d) => d.startsWith('_') && d in SEATS)).toEqual([])
    expect(all.filter((d) => isLiveSeatDir(d) && !(d in SEATS))).toEqual([])
  })

  it('carries at least one seat with a role, so the projection is not inert', () => {
    // A projection that maps every seat to null agrees with an all-null file
    // forever. captain-mark selects `couples`; if no seat selects a role, this
    // file stops testing anything and should be looked at.
    expect(Object.values(SEATS).some((s) => s.role !== null)).toBe(true)
  })

  it('the checked-in artifact matches the seats and roles (regenerate with UPDATE_POLICY_MATRIX=1)', () => {
    const fresh = render(SEATS, `venturecrane/ss-console@${consoleSha()}`)
    if (process.env.UPDATE_POLICY_MATRIX === '1') {
      writeFileSync(resolve(ARTIFACT_PATH), fresh)
      return
    }
    expect(
      existsSync(resolve(ARTIFACT_PATH)),
      `${ARTIFACT_PATH} missing; run UPDATE_POLICY_MATRIX=1 npx vitest run tests/policy-matrix.test.ts`
    ).toBe(true)
    const onDisk = readFileSync(resolve(ARTIFACT_PATH), 'utf8')
    expect(
      normalize(onDisk),
      'policy inputs drifted from the seats/roles. Regenerate with UPDATE_POLICY_MATRIX=1, then ' +
        'copy the file over hermes-smd-overlay tests/contract/policy_inputs.json in the same change.'
    ).toBe(normalize(fresh))
    expect(JSON.parse(onDisk).generated_from).toMatch(/^venturecrane\/ss-console@[0-9a-f]{40}$/)
  })
})
