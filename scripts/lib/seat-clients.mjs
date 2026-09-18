/**
 * Which client a seat's obligations belong to.
 *
 * The register answers "what does SMD owe, and to whom". Its rows are keyed by
 * SEAT (`customer_configs.customer_slug`), and most of the time a seat is a
 * client. Three of ours are not: `pilot-smokeball` is our pilot, `smd-staging`
 * is our staging environment, and `scott` is the Captain's own seat. Rolling
 * their rows up under their own names made the register report "11 open across
 * 3 clients" on the day it held work for exactly one.
 *
 * The Captain's model, 2026-09-17: every obligation belongs to a client, and
 * SMD Services is itself one of them. So there is no internal-versus-external
 * special case anywhere in the code -- there is a client list, and we are on it.
 *
 * IDENTITY IS THE DEFAULT, and that choice is the whole safety argument. Only
 * seats that roll up somewhere ELSE need an entry here, which means a new client
 * seat needs no change at all: forget this file when onboarding and the client
 * still appears, correctly, under its own name. The failure mode of forgetting
 * runs the other way -- a new INTERNAL seat shows up as its own client, which is
 * noisy and visible rather than silent. A list of clients would have inverted
 * that, and a missing client is the failure this register exists to prevent.
 *
 * Shared by `scripts/ci-reconcile-obligations.ts` (tsx) and
 * `.claude/hooks/lib/register.mjs` (bare node), hence `.mjs`.
 */

/** Seats SMD owns. Everything not listed here is its own client. */
const SMD_OWNED_SEATS = new Set(['pilot-smokeball', 'smd-staging', 'scott'])

/** The client slug SMD's own seats roll up to. */
export const SMD_CLIENT = 'smd-services'

/**
 * @param {string} seatSlug a `customer_configs.customer_slug`
 * @returns {string} the client slug that seat's obligations belong to
 */
export function clientOf(seatSlug) {
  return SMD_OWNED_SEATS.has(seatSlug) ? SMD_CLIENT : seatSlug
}
