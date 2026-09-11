/**
 * Cross-site request predicate for state-changing requests on the signed-in
 * surfaces (admin and portal, pages and APIs).
 *
 * The session cookies here are `SameSite=Lax` (Clerk's and the legacy
 * `session_token`), which is what stops a cross-site form POST from carrying
 * them in current browsers. This check is the second layer: it reads the
 * browser's own statement of where the request came from and refuses a
 * mutation that arrived cross-site even if a cookie did come along
 * (2026-09-09 review, Security LOW 7: "CSRF rests on SameSite=Lax alone").
 *
 * Two signals, in order of reliability:
 *   - `Sec-Fetch-Site` is set by every current browser and cannot be set by
 *     page script. `cross-site` is a refusal; `same-origin`, `same-site`, and
 *     `none` (a top-level navigation the user typed or bookmarked) are not.
 *   - `Origin` is sent on every browser POST. If present it must match the
 *     request's own origin.
 *
 * A request carrying neither header is a non-browser client (curl, a test, a
 * server-to-server call). Those cannot be CSRF vectors, because CSRF is a
 * browser attaching a victim's cookies to an attacker's request; they are
 * allowed through to the auth checks that already gate them.
 */

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

/** Paths whose mutations are cookie-authenticated and therefore CSRF-relevant. */
export function isSignedInSurface(pathname: string): boolean {
  return (
    pathname.startsWith('/admin') ||
    pathname.startsWith('/api/admin') ||
    pathname.startsWith('/portal') ||
    pathname.startsWith('/api/portal')
  )
}

export type CrossSiteVerdict =
  { crossSite: false } | { crossSite: true; reason: 'sec-fetch-site' | 'origin-mismatch' }

/**
 * Decide whether a request is a cross-site mutation of a signed-in surface.
 * Pure: reads method, URL, and two headers.
 */
export function classifyCrossSite(request: Request): CrossSiteVerdict {
  if (SAFE_METHODS.has(request.method.toUpperCase())) return { crossSite: false }
  const pathname = new URL(request.url).pathname
  if (!isSignedInSurface(pathname)) return { crossSite: false }

  const fetchSite = request.headers.get('sec-fetch-site')
  if (fetchSite !== null && fetchSite.toLowerCase() === 'cross-site') {
    return { crossSite: true, reason: 'sec-fetch-site' }
  }

  const origin = request.headers.get('origin')
  if (origin !== null && origin !== 'null') {
    const requestOrigin = new URL(request.url).origin
    if (origin !== requestOrigin) return { crossSite: true, reason: 'origin-mismatch' }
  }
  return { crossSite: false }
}
