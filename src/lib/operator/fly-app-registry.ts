/**
 * Explicit `customer_id → Fly app` registry — the single source of truth for
 * mapping a customer to its per-customer Fly app.
 *
 * Deliberately NOT a `hermes-${id}` string convention: addressing the wrong app
 * is a cross-tenant action (setting a secret on the wrong Machine, or reading
 * the wrong Machine's runtime), so an unlisted customer is **rejected** rather
 * than guessed (ADR 0036). The first entry was customer-zero ("smd") →
 * `hermes-smd`, retired 2026-09-03.
 *
 * The map itself is authored per seat, as `fly_app:` in
 * `operator/customers/<slug>/seat.yaml` (the seat inventory,
 * `scripts/lib/seat-inventory.ts`), and generated into
 * `fly-app-registry.generated.ts`. Every app name is still written by a person
 * and reviewed; the generator copies it, it never derives one from the slug.
 *
 * Both the OAuth token relay (`src/lib/oauth/store.ts`, ADR 0036) and the
 * console→Machine runtime read transport (`runtime-read-transport.ts`, ADR
 * 0043) resolve through here so the two paths can never drift on which app a
 * customer maps to.
 *
 * ## Why the fail-closed default needs a drift guard (ss#2283)
 *
 * Rejecting an unlisted customer is the correct SAFETY behavior and it is not
 * changing. But it is also SILENT: a seat that exists everywhere else and is
 * merely absent from this map does not error anywhere a human looks — the
 * runtime read throws and `readMachineRuntime` collapses it to an honest-empty
 * result. `readLiveOverrides` reports `unconfigured` (it returned a bare null
 * until ss#2314) and the console renders the authored config. "No live data"
 * and "we forgot to add the slug" still look identical from HERE — the status
 * distinguishes a missing transport from a failed read, not a missing transport
 * from a missing registry entry, which is what the drift guard below is for.
 *
 * That is exactly what happened: `scott` (ADR 0067 hosted-agent founding seat)
 * and `smd-staging` (the pre-production mirror of customer-zero) were both
 * deployed Fly apps and both projected into `customer_configs`, and neither was
 * in this map. Every runtime surface for those seats rendered empty for weeks
 * with nothing raised.
 *
 * The structural fix is the seat inventory: a seat is not loadable without a
 * seat.yaml that authors `fly_app:` (an app name, or null with a reason), so
 * adding a seat directory forces the decision here. `tests/seat-inventory.test.ts`
 * fails while the generated map is stale, and
 * `tests/fly-app-registry-drift.test.ts` keeps the fail-closed guarantees.
 */

import {
  CUSTOMER_FLY_APPS,
  UNPROVISIONED_CUSTOMERS as GENERATED_UNPROVISIONED,
} from './fly-app-registry.generated'

/**
 * Authored seats that deliberately resolve to NO Fly app, each with the reason.
 *
 * This is not a second registry: nothing resolves through it and membership
 * still means "refuse to target any app". It exists so the drift guard can tell
 * a DECIDED absence from a FORGOTTEN one, which is the whole defect in ss#2283.
 * A seat lands here by authoring `fly_app: null` plus `unprovisioned_reason` in
 * its seat.yaml.
 *
 * @public Drift-guard surface, imported by tests/fly-app-registry-drift.test.ts. No runtime
 * caller, by design: resolution goes through resolveCustomerFlyApp only.
 */
export const UNPROVISIONED_CUSTOMERS: Readonly<Record<string, string>> = GENERATED_UNPROVISIONED

/**
 * The registry itself, for the drift guard in
 * `tests/fly-app-registry-drift.test.ts`. Runtime callers must go through
 * {@link resolveCustomerFlyApp} — it is the one place the fail-closed default
 * lives.
 *
 * @public Drift-guard surface, imported by tests/fly-app-registry-drift.test.ts. No runtime
 * caller, by design: resolution goes through resolveCustomerFlyApp only.
 */
export const REGISTERED_CUSTOMER_FLY_APPS: Readonly<Record<string, string>> = CUSTOMER_FLY_APPS

/**
 * Resolve a customer_id to its Fly app name, or null when the customer is not
 * in the registry. A null result MUST be treated as "refuse to target any app"
 * — never fall back to a guessed name.
 */
export function resolveCustomerFlyApp(customerId: string): string | null {
  // Own keys only: `toString`, `constructor` and the rest of Object.prototype
  // are not seats, and a bare index would hand back a function for them.
  return Object.hasOwn(CUSTOMER_FLY_APPS, customerId) ? CUSTOMER_FLY_APPS[customerId] : null
}
