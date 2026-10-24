/**
 * Renderers for the files derived from the seat inventory
 * (scripts/lib/seat-inventory.ts). Pure: inventory in, file text out. The
 * generator (scripts/gen-seat-registries.ts) writes them; the drift test
 * (tests/seat-inventory.test.ts) renders them and compares to what is committed.
 * Output goes through the repo's prettier config, so a generated file is
 * format:check-clean by construction.
 */

import { format, resolveConfig } from 'prettier'
import { resolve } from 'node:path'
import type { Seat } from './seat-inventory'

export interface GeneratedFile {
  /** Repo-relative path. */
  path: string
  text: string
}

const REGEN = 'Regenerate with `npm run gen:seat-registries`'

const RINGS_HEADER = `# GENERATED from operator/customers/<slug>/seat.yaml (\`ring:\`) by
# scripts/gen-seat-registries.ts. Do not edit: change the seat's seat.yaml and
# run \`npm run gen:seat-registries\`. tests/seat-inventory.test.ts fails while
# this file is stale.
#
# Release rings: which seats take a release first. MEMBERSHIP ONLY.
#
# \`operator/bin/fleet release\` walks these in order. A ring finishes before the
# next starts; any seat that FAILS (smoke red, so it is rolled back) or ends in a
# TOOLING ERROR halts the whole rollout, so a defect stops at the smallest ring
# that shows it. Ring 0 is SMD's own staging seat; the last ring is the paying
# client's seat and always deploys one seat at a time.
#
# Holds are NOT authored here. \`fleet hold <slug> --reason "..."\` writes D1
# fleet_holds and takes effect at once, without a PR (a held seat is skipped
# and recorded as skipped_held).
#
# Every seat authors its ring, so none is in a ring by omission. As a backstop,
# operator/bin/lib/fleet/rings.py still puts a seat named in no ring in the LAST
# ring: a new seat is never in an early ring by accident.
#
# bake_minutes (ADR 0096): the next ring takes a release only after every seat
# of this ring has been done on it this long with no open selftest_failing or
# image_drift alarm. The longest seat.yaml \`bake_minutes\` in the ring, else the
# default. The last ring has no next ring, so it carries none.
# tests: operator/bin/tests/test_fleet_parts.py
`

/** The bake when no seat in a ring authors one; rings.py's DEFAULT_BAKE_MINUTES. */
export const DEFAULT_BAKE_MINUTES = 30

/** Seats in ring order, ordered within a ring by the day each was authored, then slug. */
function ringsOf(seats: Seat[]): Array<{ ring: number; seats: string[]; bake: number }> {
  const numbers = [...new Set(seats.map((s) => s.ring))].sort((a, b) => a - b)
  return numbers.map((ring) => {
    const members = seats.filter((s) => s.ring === ring)
    const authored = members
      .map((s) => s.bakeMinutes)
      .filter((m): m is number => typeof m === 'number')
    return {
      ring,
      seats: members
        .sort((a, b) => a.authored.localeCompare(b.authored) || a.slug.localeCompare(b.slug))
        .map((s) => s.slug),
      bake: authored.length ? Math.max(...authored) : DEFAULT_BAKE_MINUTES,
    }
  })
}

export function renderRingsYaml(seats: Seat[]): string {
  const rings = ringsOf(seats)
  const lines = ['rings:']
  rings.forEach((r, i) => {
    lines.push(`  - ring: ${r.ring}`, `    seats: [${r.seats.join(', ')}]`)
    // rings.py forces the last ring to one at a time regardless; stated here so
    // the file says what the tool does.
    if (i === rings.length - 1) lines.push('    max_parallel: 1')
    else lines.push(`    bake_minutes: ${r.bake}`)
  })
  return RINGS_HEADER + lines.join('\n') + '\n'
}

const IDENT_RE = /^[A-Za-z_$][A-Za-z0-9_$]*$/
const key = (k: string) => (IDENT_RE.test(k) ? k : `'${k}'`)

export function renderFlyAppRegistry(seats: Seat[]): string {
  const apps = seats.filter((s) => s.flyApp !== null)
  const none = seats.filter((s) => s.flyApp === null)
  return `/**
 * GENERATED from operator/customers/<slug>/seat.yaml (\`fly_app:\`) by
 * scripts/gen-seat-registries.ts. Do not edit. ${REGEN};
 * tests/seat-inventory.test.ts fails while this file is stale.
 *
 * Data only. The fail-closed resolution and its rationale (ADR 0036, ss#2283)
 * live in fly-app-registry.ts, which is the only module that should import this.
 */

/** Seat slug -> its Fly app, as authored in each seat's seat.yaml. */
export const CUSTOMER_FLY_APPS: Readonly<Record<string, string>> = Object.freeze({
${apps.map((s) => `  ${key(s.slug)}: '${s.flyApp}',`).join('\n')}
})

/** Seats authored with \`fly_app: null\`, each with its unprovisioned_reason. */
export const UNPROVISIONED_CUSTOMERS: Readonly<Record<string, string>> = Object.freeze({
${none.map((s) => `  ${key(s.slug)}: ${JSON.stringify(s.unprovisionedReason)},`).join('\n')}
})
`
}

export function renderSeatClients(seats: Seat[]): string {
  const rolled = seats.filter((s) => s.client !== s.slug)
  return `// GENERATED from operator/customers/<slug>/seat.yaml (\`client:\`) by
// scripts/gen-seat-registries.ts. Do not edit. ${REGEN};
// tests/seat-inventory.test.ts fails while this file is stale.
// Read through scripts/lib/seat-clients.mjs, never directly.

/** [seat, client] for every seat that is NOT its own client. */
export const INVENTORY_SEAT_CLIENTS = Object.freeze([
${rolled.map((s) => `  ['${s.slug}', '${s.client}'],`).join('\n')}
])
`
}

export const GENERATED_PATHS = {
  flyApps: 'src/lib/operator/fly-app-registry.generated.ts',
  rings: 'operator/fleet/rings.yaml',
  seatClients: 'scripts/lib/seat-clients.generated.mjs',
} as const

async function pretty(repoRoot: string, path: string, text: string): Promise<string> {
  const abs = resolve(repoRoot, path)
  const options = (await resolveConfig(abs)) ?? {}
  return format(text, { ...options, filepath: abs })
}

/** Every derived file, formatted, in a stable order. */
export async function renderSeatRegistries(
  repoRoot: string,
  seats: Seat[]
): Promise<GeneratedFile[]> {
  const raw: GeneratedFile[] = [
    { path: GENERATED_PATHS.flyApps, text: renderFlyAppRegistry(seats) },
    { path: GENERATED_PATHS.rings, text: renderRingsYaml(seats) },
    { path: GENERATED_PATHS.seatClients, text: renderSeatClients(seats) },
  ]
  return Promise.all(
    raw.map(async (f) => ({ path: f.path, text: await pretty(repoRoot, f.path, f.text) }))
  )
}
