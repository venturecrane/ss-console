/**
 * Sitemap inclusion filter for @astrojs/sitemap (wired in astro.config.mjs).
 *
 * The integration enumerates every non-dynamic route in src/pages, including
 * the admin console, client portal, auth, dev/design scaffolding, and
 * flag-gated pages that 404 in production. Only the public marketing surface
 * belongs in the sitemap; everything else wastes crawl budget and publishes
 * internal route names.
 *
 * Plain .mjs (not .ts) so both the Astro config loader and vitest import it
 * without a transform step.
 */

/** Path prefixes that must never appear in the public sitemap. */
export const EXCLUDED_PATH_PREFIXES = [
  '/admin',
  '/portal',
  '/auth',
  '/api',
  '/dev',
  '/design-preview',
  '/patterns', // flag-gated, 404 in prod (ENABLE_PUBLIC_PATTERNS)
  '/book/manage', // guest token surface, not a landing page
  '/get-started', // post-booking only; cold visits 301 to /
  '/404',
]

/**
 * @param {string} page Absolute URL as passed by @astrojs/sitemap's filter().
 * @returns {boolean} true when the URL belongs in the sitemap.
 */
export function isPublicMarketingUrl(page) {
  const pathname = new URL(page).pathname
  return !EXCLUDED_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  )
}
