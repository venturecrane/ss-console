/**
 * Declarative table of the console's legacy 301 redirects, extracted from
 * src/middleware.ts (code review 2026-07-02 §1.3). Each rule is a predicate plus
 * a target builder; the middleware evaluates a rule list in order and issues the
 * first match. Keeping the rules here as data (rather than inline `if` ladders in
 * the request handler) makes the redirect surface auditable in one place and
 * keeps the middleware focused on rewrite + auth.
 *
 * Two lists exist because the rules fire at two different points in the pipeline:
 *
 *   PRE_REWRITE_REDIRECTS  run BEFORE the subdomain rewrite (the rewrite
 *                          terminates the chain, so the product-rename and
 *                          one-Operator redirects must precede it to catch
 *                          subdomain-relative forms).
 *   POST_REWRITE_REDIRECTS run AFTER the rewrite (host canonicalization, legacy
 *                          auth paths, and retired marketing surfaces).
 *
 * IMPORTANT: the SOURCES of every rule are the OLD paths. Do not "modernize" a
 * source path to its target — that turns the rule into a self-redirect loop.
 *
 * Each rule's `location` is returned to `context.redirect(location, status)`
 * verbatim, so the absolute-vs-relative form of each target is preserved exactly
 * as it was inline (absolute URLs where the host or query must be carried;
 * relative paths otherwise).
 */

export interface RedirectContext {
  readonly hostname: string
  readonly pathname: string
  readonly url: URL
}

export interface RedirectRule {
  readonly label: string
  match(ctx: RedirectContext): boolean
  location(ctx: RedirectContext): string
  readonly status: 301
}

export interface RedirectOutcome {
  readonly location: string
  readonly status: 301
}

/** pathname === base, or pathname is a descendant path of base. */
function isPathOrDescendant(pathname: string, base: string): boolean {
  return pathname === base || pathname.startsWith(`${base}/`)
}

/**
 * Product renamed "AI Employee" → "Operator" (ADR 0034). Permanent redirects
 * from the pre-rename `/ai-employee` URLs to `/operator`, covering the marketing,
 * portal, and admin surfaces plus their subdomain-relative forms. All matches use
 * the same first-occurrence path substitution, so they collapse into one rule.
 */
const OPERATOR_RENAME: RedirectRule = {
  label: 'operator-rename',
  match: ({ pathname }) =>
    isPathOrDescendant(pathname, '/ai-employee') ||
    isPathOrDescendant(pathname, '/portal/products/ai-employee') ||
    isPathOrDescendant(pathname, '/products/ai-employee') ||
    isPathOrDescendant(pathname, '/admin/ai-employee'),
  location: ({ pathname }) => pathname.replace('/ai-employee', '/operator'),
  status: 301,
}

/**
 * One Operator (ADR 0100 W6). The self-serve subscription's portal pages moved
 * from `/portal/products/hosted-agent[/intake|/api-key]` into the Operator
 * section at `/portal/products/operator/setup[...]`, and the admin intake queue
 * from `/admin/hosted-agent` to `/admin/operator/intake`. Welcome emails, the
 * checkout thanks page and bookmarks carry the old URLs, so they 301 forever.
 * Runs before the subdomain rewrite to catch the subdomain-relative forms
 * (portal host `/products/hosted-agent`, admin host `/hosted-agent`). Query
 * strings (the `?cs=` status banners) are carried. API routes are unchanged.
 */
const ONE_OPERATOR_MOVES: ReadonlyArray<{ host?: string; from: string; to: string }> = [
  { from: '/portal/products/hosted-agent', to: '/portal/products/operator/setup' },
  { host: 'portal.', from: '/products/hosted-agent', to: '/products/operator/setup' },
  { from: '/admin/hosted-agent', to: '/admin/operator/intake' },
  { host: 'admin.', from: '/hosted-agent', to: '/operator/intake' },
]

function oneOperatorMove({ hostname, pathname }: RedirectContext) {
  return (
    ONE_OPERATOR_MOVES.find(
      (m) => (!m.host || hostname.startsWith(m.host)) && isPathOrDescendant(pathname, m.from)
    ) ?? null
  )
}

const ONE_OPERATOR: RedirectRule = {
  label: 'one-operator',
  match: (ctx) => oneOperatorMove(ctx) !== null,
  location: (ctx) => {
    const move = oneOperatorMove(ctx)!
    return move.to + ctx.pathname.slice(move.from.length) + ctx.url.search
  },
  status: 301,
}

/**
 * Marketing `/admin/*` on the apex host canonicalizes to the admin subdomain, so
 * old bookmarks keep working. Absolute target (host change).
 */
const ADMIN_HOST_CANONICALIZE: RedirectRule = {
  label: 'admin-host-canonicalize',
  match: ({ hostname, pathname }) =>
    hostname === 'smd.services' && (pathname === '/admin' || pathname.startsWith('/admin/')),
  location: ({ url }) => {
    const next = new URL(url)
    next.hostname = 'admin.smd.services'
    return next.toString()
  },
  status: 301,
}

/**
 * Apex `/portal/*` canonicalizes to the portal subdomain, mirroring the admin
 * rule above. Found in the Hosted Agent live dry-run (2026-07-06): a relative
 * portal link on a marketing page kept the buyer on smd.services, serving the
 * portal on the wrong host. Absolute target (host change).
 */
