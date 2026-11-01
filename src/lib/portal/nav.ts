/**
 * Portal navigation model (portal IA rebuild, 2026-07-07).
 *
 * Pure function from PortalOfferings to the ordered destination list —
 * offerings as destinations, fully composed (Captain decisions 1, 2, 10):
 *
 *   Home        unless a pre-go-live operator-only client (then hidden; they
 *               land on the operator itself — see offerings.preGoLiveLanding)
 *   Engagement  when any engagement or proposal exists
 *   Operator    ONE tab when the client owns any operator, self-serve included
 *               (ADR 0100: one product); the list lives inside
 *   Billing     once a billing relationship exists (invoice history or a live subscription)
 */

import type { PortalOfferings } from './offerings'

export interface PortalNavDestination {
  href: string
  label: string
  /** Escape hatch for narrow mobile cells; unused initially. */
  mobileLabel?: string
  matchPrefix: string
  /** Home matches exactly; everything else matches by prefix. */
  exact?: boolean
}

export function buildPortalNav(offerings: PortalOfferings): PortalNavDestination[] {
  const destinations: PortalNavDestination[] = []
  // Home is hidden for a pre-go-live operator-only client: they land in the
  // operator area and have no hub to return to (Captain, 2026-07-16). Same
  // shape as the Billing gate below — a tab appears only when it has a purpose.
  // (Billing is already absent in this state: hasBillingRelationship is false.)
  if (!offerings.preGoLiveLanding) {
    destinations.push({ href: '/portal', label: 'Home', matchPrefix: '/portal', exact: true })
  }
  if (offerings.engagement.present) {
    destinations.push({
      href: '/portal/engagement',
      label: 'Engagement',
      matchPrefix: '/portal/engagement',
    })
  }
  // ONE "Operator" destination for the product, regardless of how many operators
  // the client owns — the same category-per-tab shape as Engagement/Billing.
  // It opens the operator list (which auto-forwards to the sole operator when
  // there is only one); per-operator navigation lives inside that destination, not
  // across the top (a client with 50 operators must not get 50 tabs).
  // A self-serve subscription is one of the client's Operators (ADR 0100: one
  // product; self-serve is an offering), so it shares this one destination.
  if (offerings.operators.length > 0 || offerings.selfServe) {
    destinations.push({
      href: '/portal/products/operator',
      label: 'Operator',
      matchPrefix: '/portal/products/operator',
    })
  }
  // Billing renders only once a billing relationship EXISTS (invoice history,
  // or a subscription past provisioning). A pre-go-live client easing into the
  // portal has no commercial plane to read (Captain, 2026-07-15).
  if (offerings.hasBillingRelationship) {
    destinations.push({
      href: '/portal/billing',
      label: 'Billing',
      matchPrefix: '/portal/billing',
    })
  }
  return destinations
}

export function isNavDestinationActive(dest: PortalNavDestination, pathname: string): boolean {
  const path = pathname.endsWith('/') && pathname !== '/' ? pathname.slice(0, -1) : pathname
  if (dest.exact) return path === dest.href
  return path === dest.href || path.startsWith(dest.matchPrefix + '/')
}
