import { errorResponse } from './lib/api/helpers'
import { defineMiddleware, sequence } from 'astro:middleware'
import type { APIContext, MiddlewareNext } from 'astro'
import { clerkMiddleware } from '@clerk/astro/server'
import { resolveAdminSessionFromClerk } from './lib/auth/admin-session-shim'
import { parseSessionToken, validateSession, renewSession } from './lib/auth/session'
import { captureError, withSentryRequestHandler } from './lib/observability/sentry'
import { applySecurityHeaders } from './lib/security/response-headers'
import { classifyCrossSite } from './lib/security/cross-site'
import {
  PRE_REWRITE_REDIRECTS,
  POST_REWRITE_REDIRECTS,
  firstRedirect,
} from './lib/routing/legacy-redirects'
import {
  ATTRIBUTION_COOKIE,
  ATTRIBUTION_COOKIE_MAX_AGE_S,
  encodeAttributionCookie,
  parseAttributionFromUrl,
  urlHasAttributionParams,
} from './lib/marketing/attribution'
import { env } from 'cloudflare:workers'

/**
 * Astro middleware — handles auth for protected routes.
 *
 * Host → path mapping (three custom domains on one Worker):
 *   admin.smd.services/*   → rewritten to /admin/* (Clerk auth, role=admin gated)
 *   portal.smd.services/*  → rewritten to /portal/* (Clerk auth)
 *   smd.services/*         → marketing (public) and unified /auth/sign-in;
 *                            /admin/* 301s to admin.smd.services for
 *                            backwards compat. /auth/login 301s to
 *                            /auth/sign-in (handled by legacy-redirect path).
 *
 * Auth model (unified 2026-05-25):
 *   - Clerk owns identity for both admin and portal. clerkMiddleware
 *     (composed before ssMiddleware via sequence()) populates
 *     locals.auth() and locals.currentUser() for downstream handlers.
 *   - On admin paths, resolveAdminSessionFromClerk maps the Clerk user_id
 *     to the local users row (role='admin' gated) and synthesizes the
 *     legacy SessionData shape into locals.session so the 73 existing
 *     call sites (entity queries, OAuth CSRF, email display, etc.) keep
 *     working without per-site refactor. See admin-session-shim.ts.
 *   - On portal paths, Clerk is the primary path. Legacy magic-link
 *     sessions (created by /auth/verify via createSession) are still
 *     accepted as a fallback so client onboarding via invitation email
 *     keeps working. New client onboarding will migrate to Clerk
 *     invitations in a follow-up; the legacy path remains active until
 *     all in-flight invitations have expired.
 */

type NextFn = MiddlewareNext

async function resolveAdminSession(context: APIContext, pathname: string): Promise<void> {
  const isAdminRoute = pathname.startsWith('/admin') || pathname.startsWith('/api/admin')
  if (!isAdminRoute) return

  const auth = context.locals.auth()
  if (!auth.userId) return

  const sessionData = await resolveAdminSessionFromClerk(auth.userId, env.DB, env.SESSIONS)
  if (sessionData) {
    context.locals.session = sessionData
  }
}

/**
 * Resolve legacy magic-link sessions for client portal access. Returns
 * the session token if validated (so callers can renew + extend the
 * cookie sliding-window). Clerk is the primary auth path for portal;
 * this fallback exists only to keep in-flight invitation links working
 * during the Clerk transition.
 */
async function resolveLegacyPortalSession(
  context: APIContext,
  pathname: string
): Promise<string | null> {
  const isPortalRoute = pathname.startsWith('/portal') || pathname.startsWith('/api/portal')
  if (!isPortalRoute) return null

  const cookieHeader = context.request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)
  if (!token) return null

  const sessionData = await validateSession(env.DB, env.SESSIONS, token)
  if (sessionData && sessionData.role === 'client') {
    context.locals.session = sessionData
    // Sliding-window renewal, off the response path: a KV/D1 write failure
    // here must not fail an otherwise-authenticated request. The session stays
    // valid off its existing expiry; the next request retries the renewal.
    //
    // Two things the 2026-09-09 review found on the old one-liner and this
    // fixes. The promise is handed to `waitUntil`, because Workers cancels
    // pending work when the response returns, so a bare fire-and-forget may
    // never run. And the failure is reported, not swallowed: a renewal that
    // fails every time is the kind of quiet degrade captureError exists for.
    const renewal = renewSession(env.DB, env.SESSIONS, token, sessionData).catch((err: unknown) =>
      captureError(err, 'middleware.renew-session')
    )
    context.locals.cfContext?.waitUntil(renewal)
    return token
  }
  return null
}

function handleSubdomainRewrite(
  context: APIContext,
  hostname: string,
  pathname: string
): Promise<Response> | null {
  const isPortalSubdomain = hostname.startsWith('portal.')
  if (
    isPortalSubdomain &&
    !pathname.startsWith('/portal') &&
    !pathname.startsWith('/api/portal') &&
    !pathname.startsWith('/auth') &&
    !pathname.startsWith('/api/auth')
  ) {
    const portalPath = pathname === '/' ? '/portal' : `/portal${pathname}`
    return context.rewrite(new Request(new URL(portalPath, context.url), context.request))
  }

  const isAdminSubdomain = hostname.startsWith('admin.')
  if (
    isAdminSubdomain &&
    !pathname.startsWith('/admin') &&
    !pathname.startsWith('/api/admin') &&
    !pathname.startsWith('/auth') &&
    !pathname.startsWith('/api/auth')
  ) {
    const adminPath = pathname === '/' ? '/admin' : `/admin${pathname}`
    return context.rewrite(new Request(new URL(adminPath, context.url), context.request))
  }

  return null
}