const PORTAL_HOST_CANONICALIZE: RedirectRule = {
  label: 'portal-host-canonicalize',
  match: ({ hostname, pathname }) =>
    hostname === 'smd.services' && (pathname === '/portal' || pathname.startsWith('/portal/')),
  location: ({ url }) => {
    const next = new URL(url)
    next.hostname = 'portal.smd.services'
    return next.toString()
  },
  status: 301,
}

/**
 * Portal IA rebuild (2026-07-07): the engagement-function surfaces folded
 * into the offerings-as-destinations IA. Old paths 301 to their new homes
 * so invoice emails, SignWell return links, and bookmarks keep working.
 * SOURCES are the retired paths; do not "modernize" them.
 */
const PORTAL_IA_PREFIXES: Array<{ from: string; to: string }> = [
  { from: '/portal/quotes', to: '/portal/engagement/proposals' },
  { from: '/portal/invoices', to: '/portal/billing/invoices' },
  { from: '/portal/documents', to: '/portal/engagement/documents' },
]

const PORTAL_IA_REDIRECTS: RedirectRule = {
  label: 'portal-ia-redirects',
  match: ({ pathname }) =>
    PORTAL_IA_PREFIXES.some(({ from }) => isPathOrDescendant(pathname, from)),
  location: ({ pathname, url }) => {
    const hit = PORTAL_IA_PREFIXES.find(({ from }) => isPathOrDescendant(pathname, from))!
    // Bare list paths land on the new destination roots (the lists were
    // absorbed); descendant paths (detail ids) map into the new subtree.
    const next = new URL(url)
    if (pathname === hit.from) {
      next.pathname =
        hit.from === '/portal/quotes'
          ? '/portal/engagement'
          : hit.from === '/portal/invoices'
            ? '/portal/billing'
            : '/portal/engagement/documents'
    } else {
      next.pathname = pathname.replace(hit.from, hit.to)
    }
    return next.toString()
  },
  status: 301,
}

/** Old dual-auth-era paths → the unified Clerk sign-in/up. Query preserved. */
const LEGACY_AUTH_PATHS: Record<string, string> = {
  '/auth/login': '/auth/sign-in',
  '/auth/portal-sign-in': '/auth/sign-in',
  '/auth/portal-sign-up': '/auth/sign-up',
  '/auth/portal-login': '/auth/sign-in',
}

const LEGACY_AUTH: RedirectRule = {
  label: 'legacy-auth-paths',
  match: ({ pathname }) => pathname in LEGACY_AUTH_PATHS,
  location: ({ pathname, url }) => {
    const next = new URL(url)
    next.pathname = LEGACY_AUTH_PATHS[pathname]
    // Preserve query string (status=signed_out, etc.).
    return next.toString()
  },
  status: 301,
}

/** Post-booking thanks page moved onto /get-started. */
const BOOK_THANKS: RedirectRule = {
  label: 'book-thanks',
  match: ({ pathname }) => isPathOrDescendant(pathname, '/book/thanks'),
  location: () => '/get-started?booked=1',
  status: 301,
}

// Retired marketing routes → the surviving surface that absorbed them (marketing
// consolidation 2026-06-29 + earlier lead-magnet retirements). /contact is NOT
// retired (Captain decision 2026-06-30). SOURCES are the retired paths.
const RETIRED_MARKETING_TO_HOME_EXACT = new Set(['/scan', '/consulting', '/ai'])
const RETIRED_MARKETING_TO_HOME_PREFIX = ['/scorecard', '/outside-view', '/consulting/', '/ai/']

const RETIRED_MARKETING_EXACT: RedirectRule = {
  label: 'retired-marketing-exact',
  match: ({ pathname }) => RETIRED_MARKETING_TO_HOME_EXACT.has(pathname),
  location: () => '/',
  status: 301,
}

const RETIRED_MARKETING_PREFIX: RedirectRule = {
  label: 'retired-marketing-prefix',
  match: ({ pathname }) =>
    RETIRED_MARKETING_TO_HOME_PREFIX.some(
      (p) => pathname === p.replace(/\/$/, '') || pathname.startsWith(p)
    ),
  location: () => '/',
  status: 301,
}

const RETIRED_WHY: RedirectRule = {
  label: 'retired-why',
  match: ({ pathname }) => isPathOrDescendant(pathname, '/why'),
  location: () => '/operator#compare',
  status: 301,
}

const RETIRED_GET_STARTED: RedirectRule = {
  label: 'retired-get-started',
  match: ({ pathname, url }) => pathname === '/get-started' && !url.searchParams.has('booked'),
  location: () => '/',
  status: 301,
}

/** Redirects that must run BEFORE the subdomain rewrite terminates the chain. */
export const PRE_REWRITE_REDIRECTS: readonly RedirectRule[] = [OPERATOR_RENAME, ONE_OPERATOR]

/** Redirects that run AFTER the subdomain rewrite. Order is significant. */
export const POST_REWRITE_REDIRECTS: readonly RedirectRule[] = [
  ADMIN_HOST_CANONICALIZE,
  PORTAL_HOST_CANONICALIZE,
  PORTAL_IA_REDIRECTS,
  LEGACY_AUTH,
  BOOK_THANKS,
  RETIRED_MARKETING_EXACT,
  RETIRED_MARKETING_PREFIX,
  RETIRED_WHY,
  RETIRED_GET_STARTED,
]

/** Return the first matching rule's outcome, or null if none match. */
export function firstRedirect(
  rules: readonly RedirectRule[],
  ctx: RedirectContext
): RedirectOutcome | null {
  for (const rule of rules) {
    if (rule.match(ctx)) {
      return { location: rule.location(ctx), status: rule.status }
    }
  }
  return null
}
