/**
 * Admin navigation model (admin portal rethink, 2026-07-14, ADR 0077).
 *
 * The admin console is the same object graph as the client portal, viewed
 * from the guide's side. Where the client nav is derived per-client from
 * what that client owns (lib/portal/nav.ts), the admin nav is the fixed
 * five-destination spine that aggregates every client up:
 *
 *   Home      the cockpit — what needs the Captain today
 *   Clients   the roster; delivery + operators + billing drill in per-client
 *   Fleet     running products across all clients (Operator + Hosted Agent)
 *   Billing   money across all clients
 *   Playbook  the venture handbook (reference)
 *
 * Settings is NOT a spine word — it is an account affordance in the header.
 * Section anchors are assigned sequentially so there is no conditional-anchor
 * arithmetic, exactly as the portal nav does.
 */

export interface AdminNavDestination {
  href: string
  label: string
  anchor: string
  matchPrefix: string
  /** Home matches exactly; everything else matches by prefix. */
  exact?: boolean
}

export function buildAdminNav(): AdminNavDestination[] {
  const destinations: Omit<AdminNavDestination, 'anchor'>[] = [
    { href: '/admin', label: 'Home', matchPrefix: '/admin', exact: true },
    { href: '/admin/clients', label: 'Clients', matchPrefix: '/admin/clients' },
    // "Fleet" is the operational lens over both running products. It seeds from
    // the existing operator fleet page; folding the Hosted Agent view in is a
    // later surface rebuild (ADR 0077 §3).
    { href: '/admin/operator', label: 'Fleet', matchPrefix: '/admin/operator' },
    { href: '/admin/billing', label: 'Billing', matchPrefix: '/admin/billing' },
    { href: '/admin/playbook', label: 'Playbook', matchPrefix: '/admin/playbook' },
  ]
  return destinations.map((d, i) => ({ ...d, anchor: String(i + 1).padStart(2, '0') }))
}

export function isAdminNavActive(dest: AdminNavDestination, pathname: string): boolean {
  const path = pathname.endsWith('/') && pathname !== '/' ? pathname.slice(0, -1) : pathname
  if (dest.exact) return path === dest.href
  return path === dest.href || path.startsWith(dest.matchPrefix + '/')
}