function enforceAdminAuth(context: APIContext, isAdminApiRoute: boolean): Response | null {
  const auth = context.locals.auth()
  if (!auth.userId) {
    return isAdminApiRoute ? errorResponse(401, 'unauthorized') : context.redirect('/auth/sign-in')
  }
  // Clerk user signed in but no admin row in D1 (or role != 'admin').
  // Treat as forbidden — they're authenticated but lack admin clearance.
  if (!context.locals.session || context.locals.session.role !== 'admin') {
    return isAdminApiRoute ? errorResponse(403, 'forbidden') : context.redirect('/portal')
  }
  return null
}

function enforcePortalAuth(context: APIContext, isPortalApiRoute: boolean): Response | null {
  // Clerk is the primary portal auth path. clerkMiddleware (composed
  // before ssMiddleware via sequence()) populates locals.auth() with
  // the current request's Clerk session state. The bridge from Clerk
  // identity to local user/entity runs lazily in getPortalClient()
  // per-route.
  //
  // Legacy magic-link sessions (set by /auth/verify, populated into
  // locals.session by resolveLegacyPortalSession) are accepted as a
  // fallback so in-flight invitation emails continue to work during
  // the Clerk transition.
  const auth = context.locals.auth()
  if (auth.userId) return null
  if (context.locals.session?.role === 'client') return null

  return isPortalApiRoute ? errorResponse(401, 'unauthorized') : context.redirect('/auth/sign-in')
}

function enforceAuth(context: APIContext, pathname: string): Response | null {
  const isAdminRoute = pathname.startsWith('/admin')
  const isAdminApiRoute = pathname.startsWith('/api/admin')
  const isPortalRoute = pathname.startsWith('/portal')
  const isPortalApiRoute = pathname.startsWith('/api/portal')

  if (isAdminRoute || isAdminApiRoute) {
    return enforceAdminAuth(context, isAdminApiRoute)
  }
  if (isPortalRoute || isPortalApiRoute) {
    return enforcePortalAuth(context, isPortalApiRoute)
  }
  return null
}

/**
 * First-touch ad-attribution capture (ADR 0066 launch gate 1, #1722).
 *
 * On marketing hosts only: when a request lands carrying any enumerated ad
 * param (utm_*, gclid, fbclid) and no attribution cookie exists yet, persist
 * the params in a first-party httpOnly cookie. First-touch semantics: an
 * existing cookie is never overwritten. The intake/booking APIs read the
 * cookie server-side and store it on the lead's D1 context row.
 */
function captureAdAttribution(context: APIContext, hostname: string): void {
  if (hostname.startsWith('admin.') || hostname.startsWith('portal.')) return
  if (!urlHasAttributionParams(context.url)) return
  if (context.cookies.has(ATTRIBUTION_COOKIE)) return
  const attribution = parseAttributionFromUrl(context.url)
  if (!attribution) return
  context.cookies.set(ATTRIBUTION_COOKIE, encodeAttributionCookie(attribution), {
    path: '/',
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: ATTRIBUTION_COOKIE_MAX_AGE_S,
  })
}

async function handleRequest(context: APIContext, next: NextFn): Promise<Response> {
  const { pathname } = context.url
  const hostname = context.url.hostname
  const redirectCtx = { hostname, pathname, url: context.url }

  // Legacy redirects that must run BEFORE the subdomain rewrite terminates the
  // chain (the /ai-employee → /operator product rename, ADR 0034). Rule table
  // lives in src/lib/routing/legacy-redirects.ts.
  const preRewrite = firstRedirect(PRE_REWRITE_REDIRECTS, redirectCtx)
  if (preRewrite) return context.redirect(preRewrite.location, preRewrite.status)

  const subdomainRewrite = handleSubdomainRewrite(context, hostname, pathname)
  if (subdomainRewrite) return subdomainRewrite

  // Legacy redirects that run after the rewrite (admin-host canonicalization,
  // legacy auth paths, retired marketing surfaces).
  const postRewrite = firstRedirect(POST_REWRITE_REDIRECTS, redirectCtx)
  if (postRewrite) return context.redirect(postRewrite.location, postRewrite.status)

  captureAdAttribution(context, hostname)

  context.locals.session = null
  await resolveAdminSession(context, pathname)
  await resolveLegacyPortalSession(context, pathname)

  const authDenial = enforceAuth(context, pathname)
  if (authDenial) return authDenial

  // After auth, not before: an unauthenticated cross-site POST should read as
  // the auth failure it is (a redirect or 401), and the CSRF refusal is for the
  // case the cookie DID come along. See src/lib/security/cross-site.ts.
  const crossSite = classifyCrossSite(context.request)
  if (crossSite.crossSite) {
    return errorResponse(403, 'cross_site_request', undefined, { detail: crossSite.reason })
  }

  return await next()
}

// Composed middleware pipeline:
//   1. clerkMiddleware  — parses Clerk session, populates locals.auth()
//                         and locals.currentUser() for downstream handlers.
//                         Does NOT enforce auth on any route.
//   2. ssMiddleware     — SS-owned: subdomain rewrites, legacy redirects,
//                         admin session shim, and auth enforcement
//                         (Clerk for both portal and admin; admin gated
//                         on role='admin' via the shim).
//   3. security headers — set on EVERY response leaving this Worker, including
//      the redirects and 401/403 denials that `handleRequest` returns before
//      ever calling `next()`. Wrapping here rather than inside `handleRequest`
//      is what makes that true: a header applied only around `next()` would
//      miss every early return, which is most of the auth surface.
const ssMiddleware = defineMiddleware(async (context: APIContext, next: NextFn) => {
  const response = await withSentryRequestHandler(context, () => handleRequest(context, next))
  return applySecurityHeaders(response, context.request.url)
})

export const onRequest = sequence(clerkMiddleware(), ssMiddleware)
