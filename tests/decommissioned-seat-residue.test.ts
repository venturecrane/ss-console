/**
 * Repo-layer guard for a retired seat: once `operator/customers/<slug>/` is
 * tombstoned as `<slug>.decommissioned.<date>`, no authored allow-list may
 * still name `<slug>`.
 *
 * WHY THIS EXISTS. `operator/bin/decommission-customer.sh` removes a seat from
 * every runtime layer it can reach, but it does not commit, so the repo-layer
 * entries are edits a person has to make: the release ring
 * (`operator/fleet/rings.yaml`), the Fly app allow-list
 * (`src/lib/operator/fly-app-registry.ts`) and the ops workspace registry
 * (`operator/ops-machine/workspaces.json`). The live run prints each edit
 * (`decommission_inventory.repo_edits`), and a printed instruction is exactly
 * the kind of thing that gets lost between the run and the PR. This test is
 * the half that cannot be forgotten: the PR that commits the tombstone fails
 * until the three files are clean.
 *
 * Each entry it catches is a real hazard, not tidiness. A registry entry
 * keeps `hermes-<slug>` targetable (ADR 0036's cross-tenant concern), and a
 * Fly app name is reusable by anyone once destroyed. A ring entry is filtered
 * silently by `operator/bin/lib/fleet/rings.py`, so nothing else would ever
 * notice it. A workspace entry points the daily sweep and the laptop launcher
 * at an app that no longer exists.
 *
 * The check functions take their inputs as arguments, and the last block runs
 * them on synthetic input that MUST fail, so the guard is proven to bite even
 * while no seat in this checkout is tombstoned.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  REGISTERED_CUSTOMER_FLY_APPS,
  UNPROVISIONED_CUSTOMERS,
} from '../src/lib/operator/fly-app-registry'

const CUSTOMERS_DIR = resolve('operator/customers')
const RINGS_FILE = resolve('operator/fleet/rings.yaml')
const WORKSPACES_FILE = resolve('operator/ops-machine/workspaces.json')

/** `<slug>.decommissioned.<YYYY-MM-DD>`, the FilesystemTombstoner's shape. */
const TOMBSTONE = /^([a-z0-9]+(?:-[a-z0-9]+)*)\.decommissioned\.\d{4}-\d{2}-\d{2}$/

/** Slugs tombstoned in `dirNames` that have no live seat dir beside them. */
function retiredSlugs(dirNames: string[], liveSlugs: Set<string>): string[] {
  const out = new Set<string>()
  for (const name of dirNames) {
    const m = TOMBSTONE.exec(name)
    if (m && !liveSlugs.has(m[1])) out.add(m[1])
  }
  return [...out].sort()
}

/** Every allow-list entry naming a retired slug, as `file: slug`. */
function residue(
  retired: string[],
  sources: {
    rings: unknown
    registry: Readonly<Record<string, string>>
    unprovisioned: Readonly<Record<string, string>>
    workspaces: unknown
  }
): string[] {
  const ringSeats = new Set<string>()
  const rings = (sources.rings as { rings?: { seats?: unknown[] }[] } | null)?.rings ?? []
  for (const ring of rings) for (const s of ring.seats ?? []) ringSeats.add(String(s))
  const ws =
    (sources.workspaces as { workspaces?: Record<string, unknown> } | null)?.workspaces ?? {}
  const found: string[] = []
  for (const slug of retired) {
    if (ringSeats.has(slug)) found.push(`operator/fleet/rings.yaml: ${slug}`)
    if (slug in sources.registry)
      found.push(`src/lib/operator/fly-app-registry.ts CUSTOMER_FLY_APPS: ${slug}`)
    if (slug in sources.unprovisioned)
      found.push(`src/lib/operator/fly-app-registry.ts UNPROVISIONED_CUSTOMERS: ${slug}`)
    if (slug in ws) found.push(`operator/ops-machine/workspaces.json: ${slug}`)
  }
  return found
}

function realInputs() {
  const entries = existsSync(CUSTOMERS_DIR)
    ? readdirSync(CUSTOMERS_DIR, { withFileTypes: true }).filter((d) => d.isDirectory())
    : []
  const live = new Set(
    entries
      .map((d) => d.name)
      .filter((n) => !n.includes('.decommissioned.') && !n.startsWith('_'))
      .filter((n) => existsSync(join(CUSTOMERS_DIR, n, 'customer.yaml')))
  )
  return {
    dirNames: entries.map((d) => d.name),
    live,
    rings: parseYaml(readFileSync(RINGS_FILE, 'utf8')),
    workspaces: JSON.parse(readFileSync(WORKSPACES_FILE, 'utf8')),
  }
}

describe('decommissioned seat: repo-layer residue', () => {
  it('no allow-list names a seat whose dir is tombstoned', () => {
    const { dirNames, live, rings, workspaces } = realInputs()
    const retired = retiredSlugs(dirNames, live)
    const found = residue(retired, {
      rings,
      registry: REGISTERED_CUSTOMER_FLY_APPS,
      unprovisioned: UNPROVISIONED_CUSTOMERS,
      workspaces,
    })
    expect(
      found,
      `A decommissioned seat is still named in an authored allow-list:\n  ${found.join('\n  ')}\n\n` +
        `Remove each entry in the same PR that commits the tombstone. The live decommission ` +
        `printed these as "[residue] repo edit owed" lines (operator/bin/lib/decommission_inventory.py).`
    ).toEqual([])
  })

  it('reads the allow-lists it guards (a parse failure is not a pass)', () => {
    const { rings, workspaces } = realInputs()
    expect(Array.isArray((rings as { rings?: unknown }).rings)).toBe(true)
    expect(typeof (workspaces as { workspaces?: unknown }).workspaces).toBe('object')
  })
})

describe('decommissioned seat guard: proven to bite', () => {
  const sources = {
    rings: { rings: [{ seats: ['other'] }, { seats: ['acme', 'acme-two'] }] },
    registry: { acme: 'hermes-acme', 'acme-two': 'hermes-acme-two', other: 'hermes-other' },
    unprovisioned: {},
    workspaces: { workspaces: { acme: { app: 'ops-acme' } } },
  }

  it('finds a retired slug in every allow-list, and only that slug', () => {
    const retired = retiredSlugs(
      ['acme.decommissioned.2026-10-22', 'acme-two', 'other', '_template'],
      new Set(['acme-two', 'other'])
    )
    expect(retired).toEqual(['acme'])
    expect(residue(retired, sources)).toEqual([
      'operator/fleet/rings.yaml: acme',
      'src/lib/operator/fly-app-registry.ts CUSTOMER_FLY_APPS: acme',
      'operator/ops-machine/workspaces.json: acme',
    ])
  })

  it('does not treat a re-provisioned slug (live dir beside an old tombstone) as retired', () => {
    expect(retiredSlugs(['acme.decommissioned.2026-01-01', 'acme'], new Set(['acme']))).toEqual([])
  })

  it('ignores a dir that only looks like a tombstone', () => {
    expect(retiredSlugs(['acme.decommissioned.latest', 'acme.old'], new Set())).toEqual([])
  })
})
