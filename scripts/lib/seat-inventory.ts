/**
 * The seat inventory: the ONE place the set of Operator seats is defined.
 *
 * A seat is a directory `operator/customers/<slug>/` that holds a
 * `customer.yaml`, is not a `_`-prefixed provisioning scaffold, is not a
 * `<slug>.decommissioned.<date>` tombstone, is not a dotfile, and has a valid
 * slug. Each seat also authors a console-side sidecar, `seat.yaml`, carrying
 * what used to be hand-kept elsewhere: its Fly app, its release ring, the day it
 * was authored (orders a ring), and the client its obligations roll up to.
 *
 * ## Why a sidecar and not keys in customer.yaml
 *
 * customer.yaml is the object a seat boots from: it is published to R2 and the
 * overlay's validator (`operator/hermes-smd-overlay/bootstrap/validate.py`) and
 * translator read it on the Machine. A new key there is a change to every
 * seat's boot input and a reason for a live-apply to fire; it can fail a boot
 * and it cannot be shipped from this repo alone. seat.yaml is read only here, by
 * this module, so nothing in it can break a seat at boot.
 *
 * ## What is derived from it
 *
 * `scripts/gen-seat-registries.ts` renders three committed files from
 * {@link loadSeatInventory}: `src/lib/operator/fly-app-registry.generated.ts`
 * (the ADR 0036 allow-list), `operator/fleet/rings.yaml` (read by the Python
 * fleet tool), and `scripts/lib/seat-clients.generated.mjs` (the obligation
 * register's seat-to-client rollup). They stay committed so no consumer changes
 * how it reads; `tests/seat-inventory.test.ts` re-renders them and fails on any
 * byte of drift.
 *
 * The same directory rule lives in Python (`operator/bin/lib/seat_dirs.py`) and
 * bash (`scripts/lib/seat-dir.sh`) for the tooling that cannot import this;
 * `tests/seat-inventory.test.ts` runs all three over one fixture tree.
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'

/** Canonical seat slug shape (#2285); mirrors SLUG_RE in operator/bin/lib/seat_dirs.py. */
export const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$/

const TOMBSTONE_MARKER = '.decommissioned.'
const FLY_APP_RE = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const SEAT_YAML_KEYS = new Set([
  'fly_app',
  'unprovisioned_reason',
  'ring',
  'authored',
  'client',
  'bake_minutes',
])

export type SkipReason = 'scaffold' | 'hidden' | 'tombstone'

/** Why a directory NAME is not a seat, or null when it could be one. */
export function skipReason(name: string): SkipReason | null {
  if (name.startsWith('_')) return 'scaffold'
  if (name.startsWith('.')) return 'hidden'
  if (name.includes(TOMBSTONE_MARKER)) return 'tombstone'
  return null
}

/** Every seat slug under `customersRoot`, sorted. The directory rule only; no sidecar read. */
export function seatSlugs(customersRoot: string): string[] {
  if (!existsSync(customersRoot)) return []
  return readdirSync(customersRoot)
    .filter((name) => {
      const dir = join(customersRoot, name)
      return (
        statSync(dir).isDirectory() &&
        skipReason(name) === null &&
        existsSync(join(dir, 'customer.yaml')) &&
        SLUG_RE.test(name)
      )
    })
    .sort()
}

export interface Seat {
  slug: string
  /** The seat's Fly app, or null when it deliberately has none (see unprovisionedReason). */
  flyApp: string | null
  unprovisionedReason: string | null
  ring: number
  /** YYYY-MM-DD the seat's customer.yaml first landed on main. */
  authored: string
  /** The client this seat's obligations roll up to; the slug itself when not authored. */
  client: string
  /**
   * Minutes this seat's ring must hold a release, done with no open seat alarm,
   * before the next ring takes it (ADR 0096). Null or absent: the default. A
   * ring bakes for the longest value any of its seats authors.
   */
  bakeMinutes?: number | null
}

export class SeatInventoryError extends Error {}

