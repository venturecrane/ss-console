/**
 * Client-internal RBAC — the Layer-2 capability matrix (foundations §2,
 * client-portal §2). Answers one question: among a client's OWN people, which
 * roles may *operate* a given authority domain.
 *
 * This is one of the three independent layers that compose into a client
 * action (foundations §2). It is NOT authority (Layer 1, who-turns-the-dial:
 * SMD vs client org — src/lib/operator/authority.ts) and NOT entitlements
 * (Layer 3, what-the-operator-does — ADR 0035). A client action is permitted
 * iff Layer 1 grants the client org the domain AND this layer grants the
 * acting user's role the domain. The dual-mode wrapper composes the two via
 * resolveDomainSurfaceMode (domain-surface.ts); this module supplies the
 * Layer-2 half (`clientRolePermits`).
 *
 * Re-derived from the role definitions in client-portal §2 — deliberately NOT
 * inherited from the legacy dashboard-roles.md permission matrix (foundations
 * §8 marks that artifact "do not adopt"). The role vocabulary is
 * `principal | staff | compliance` (foundations §2; the `staff` value replaced
 * the legacy `operator` to stop colliding with the product name).
 *
 * Pure — no I/O, no D1. The matrix is operability only; read access is governed
 * separately by canClientRead (authority.ts) and is on for every domain except
 * cost. A `true` cell means "this role MAY operate this domain when the Layer-1
 * switch is also on"; at launch every switch is off, so every surface is
 * Read + Request regardless of role, but the matrix is the correct contract for
 * when SMD flips a switch.
 */

import {
  isSwitchableDomain,
  SWITCHABLE_AUTHORITY_DOMAINS,
  type SwitchableAuthorityDomain,
} from '../../operator/authority'

/** The client-internal roles (client-portal §2). Mirrors ACCEPTED_USER_ROLES
 * in the customer.yaml validator; kept as a local const so this module stays
 * pure of the validator graph. */
export const CLIENT_ROLES = ['principal', 'staff', 'compliance'] as const
export type ClientRole = (typeof CLIENT_ROLES)[number]

export function isClientRole(value: string): value is ClientRole {
  return (CLIENT_ROLES as readonly string[]).includes(value)
}

/**
 * Which client-internal roles may operate each switchable domain. The cell
 * rationale, grounded in client-portal §2 (role definitions), §4.2 (domain
 * controls), and §5 (per-surface notes):
 *
 *   - principal — "control the operator within authority; manage the team; set
 *     ceilings within floors; be the escalation target." Operates every
 *     switchable domain.
 *   - staff — "handle work the operator routes to a human; manage matters; see
 *     activity." Operates `runtime` (§5.3: the routed work is acted on by
 *     "principal or staff, per role") and the limited `observability` actions
 *     (§4.2: "Read for all; limited actions (ack, pause) gated by Layer 2").
 *     Configuration, governance, connectors, memory, people, and compliance are
 *     not the day-to-day role's to operate.
 *   - compliance — "read-only audit + evidence access; separation of duties"
 *     (§2). The one action this role performs is exporting an evidence packet
 *     (§5.5), which is the operable half of the `compliance` domain. Everything
 *     else is read-only by definition of the role.
 *
 * Absent (false) by construction: a role with no `true` cell for a domain
 * cannot operate it even when the authority switch is on. Fail-closed.
 */
const OPERABILITY: Readonly<Record<ClientRole, ReadonlySet<SwitchableAuthorityDomain>>> = {
  principal: new Set<SwitchableAuthorityDomain>(SWITCHABLE_AUTHORITY_DOMAINS),
  staff: new Set<SwitchableAuthorityDomain>(['runtime', 'observability']),
  compliance: new Set<SwitchableAuthorityDomain>(['compliance']),
}

/**
 * Does any of the acting user's client-internal roles permit operating this
 * domain? This is the Layer-2 input to resolveDomainSurfaceMode. Unknown role
 * strings (not in CLIENT_ROLES) and unknown domains contribute nothing — the
 * function is total and fail-closed: a malformed role never grants operability.
 *
 * A user may hold multiple roles (the grant model is additive); operability is
 * the union — permitted if ANY held role permits the domain.
 */
export function clientRolePermits(roles: readonly string[], domain: string): boolean {
  if (!isSwitchableDomain(domain)) return false
  for (const role of roles) {
    if (isClientRole(role) && OPERABILITY[role].has(domain)) return true
  }
  return false
}
