/**
 * Drift guard: every authored Operator seat must have a DECIDED entry in
 * `src/lib/operator/fly-app-registry.ts` — either a Fly app, or a recorded
 * reason it has none.
 *
 * ## The defect this exists to make loud (ss#2283)
 *
 * The registry is an explicit allow-list on purpose (ADR 0036): addressing the
 * wrong app is a cross-tenant action, so an unlisted customer is rejected
 * rather than guessed. That default is correct and this guard does not touch
 * it. The problem is that the rejection is SILENT — a runtime read for an
 * unlisted slug throws and collapses to an honest-empty result, and
 * `readLiveOverrides` reports `unconfigured` (a bare null until ss#2314) so the
 * console renders the authored config. A seat that was simply never added to
 * the map is still indistinguishable from a seat with no read transport
 * configured, which is why this guard exists rather than a richer status.
 *
 * Proven live (vfy_01KZSK4TQF2G0PKNNWV6GNM0BQ): `customer_configs` held five
 * slugs and the map held three. `scott` and `smd-staging` were missing, and
 * `hermes-scott` is a deployed Fly app — a live seat whose every runtime
 * surface rendered empty, with nothing raised anywhere.
 *
 * ## Why `operator/customers/` is the source-of-truth side
 *
 * The obvious oracle is `customer_configs` itself, but CI has no live D1 read
 * (the sync job holds the Cloudflare token; the test suite does not), so a
 * guard querying D1 could not run on a PR — and a guard that cannot run cannot
 * fail. The authored configs are the upstream of that very table: ADR 0012 §5
 * makes `operator/customers/<slug>/customer.yaml` the git source of truth, and
 * `scripts/ci-sync-customer-configs.sh` projects exactly the non-`_` seat dirs
 * into `customer_configs`. So the directory listing is the same population one
 * step earlier in the pipeline, checkable offline, and it catches the gap
 * BEFORE the row is projected rather than after.
 *
 * The `shippedSlugs()` rule below is deliberately identical to the one in
 * `tests/shipped-customer-configs.test.ts` and to the CI sync script's own
 * filter, so the three cannot diverge on what counts as a seat.
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import {
  REGISTERED_CUSTOMER_FLY_APPS,
  UNPROVISIONED_CUSTOMERS,
  resolveCustomerFlyApp,
} from '../src/lib/operator/fly-app-registry'

const CUSTOMERS_DIR = resolve('operator/customers')

/** Seat slugs the CI sync script would project: real dirs, `_`-prefixed skipped. */
function shippedSlugs(): string[] {
  if (!existsSync(CUSTOMERS_DIR)) return []
  return readdirSync(CUSTOMERS_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith('_'))
    .map((d) => d.name)
    .filter((slug) => existsSync(join(CUSTOMERS_DIR, slug, 'customer.yaml')))
    .sort()
}

const slugs = shippedSlugs()

describe('fly-app-registry drift guard', () => {
  it('discovers the authored seats', () => {
    expect(slugs.length).toBeGreaterThan(0)
  })

  it.each(slugs)('%s: is registered, or records why it has no Fly app', (slug) => {
    const app = resolveCustomerFlyApp(slug)
    const reason = UNPROVISIONED_CUSTOMERS[slug]

    expect(
      app !== null || typeof reason === 'string',
      `Seat "${slug}" is authored at operator/customers/${slug}/customer.yaml but appears in ` +
        `neither map in src/lib/operator/fly-app-registry.ts.\n\n` +
        `It will fail closed SILENTLY: every runtime surface for this seat renders ` +
        `honest-empty and nothing alerts (ss#2283).\n\n` +
        `Decide one:\n` +
        `  - it has a Fly app  -> add "${slug}": "<app-name>" to CUSTOMER_FLY_APPS ` +
        `(confirm the real name with \`fly apps list\`; do NOT assume hermes-${slug})\n` +
        `  - it has none yet   -> add "${slug}" to UNPROVISIONED_CUSTOMERS with the reason`
    ).toBe(true)
  })

  it('never lists a seat as both registered and unprovisioned', () => {
    const both = Object.keys(UNPROVISIONED_CUSTOMERS).filter(
      (slug) => REGISTERED_CUSTOMER_FLY_APPS[slug] !== undefined
    )
    expect(both, `contradictory entries: ${both.join(', ')}`).toEqual([])
  })

  it('every entry in either map is a seat that still exists', () => {
    // A stale entry outlives the seat it named, and its app name stays
    // targetable — the cross-tenant hazard ADR 0036 built the allow-list for.
    const authored = new Set(slugs)
    const orphans = [
      ...Object.keys(REGISTERED_CUSTOMER_FLY_APPS),
      ...Object.keys(UNPROVISIONED_CUSTOMERS),
    ].filter((slug) => !authored.has(slug))
    expect(
      orphans,
      `registry entries with no operator/customers/<slug>/customer.yaml: ${orphans.join(', ')}`
    ).toEqual([])
  })

  it('maps each registered seat to a distinct Fly app', () => {
    // Two slugs sharing an app name means one seat is addressing another
    // tenant's Machine — the precise failure ADR 0036 refuses to risk.
    const apps = Object.values(REGISTERED_CUSTOMER_FLY_APPS)
    expect(new Set(apps).size, `duplicate Fly app names: ${apps.join(', ')}`).toBe(apps.length)
  })

  it('still refuses an unknown slug (fail-closed default survives)', () => {
    expect(resolveCustomerFlyApp('not-a-customer')).toBeNull()
    expect(resolveCustomerFlyApp('')).toBeNull()
    // A seat authored but deliberately unprovisioned resolves to null too:
    // being listed as a known absence is not authorization to target an app.
    for (const slug of Object.keys(UNPROVISIONED_CUSTOMERS)) {
      expect(resolveCustomerFlyApp(slug)).toBeNull()
    }
  })

  it('does not resolve via the hermes-<slug> convention', () => {
    // ADR 0036 rejects the convention as authority. A convention-derived
    // registry would happily hand back "hermes-<anything>"; the registry must
    // return null for a slug it does not know.
    //
    // The original example here was `pilot-law`, a seat authored 2026-06-05 and
    // never provisioned, retired in full on 2026-08-25. Deliberately replaced
    // with a slug that has never existed and never will, so this assertion
    // cannot be quietly satisfied by a real entry appearing later.
    expect(resolveCustomerFlyApp('never-a-real-seat')).toBeNull()
  })
})
