/**
 * Operator authority posture — the frozen Layer-1 contract (ADR 0041).
 *
 * "Authority" answers a single question per client, per domain: between SMD
 * and the client org, who may *operate* the controls for this domain? It is
 * orthogonal to entitlements (ADR 0035 — what the operator itself may do) and
 * to client-internal RBAC (which of the client's own people may act). See
 * docs/design/operator/00-foundations.md §2 for the three-layer model.
 *
 * This module is the single source of truth both portals and the validator
 * import. It is deliberately pure — no D1, no validator dependency, no I/O —
 * so it can be imported from anywhere (portal pages, the customer.yaml
 * validator, the projection reader) without pulling a heavier graph.
 *
 * Two invariants this module encodes and never bends:
 *   1. SMD control is a constant, not a posture. Authority governs only the
 *      *client* org's operability. SMD always retains full control regardless
 *      of any value here — that lives in admin RBAC (Layer 0), not in this
 *      data. Nothing in this module ever returns "SMD may not".
 *   2. Launch-safe by construction. The absent / unconfigured posture resolves
 *      to `managed` for every switchable domain (every client self-serve
 *      switch off). Turning a domain client-operable is an explicit authored
 *      change, never a default.
 */

import { isRecord } from '../api/helpers'

/**
 * The per-client preset applied to every switchable domain that has no
 * explicit override.
 *
 * - `managed` — SMD operates every domain (all client switches off). Launch
 *   default and the value an absent `authority` block resolves to.
 * - `self_managed` — every switchable domain is client-operable unless an
 *   override pins it back to `managed`. Not a launch state; reachable only by
 *   authoring it once an operator has settled.
 */
export const ACCEPTED_AUTHORITY_DEFAULTS = ['managed', 'self_managed'] as const
export type AuthorityDefault = (typeof ACCEPTED_AUTHORITY_DEFAULTS)[number]

/**
 * Who holds operability for a single switchable domain once resolved (also the
 * value space for an override).
 *
 * - `managed` — SMD-operated; the client sees the domain read-only with a
 *   "request a change" path (Read + Request mode in the client portal).
 * - `client` — the client org *also* gets operable controls (subject to
 *   client-internal RBAC). Never instead of SMD — strictly additive.
 */
export const ACCEPTED_AUTHORITY_HOLDERS = ['managed', 'client'] as const
export type AuthorityHolder = (typeof ACCEPTED_AUTHORITY_HOLDERS)[number]

/**
 * The closed set of domains a client switch can cover. Maps to the capability
 * domains in foundations §4.2. Two domains are deliberately absent because
 * they are SMD-only always (see {@link SMD_ONLY_AUTHORITY_DOMAINS}):
 * provisioning/lifecycle and cost/economics.
 *
 * Adding or splitting a domain here is a schema + portal commitment (ADR 0041
 * §Consequences) — the list is intentionally conservative.
 */
export const SWITCHABLE_AUTHORITY_DOMAINS = [
  'configuration', // persona/skill/scope/business-hours authoring
  'trust', // entitlement ceilings within authored floors
  'connectors', // connect / reconnect / credential custody (§5)
  'runtime', // whatever controls the authored entitlements expose
  'memory', // review/dismiss observations + agent-authored skills
  'people_access', // users, roles, PTO, voice profiles
  'compliance', // evidence packets, retention posture, holds
  'observability', // health/connector/sticky-stop action subset
] as const
export type SwitchableAuthorityDomain = (typeof SWITCHABLE_AUTHORITY_DOMAINS)[number]

/**
 * Domains that are never a client switch — SMD operates them in every state.
 * Listed (not merely omitted) so the validator can reject them as override
 * keys with a precise error and so read-access logic can reason over the full
 * domain space.
 *
 * - `provisioning` — stand up / pin / resize / pause / decommission.
 * - `cost` — COGS, COGS/MRR, anomalies. The one domain the client never even
 *   *reads* — our cost basis is ours by nature, not by posture.
 */
export const SMD_ONLY_AUTHORITY_DOMAINS = ['provisioning', 'cost'] as const
export type SmdOnlyAuthorityDomain = (typeof SMD_ONLY_AUTHORITY_DOMAINS)[number]

