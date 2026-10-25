/**
 * GENERATED from operator/customers/<slug>/seat.yaml (`fly_app:`) by
 * scripts/gen-seat-registries.ts. Do not edit. Regenerate with `npm run gen:seat-registries`;
 * tests/seat-inventory.test.ts fails while this file is stale.
 *
 * Data only. The fail-closed resolution and its rationale (ADR 0036, ss#2283)
 * live in fly-app-registry.ts, which is the only module that should import this.
 */

/** Seat slug -> its Fly app, as authored in each seat's seat.yaml. */
export const CUSTOMER_FLY_APPS: Readonly<Record<string, string>> = Object.freeze({
  'ashton-price': 'hermes-ashton-price',
  brokery: 'hermes-brokery',
  'captain-mark': 'hermes-captain-mark',
  'pilot-smokeball': 'hermes-pilot-smokeball',
  scott: 'hermes-scott',
  'smd-staging': 'hermes-smd-staging',
})

/** Seats authored with `fly_app: null`, each with its unprovisioned_reason. */
export const UNPROVISIONED_CUSTOMERS: Readonly<Record<string, string>> = Object.freeze({
  'ashton-price-marketing':
    "The firm has not yet created the seat's mailbox, its two Microsoft Graph app registrations or the WordPress Contributor user; the seat is provisioned the week the firm engages (scope letter 385, 2026-10-15).",
})
