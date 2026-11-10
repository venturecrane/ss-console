/**
 * GENERATED from operator/customers/<slug>/seat.yaml (`ring:`) by
 * scripts/gen-seat-registries.ts. Do not edit. Regenerate with `npm run gen:seat-registries`;
 * tests/seat-inventory.test.ts fails while this file is stale.
 *
 * Data only: the release ring of every provisioned seat, the same membership
 * as operator/fleet/rings.yaml. Ring 0 is staging, the last ring is SMD's own
 * seats, and every ring between is a client ring (src/lib/admin/merge-to-seat.ts).
 */

/** Seat slug -> its release ring. */
export const SEAT_RINGS: Readonly<Record<string, number>> = Object.freeze({
  'ashton-price': 1,
  brokery: 2,
  'captain-mark': 2,
  'pilot-smokeball': 2,
  scott: 2,
  'smd-guide': 2,
  'smd-staging': 0,
})
