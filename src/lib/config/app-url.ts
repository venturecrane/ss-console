/**
 * Canonical application URL helpers.
 *
 * All outbound auth and portal links (magic links, invitation emails,
 * follow-up emails, invoice notifications, signature webhook callbacks)
 * MUST be built from `APP_BASE_URL` rather than the inbound request's
 * host/protocol. Trusting the request is unsafe: if the edge does not
 * tightly canonicalize Host/Origin, an attacker can poison generated
 * links by sending a request with a spoofed Host header.
 *
 * Production deploys MUST set `APP_BASE_URL` (e.g. `https://smd.services`).
 * The portal subdomain is served by the same Pages project, so we derive
 * the portal base from `PORTAL_BASE_URL` if set, otherwise we fall back
 * to `APP_BASE_URL`. Both come from environment, never from request.
 *
 * Tracking: GitHub issue #173.
 */

/**
 * Minimal env shape — only the URL config we need. Accepting a structural
 * type keeps callers (Astro endpoints, webhook handlers, Workers) decoupled
 * from the full `CfEnv` declaration in `src/env.d.ts`.
 */
export interface AppUrlEnv {
  APP_BASE_URL?: string
  PORTAL_BASE_URL?: string
  ADMIN_BASE_URL?: string
}

/**
 * Read and normalize `APP_BASE_URL` from environment. Trims whitespace and
 * strips any trailing slash so callers can safely concatenate paths.
 *
 * Returns `null` if the value is missing or blank. Use `requireAppBaseUrl`
 * when you need a hard guarantee.
 */
export function getAppBaseUrl(env: AppUrlEnv): string | null {
  const raw = env.APP_BASE_URL
  if (!raw || typeof raw !== 'string') return null
  const trimmed = raw.trim().replace(/\/+$/, '')
  return trimmed.length > 0 ? trimmed : null
}

/**
 * Read and normalize `PORTAL_BASE_URL`. Falls back to `APP_BASE_URL` when
 * unset, since the portal is served by the same Pages project under a
 * subdomain rewrite (see `src/middleware.ts`).
 */
export function getPortalBaseUrl(env: AppUrlEnv): string | null {
  const raw = env.PORTAL_BASE_URL
  if (raw && typeof raw === 'string') {
    const trimmed = raw.trim().replace(/\/+$/, '')
    if (trimmed.length > 0) return trimmed
  }
  return getAppBaseUrl(env)
}

/**
 * Strict variant of `getAppBaseUrl`. Throws when the canonical URL is not
 * configured. Use this in code paths that send outbound emails or webhook
 * callbacks — failing loudly is preferable to silently emitting links built
 * from a request host.
 */
export function requireAppBaseUrl(env: AppUrlEnv): string {
  const base = getAppBaseUrl(env)
  if (!base) {
    throw new Error(
      'APP_BASE_URL is not configured. Set APP_BASE_URL in wrangler env (e.g. https://smd.services) before sending outbound links.'
    )
  }
  return base
}

/**
 * Strict variant of `getPortalBaseUrl`. Throws when neither `PORTAL_BASE_URL`
 * nor `APP_BASE_URL` is configured.
 */
export function requirePortalBaseUrl(env: AppUrlEnv): string {
  const base = getPortalBaseUrl(env)
  if (!base) {
    throw new Error(
      'PORTAL_BASE_URL (or APP_BASE_URL fallback) is not configured. Set it in wrangler env before sending portal links.'
    )
  }
  return base
}

/**
 * Build an absolute URL on the canonical app origin. Handles leading-slash
 * normalization so callers can pass either `/foo` or `foo`.
 */
export function buildAppUrl(env: AppUrlEnv, path: string): string {
  const base = requireAppBaseUrl(env)
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${base}${normalized}`
}

/**
 * Build an absolute URL on the canonical portal origin. Defaults to the
 * portal root (`/portal`) when no path is supplied.
 */
export function buildPortalUrl(env: AppUrlEnv, path: string = '/portal'): string {
  const base = requirePortalBaseUrl(env)
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${base}${normalized}`
}

/**
 * Read and normalize `ADMIN_BASE_URL`. Unlike the portal helper, does NOT
 * fall back to `APP_BASE_URL`. A silent fallback would emit OAuth redirect
 * URIs on the marketing domain, producing `redirect_uri_mismatch` errors
 * that are hard to diagnose. Callers should use `requireAdminBaseUrl` to
 * get a strict failure when the value is missing.
 */
export function getAdminBaseUrl(env: AppUrlEnv): string | null {
  const raw = env.ADMIN_BASE_URL
  if (!raw || typeof raw !== 'string') return null
  const trimmed = raw.trim().replace(/\/+$/, '')
  return trimmed.length > 0 ? trimmed : null
}

/**
 * Strict variant of `getAdminBaseUrl`. Throws when `ADMIN_BASE_URL` is not
 * configured. Used for OAuth redirect URIs and outbound admin links — fail
 * loudly rather than silently falling back to the marketing domain.
 */
export function requireAdminBaseUrl(env: AppUrlEnv): string {
  const base = getAdminBaseUrl(env)
  if (!base) {
    throw new Error(
      'ADMIN_BASE_URL is not configured. Set ADMIN_BASE_URL in Cloudflare Pages env (e.g. https://admin.smd.services).'
    )
  }
  return base
}

/**
 * Build an absolute URL on the canonical admin origin. Handles leading-slash
 * normalization so callers can pass either `/foo` or `foo`.
 */
export function buildAdminUrl(env: AppUrlEnv, path: string): string {
  const base = requireAdminBaseUrl(env)
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${base}${normalized}`
}