export const ALL_AUTHORITY_DOMAINS = [
  ...SWITCHABLE_AUTHORITY_DOMAINS,
  ...SMD_ONLY_AUTHORITY_DOMAINS,
] as const
export type AuthorityDomain = (typeof ALL_AUTHORITY_DOMAINS)[number]

const SWITCHABLE_SET: ReadonlySet<string> = new Set(SWITCHABLE_AUTHORITY_DOMAINS)

/**
 * The resolved customer authority posture. On a validated `customer.yaml` this
 * is always present (the validator fills the absent block with the launch
 * default). `overrides` is sparse — only domains that deviate from `default`.
 */
export interface AuthorityPosture {
  default: AuthorityDefault
  overrides: Partial<Record<SwitchableAuthorityDomain, AuthorityHolder>>
}

/** The launch posture: SMD operates everything, every client switch off. */
export const DEFAULT_AUTHORITY_POSTURE: AuthorityPosture = {
  default: 'managed',
  overrides: {},
}

export function isSwitchableDomain(domain: string): domain is SwitchableAuthorityDomain {
  return SWITCHABLE_SET.has(domain)
}

/**
 * Resolve who operates a single switchable domain for a client. The base comes
 * from the preset (`self_managed` → `client`, else `managed`); an override, if
 * present, wins. A `null` posture (no row / unconfigured) resolves to the
 * launch default — `managed` for every domain.
 */
export function resolveDomainAuthority(
  posture: AuthorityPosture | null,
  domain: SwitchableAuthorityDomain
): AuthorityHolder {
  if (posture === null) return 'managed'
  const base: AuthorityHolder = posture.default === 'self_managed' ? 'client' : 'managed'
  return posture.overrides[domain] ?? base
}

/** Resolve every switchable domain at once — the shape both portals iterate. */
export function resolveAllDomains(
  posture: AuthorityPosture | null
): Record<SwitchableAuthorityDomain, AuthorityHolder> {
  const out = {} as Record<SwitchableAuthorityDomain, AuthorityHolder>
  for (const domain of SWITCHABLE_AUTHORITY_DOMAINS) {
    out[domain] = resolveDomainAuthority(posture, domain)
  }
  return out
}

/**
 * Is this domain client-operable for this client? `true` → the client portal
 * renders Operable controls (still subject to client-internal RBAC); `false` →
 * Read + Request. SMD-only domains are never client-operable.
 */
export function isClientOperable(
  posture: AuthorityPosture | null,
  domain: AuthorityDomain
): boolean {
  if (!isSwitchableDomain(domain)) return false
  return resolveDomainAuthority(posture, domain) === 'client'
}

/**
 * May the client *read* this domain? Read access is on for every domain from
 * day one (foundations §4.1 principle 3) — the sole exception is `cost`, which
 * is SMD-only by nature. Independent of the operability switch: a `managed`
 * domain is still readable (that is exactly the Read + Request state).
 */
export function canClientRead(domain: AuthorityDomain): boolean {
  return domain !== 'cost'
}

/**
 * Fail-safe parse of a projected `authority_json` value (or any untrusted
 * source) into an {@link AuthorityPosture}. Unlike the customer.yaml validator
 * (which collects authoring errors), this is defensive and total: malformed
 * input, unknown override keys, and unknown values are dropped, never thrown.
 * The strict gate at authoring time is `checkAuthority` in the validator; by
 * the time data reaches the projection it has already passed that gate, so the
 * projection's job is only to never crash a portal render on a corrupt row.
 *
 * A `null`/absent/invalid root resolves to {@link DEFAULT_AUTHORITY_POSTURE}.
 */
export function parseAuthorityPosture(raw: unknown): AuthorityPosture {
  if (!isRecord(raw)) return { ...DEFAULT_AUTHORITY_POSTURE }
  const def: AuthorityDefault = raw['default'] === 'self_managed' ? 'self_managed' : 'managed'
  const overrides: Partial<Record<SwitchableAuthorityDomain, AuthorityHolder>> = {}
  const rawOverrides = raw['overrides']
  if (isRecord(rawOverrides)) {
    for (const [key, value] of Object.entries(rawOverrides)) {
      if (isSwitchableDomain(key) && (value === 'managed' || value === 'client')) {
        overrides[key] = value
      }
    }
  }
  return { default: def, overrides }
}
