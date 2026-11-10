// GENERATED from operator/customers/<slug>/seat.yaml (`purpose:`, `twin_of:`) by
// scripts/gen-seat-registries.ts. Do not edit. Regenerate with `npm run gen:seat-registries`;
// tests/seat-inventory.test.ts fails while this file is stale.
// Data only, for the hooks and scripts that cannot import TypeScript
// (.claude/hooks/plan-premise-gate.mjs). TypeScript reads
// src/lib/operator/seat-purposes.generated.ts; Python reads operator/fleet/seats.yaml.

/** [seat, purpose, twinOf or null] for every seat (ADR 0107). */
export const INVENTORY_SEAT_PURPOSES = Object.freeze([
  ['ap-staging', 'internal', null],
  ['ashton-price', 'client', null],
  ['ashton-price-marketing', 'client', null],
  ['brokery', 'client', null],
  ['captain-mark', 'client', null],
  ['pilot-smokeball', 'demo', null],
  ['scott', 'internal', null],
  ['smd-guide', 'internal', null],
  ['smd-staging', 'release-twin', 'ashton-price'],
])