function fail(slug: string, msg: string): never {
  throw new SeatInventoryError(`operator/customers/${slug}/seat.yaml: ${msg}`)
}

/** `fly_app` and `unprovisioned_reason`: an authored app name, or null with a reason. */
function parseFlyApp(
  slug: string,
  doc: Record<string, unknown>
): Pick<Seat, 'flyApp' | 'unprovisionedReason'> {
  if (!('fly_app' in doc)) {
    fail(
      slug,
      'fly_app is required: the app name from `fly apps list`, or null with unprovisioned_reason. ' +
        `Do not assume hermes-${slug} (ADR 0036).`
    )
  }
  const flyApp = doc.fly_app
  const reason = doc.unprovisioned_reason
  if (flyApp === null) {
    if (typeof reason !== 'string' || !reason.trim())
      fail(slug, 'fly_app is null, so unprovisioned_reason must say why')
    return { flyApp: null, unprovisionedReason: reason.trim() }
  }
  if (typeof flyApp !== 'string' || !FLY_APP_RE.test(flyApp))
    fail(slug, `fly_app ${JSON.stringify(flyApp)} is not a Fly app name`)
  if (reason !== undefined) fail(slug, 'unprovisioned_reason is only for fly_app: null')
  return { flyApp, unprovisionedReason: null }
}

function parseSeat(slug: string, raw: unknown): Seat {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) fail(slug, 'must be a mapping')
  const doc = raw as Record<string, unknown>
  const unknown = Object.keys(doc).filter((k) => !SEAT_YAML_KEYS.has(k))
  if (unknown.length) fail(slug, `unknown key(s): ${unknown.join(', ')}`)

  const ring = doc.ring
  if (typeof ring !== 'number' || !Number.isInteger(ring) || ring < 0)
    fail(slug, 'ring is required: a non-negative integer')

  const authored = doc.authored
  if (typeof authored !== 'string' || !DATE_RE.test(authored))
    fail(slug, "authored is required: a quoted 'YYYY-MM-DD'")

  const client = doc.client ?? slug
  if (typeof client !== 'string' || !SLUG_RE.test(client))
    fail(slug, `client ${JSON.stringify(client)} is not a client slug`)

  return {
    slug,
    ...parseFlyApp(slug, doc),
    ring,
    authored,
    client,
    bakeMinutes: parseBake(slug, doc),
  }
}

/** `bake_minutes`: optional; a non-negative whole number of minutes (ADR 0096). */
function parseBake(slug: string, doc: Record<string, unknown>): number | null {
  const bake = doc.bake_minutes ?? null
  if (bake === null) return null
  if (typeof bake !== 'number' || !Number.isInteger(bake) || bake < 0)
    fail(slug, 'bake_minutes, when authored, is a non-negative whole number of minutes')
  return bake
}

/**
 * Every seat with its sidecar metadata, sorted by slug. Throws
 * {@link SeatInventoryError} when a seat has no seat.yaml or an invalid one, or
 * when two seats claim one Fly app (one slug addressing another tenant's
 * Machine is the failure ADR 0036 exists to refuse).
 */
export function loadSeatInventory(customersRoot: string): Seat[] {
  const seats = seatSlugs(customersRoot).map((slug) => {
    const path = join(customersRoot, slug, 'seat.yaml')
    if (!existsSync(path)) {
      fail(
        slug,
        'missing. Every seat authors one (fly_app, ring, authored, optional client); ' +
          'copy a sibling seat.yaml and run `npm run gen:seat-registries`.'
      )
    }
    return parseSeat(slug, parseYaml(readFileSync(path, 'utf8')))
  })
  const byApp = new Map<string, string>()
  for (const seat of seats) {
    if (seat.flyApp === null) continue
    const prior = byApp.get(seat.flyApp)
    if (prior) fail(seat.slug, `fly_app ${seat.flyApp} is already ${prior}'s app`)
    byApp.set(seat.flyApp, seat.slug)
  }
  return seats
}
