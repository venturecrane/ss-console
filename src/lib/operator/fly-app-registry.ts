/**
 * Explicit `customer_id → Fly app` registry — the single source of truth for
 * mapping a customer to its per-customer Fly app.
 *
 * Deliberately NOT a `hermes-${id}` string convention: addressing the wrong app
 * is a cross-tenant action (setting a secret on the wrong Machine, or reading
 * the wrong Machine's runtime), so an unlisted customer is **rejected** rather
 * than guessed (ADR 0036). Graduates to a customer.yaml/D1 lookup as customers
 * are added (ADR 0012). The first entry was customer-zero ("smd") →
 * `hermes-smd`, retired 2026-09-03.
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
 * The structural fix is `tests/fly-app-registry-drift.test.ts`, which fails
 * when an authored seat under `operator/customers/` is in neither map below.
 * Adding a seat directory therefore forces an explicit decision here — register
 * it, or record in {@link UNPROVISIONED_CUSTOMERS} why it has no Fly app.
 */

const CUSTOMER_FLY_APPS: Readonly<Record<string, string>> = Object.freeze({
  // `smd` (customer-zero, `hermes-smd`) was retired 2026-09-03 by Captain
  // directive: a June bring-up test with nothing running on it since 07-13,
  // still billing as a started Machine and still holding the morning
  // audit-chain run once stopped. Fly app, volume, D1 projection, R2 vault and
  // the healthchecks ping are all gone; its customer.yaml stays in git history
  // for when it is stood up again. See tests/customer-slug-pattern.test.ts.
  // Smokeball Operator seats (ADR 0053). pilot-smokeball = our own staging
  // rehearsal rig; ashton-price = the production pilot firm.
  'pilot-smokeball': 'hermes-pilot-smokeball',
  'ashton-price': 'hermes-ashton-price',
  // Added ss#2283. Both are deployed Fly apps (`fly apps list`, 2026-08-11)
  // that this map had never listed. scott = the hosted-agent founding seat
  // (ADR 0067); smd-staging = the permanent pre-production mirror of
  // customer-zero.
  scott: 'hermes-scott',
  'smd-staging': 'hermes-smd-staging',
})

/**
 * Authored seats that deliberately resolve to NO Fly app, each with the reason.
 *
 * This is not a second registry — nothing resolves through it and membership
 * still means "refuse to target any app". It exists so the drift guard can tell
 * a DECIDED absence from a FORGOTTEN one, which is the whole defect in ss#2283.
 *
 * `customer.yaml` cannot answer this question itself: it carries no lifecycle
 * field, deliberately ("a state field is a claim an agent can write, and a
 * claim an agent can write is one that rots" — `operator/customers/<slug>/
 * customer.yaml`), and `seat.kind` does not separate provisioned from not
 * (pilot-law, retired 2026-08-25, was `sandbox` with no app; pilot-smokeball is `proving` and
 * does). So the decision is authored here and reviewed like any other code.
 *
 * @public Drift-guard surface, imported by tests/fly-app-registry-drift.test.ts. No runtime
 * caller, by design: resolution goes through resolveCustomerFlyApp only.
 */
export const UNPROVISIONED_CUSTOMERS: Readonly<Record<string, string>> = Object.freeze({})

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
  return CUSTOMER_FLY_APPS[customerId] ?? null
}
