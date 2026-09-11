/**
 * Security response headers for every response this Worker emits.
 *
 * WHY THIS EXISTS. The 2026-08-23 review found ZERO security response headers
 * anywhere in the shipped code, and a live probe confirmed it at the runtime
 * layer — `curl -sSIL https://portal.smd.services/` returned none, while the
 * same command found `content-type` (the positive control that proves the
 * instrument could have returned non-zero). The portal serves signed client
 * documents and session-scoped engagement data, so the gap was on the surface
 * that matters most.
 *
 * WHAT IS DELIBERATELY NOT HERE: a resource-loading CSP. `default-src` /
 * `script-src` / `frame-src` directives shipped blind would break the client's
 * single most important act. `src/pages/portal/engagement/proposals/[id].astro`
 * embeds `app.signwell.com` in an iframe to sign the SOW, and Clerk loads its
 * own script bundle — a CSP authored without enumerating those origins turns
 * the signing page into a blank box. The `frame-ancestors` directive below is
 * the one CSP directive that governs who may frame US rather than what WE may
 * load, so it is safe to ship without that enumeration. A full CSP is a
 * separate, separately-verified piece of work.
 *
 * `Permissions-Policy` is also absent on purpose: the SignWell iframe carries
 * `allow="clipboard-write"`, and a policy that omits clipboard-write would
 * silently degrade signing. Same reasoning — enumerate first, then ship.
 */

/**
 * Headers applied to every response regardless of transport.
 *
 * - `X-Content-Type-Options` stops MIME-sniffing a response into a script.
 * - `Referrer-Policy` keeps portal paths (which carry opaque record ids) out
 *   of the Referer header on cross-origin navigations.
 * - `X-Frame-Options` and the `frame-ancestors` CSP are the same control said
 *   twice: the header for older agents, the directive for current ones. Both
 *   ship because they are not universally interchangeable — `X-Frame-Options`
 *   is ignored by browsers that see `frame-ancestors`, and vice versa on
 *   agents predating CSP 2.
 */
export const BASE_SECURITY_HEADERS: Readonly<Record<string, string>> = Object.freeze({
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "frame-ancestors 'none'",
})

/**
 * HSTS, applied only over HTTPS.
 *
 * A browser ignores this header on a plaintext response anyway, but emitting
 * it in local dev would be a claim the origin cannot honour — `astro dev` on
 * `http://localhost:4321` has no TLS. Gating on the scheme keeps the header
 * meaning exactly what it says wherever it appears.
 *
 * NO `preload`. Preload submission is effectively irreversible for the apex and
 * every subdomain, which makes it the Captain's call rather than a default.
 */
export const HSTS_HEADER = 'Strict-Transport-Security'
export const HSTS_VALUE = 'max-age=31536000; includeSubDomains'

/**
 * Compute the header set for one request.
 *
 * Pure: takes the request URL, returns what should be set. Kept separate from
 * the mutation below so the policy can be asserted without constructing a
 * Response, and so the HTTPS branch is reachable in a unit test.
 */
export function securityHeadersFor(url: string): Record<string, string> {
  const headers: Record<string, string> = { ...BASE_SECURITY_HEADERS }
  let isHttps: boolean
  try {
    isHttps = new URL(url).protocol === 'https:'
  } catch {
    // An unparseable URL is not a reason to drop the base headers. Treat it as
    // non-HTTPS and carry on — failing closed here would mean failing OPEN on
    // the headers, which is the wrong direction.
    isHttps = false
  }
  if (isHttps) headers[HSTS_HEADER] = HSTS_VALUE
  return headers
}

/**
 * Set the security headers on a response, without clobbering a handler that
 * deliberately set its own.
 *
 * A route that has already chosen a value knows something this function does
 * not (an embed page that must be framable, a download that must not be
 * sniffed a particular way), so an existing header wins. Nothing sets one
 * today; the precedence is here so that when something does, it works rather
 * than being silently overwritten.
 *
 * Returns the same Response instance. Astro responses expose a mutable
 * `headers`; when they do not (an immutable Response, e.g. one produced by
 * `Response.redirect`), the response is rebuilt rather than dropped.
 */
export function applySecurityHeaders(response: Response, url: string): Response {
  const wanted = securityHeadersFor(url)

  try {
    for (const [name, value] of Object.entries(wanted)) {
      if (!response.headers.has(name)) response.headers.set(name, value)
    }
    return response
  } catch {
    // Immutable headers (guard: 'immutable'). Rebuild rather than lose them.
    const headers = new Headers(response.headers)
    for (const [name, value] of Object.entries(wanted)) {
      if (!headers.has(name)) headers.set(name, value)
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    })
  }
}
