/**
 * Which client a seat's obligations belong to.
 *
 * The register answers "what does SMD owe, and to whom". Its rows are keyed by
 * SEAT (`customer_configs.customer_slug`), and most of the time a seat is a
 * client. Some are not: `pilot-smokeball` is our pilot, `smd-staging` is our
 * staging environment, and `scott` is the Captain's own seat. Rolling their
 * rows up under their own names made the register report "11 open across 3
 * clients" on the day it held work for exactly one. The same holds for a
 * client's SECOND seat: `ashton-price-marketing` is the first Operator
 * client's marketing seat, and its work is owed to that client, not to a
 * new one.
 *
 * The Captain's model, 2026-09-17: every obligation belongs to a client, and
 * SMD Services is itself one of them. So there is no internal-versus-external
 * special case anywhere in the code -- there is a client list, and we are on it.
 *
 * IDENTITY IS THE DEFAULT, and that choice is the whole safety argument. Only
 * seats that roll up somewhere ELSE need an entry here, which means a new client
 * seat needs no change at all: forget this file when onboarding and the client
 * still appears, correctly, under its own name. The failure mode of forgetting
 * runs the other way -- a new internal or second seat shows up as its own
 * client, which is noisy and visible rather than silent. A list of clients
 * would have inverted that, and a missing client is the failure this register
 * exists to prevent.
 *
 * WHY KEYED BY SEAT, NOT DERIVED FROM `customer_configs.entity_id`: the
 * entity is not the client for this purpose. `brokery` is its own client but
 * its config sits on SMD's entity, so an entity rollup would file a client's
 * work under SMD.
 *
 * WHERE THE MAPPING IS AUTHORED: in each seat's own
 * `operator/customers/<slug>/seat.yaml` as `client:` (the seat inventory,
 * scripts/lib/seat-inventory.ts), generated into `seat-clients.generated.mjs`.
 * It used to be a list here, kept by hand against `customer_configs`, and it
 * carried a seat (`ashton-price-marketing`) that existed in no seat directory,
 * no customer_configs row and no obligation row. A seat that rolls up elsewhere
 * now says so where the seat is authored, and a client's second seat is one
 * `client:` line in its seat.yaml.
 *
 * Shared by `scripts/ci-reconcile-obligations.ts` and
 * `.claude/hooks/lib/register.mjs`, both under the repo's pinned tsx. `.mjs`
 * because the CLI once ran under bare node.
 */

import { INVENTORY_SEAT_CLIENTS } from './seat-clients.generated.mjs'

/** The client slug SMD's own seats roll up to. */
export const SMD_CLIENT = 'smd-services'

/** Seat (customer_slug) -> the client its obligations belong to, for every seat that is NOT its own client. */
const SEAT_CLIENT = new Map(INVENTORY_SEAT_CLIENTS)

/**
 * Clients with no seat of their own name. SMD's seats are all named for what
 * they are, so `smd-services` is a client and never a seat.
 */
const CLIENTS_WITHOUT_OWN_SEAT = new Set([SMD_CLIENT])

/**
 * @param {string} seatSlug a `customer_configs.customer_slug`
 * @returns {string} the client slug that seat's obligations belong to
 */
export function clientOf(seatSlug) {
  return SEAT_CLIENT.get(seatSlug) ?? seatSlug
}

/**
 * The inverse: which seats' rows belong to a client.
 *
 * Needed because obligations are STORED by seat and ASKED FOR by client. Without
 * it, `register list --client smd-services` filtered the seat column by a client
 * name, matched nothing, and printed "nothing open" while all three of our seats
 * had work. A clean-looking report over open work is the precise failure this
 * register exists to end, so the asymmetry cannot be left to the call site.
 *
 * A client is its own seat (unless it has none) plus every seat mapped to it,
 * which keeps every real client working with no entry anywhere and rolls a
 * client's second seat up under it.
 *
 * @param {string} clientSlug
 * @returns {string[]} the seat slugs whose rows roll up to that client
 */
export function seatsOf(clientSlug) {
  const seats = []
  if (!CLIENTS_WITHOUT_OWN_SEAT.has(clientSlug)) seats.push(clientSlug)
  for (const [seat, client] of SEAT_CLIENT) {
    if (client === clientSlug) seats.push(seat)
  }
  return seats
}
