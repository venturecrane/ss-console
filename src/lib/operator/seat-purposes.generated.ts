/**
 * GENERATED from operator/customers/<slug>/seat.yaml (`purpose:`, `twin_of:`) by
 * scripts/gen-seat-registries.ts. Do not edit. Regenerate with `npm run gen:seat-registries`;
 * tests/seat-inventory.test.ts fails while this file is stale.
 *
 * Data only: what every seat is for (ADR 0107). Read through this module, never
 * by slug: a client seat is `purpose === 'client'`, a release twin names its
 * client in `twinOf`, and `provisioned` is false for a seat with no Fly app.
 */

export type SeatPurpose = 'client' | 'release-twin' | 'demo' | 'internal'

export interface SeatPurposeEntry {
  purpose: SeatPurpose
  twinOf: string | null
  provisioned: boolean
}

/** Seat slug -> what it is for. */
export const SEAT_PURPOSES: Readonly<Record<string, SeatPurposeEntry>> = Object.freeze({
  'ap-staging': { purpose: 'release-twin', twinOf: 'ashton-price', provisioned: true },
  'ashton-price': { purpose: 'client', twinOf: null, provisioned: true },
  'ashton-price-marketing': { purpose: 'client', twinOf: null, provisioned: false },
  brokery: { purpose: 'client', twinOf: null, provisioned: true },
  'captain-mark': { purpose: 'client', twinOf: null, provisioned: true },
  'pilot-smokeball': { purpose: 'demo', twinOf: null, provisioned: true },
  'smd-guide': { purpose: 'internal', twinOf: null, provisioned: true },
  'smd-office': { purpose: 'internal', twinOf: null, provisioned: false },
})
