import { defineMiddleware } from 'astro:middleware'
import {
  parseSessionToken,
  validateSession,
  renewSession,
  buildSessionCookie,
  buildClearSessionCookie,
} from './lib/auth/session'
import { env } from 'cloudflare:workers'

/**
 * Astro middleware — handles auth for protected routes.
 *
 * Host → path mapping (three custom domains on one Pages project):
 *   admin.smd.services/*   → rewritten to /admin/* (admin console, role=admin)
 *   portal.smd.services/*  → rewritten to /portal/* (client portal,
 *                            role=client | role=prospect per ADR 0002)
 *   smd.services/*         → marketing (public); /admin/* and /auth/login 301
 *                            to admin.smd.services for backwards compat
 *
 * Route protection:
 *   /admin/*       → requires role='admin', redirects to /auth/login
 *   /api/admin/*   → requires role='admin', returns 401 JSON
 *   /portal/*      → requires role IN ('client', 'prospect'), redirects to /auth/portal-login
 *   /api/portal/*  → requires role IN ('client', 'prospect'), returns 401 JSON
 *   /auth/*        → public (login pages)
 *   everything else → public
 *
 * On valid session: renews expiration (sliding window) and attaches session to locals.
 */
export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url
  const hostname = context.url.hostname

  // Subdomain routing: portal.smd.services rewrites to /portal/* paths
  const isPortalSubdomain = hostname.startsWith('portal.')
  if (
    isPortalSubdomain &&
    !pathname.startsWith('/portal') &&
    !pathname.startsWith('/api/portal') &&
    !pathname.startsWith('/auth') &&
    !pathname.startsWith('/api/auth')
  ) {
    // Rewrite the URL to the /portal path prefix
    const portalPath = pathname === '/' ? '/portal' : `/portal${pathname}`
    return context.rewrite(new Request(new URL(portalPath, context.url), context.request))
  }

  // Subdomain routing: admin.smd.services rewrites to /admin/* paths
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

  // Backwards-compat redirects: bare apex admin/login paths → admin subdomain.
  // Strict equality guard — NOT startsWith/endsWith, which would match
  // admin.smd.services and loop. Preserves pathname and query string.
  if (hostname === 'smd.services' && (pathname === '/admin' || pathname.startsWith('/admin/'))) {
    const newUrl = new URL(context.url)
    newUrl.hostname = 'admin.smd.services'
    return context.redirect(newUrl.toString(), 301)
  }
  if (
    hostname === 'smd.services' &&
    (pathname === '/auth/login' || pathname.startsWith('/auth/login'))
  ) {
    // Admin login must happen on admin host so cookies land on the correct origin.
    // Client login at /auth/portal-login is NOT redirected — stays on portal host.
    const newUrl = new URL(context.url)
    newUrl.hostname = 'admin.smd.services'
    return context.redirect(newUrl.toString(), 301)
  }

  // Legacy redirect: /book/thanks → /get-started?booked=1
  // The old post-booking intake form lives at /get-started now.
  // Bookmarks from prior bookings still resolve.
  if (pathname === '/book/thanks' || pathname.startsWith('/book/thanks/')) {
    return context.redirect('/get-started?booked=1', 301)
  }

  // Lead-magnet retirement chain. The legacy lead-magnet surfaces
  // (/scan, /scorecard, /get-started cold-mode, /outside-view) originally
  // fed the Outside View product per ADR 0002. Outside View was retired
  // after public-footprint scraping turned out not to surface anything
  // useful, so the redirects now terminate at the home page rather than
  // dangling visitors on an orphaned product.
  //
  // Important guards:
  //   - /scan exact match only (NOT startsWith). /scan/verify/[token]
  //     is the magic-link landing page from in-flight emails sent before
  //     Outside View was retired; redirecting that path would break those
  //     tokens.
  //   - /scorecard is a startsWith; the form has no descendants today
  //     but any future descendants should also funnel home.
  //   - /get-started has dual-mode behavior: with ?booked=1 it is the
  //     post-booking prep page (still needed for /book/thanks redirect
  //     above), so we redirect ONLY when no ?booked param is present.
  //   - /outside-view is a startsWith; the public marketing form would
  //     otherwise still POST to /api/scan/start and mint orphan prospect
  //     sessions whose post-login destination has been retired.
  //   - These are 301 (permanent). Source files at /scan/index.astro,
  //     /scorecard.astro, /get-started.astro, /outside-view/index.astro
  //     stay on disk as belt-and-suspenders 301 emitters; full route
  //     deletion is tracked in the Outside View infrastructure follow-up.
  if (pathname === '/scan') {
    return context.redirect('/', 301)
  }
  if (pathname === '/scorecard' || pathname.startsWith('/scorecard/')) {
    return context.redirect('/', 301)
  }
  if (pathname === '/get-started' && !context.url.searchParams.has('booked')) {
    return context.redirect('/', 301)
  }
  if (pathname === '/outside-view' || pathname.startsWith('/outside-view/')) {
    return context.redirect('/', 301)
  }

  // Initialize session as null for all routes
  context.locals.session = null

  // Determine route type
  const isAdminRoute = pathname.startsWith('/admin')
  const isAdminApiRoute = pathname.startsWith('/api/admin')
  const isPortalRoute = pathname.startsWith('/portal')
  const isPortalApiRoute = pathname.startsWith('/api/portal')
  const isProtectedRoute = isAdminRoute || isAdminApiRoute || isPortalRoute || isPortalApiRoute

  // Resolve session from cookie only on routes that can actually use it.
  // Marketing pages are prerendered and reading request headers during
  // prerender triggers a build-time warning (#20). Restricting the cookie
  // read to routes that have a session use case (admin/portal surfaces,
  // auth flows, API endpoints including /api/auth/google/connect) eliminates
  // the warning and avoids pointless work on public static paths.
  const isAuthRoute = pathname.startsWith('/auth')
  const isApiRoute = pathname.startsWith('/api/')
  const needsSession = isProtectedRoute || isAuthRoute || isApiRoute

  let token: string | null = null
  if (needsSession) {
    const cookieHeader = context.request.headers.get('cookie')
    token = parseSessionToken(cookieHeader)

    if (token) {
      const sessionData = await validateSession(env.DB, env.SESSIONS, token)
      if (sessionData) {
        context.locals.session = sessionData
        // Renew session (sliding window) — fire and forget
        renewSession(env.DB, env.SESSIONS, token, sessionData).catch(() => {})
      }
    }
  }

  // Protected routes: enforce session + role
  if (isProtectedRoute) {
    if (!context.locals.session) {
      if (isAdminApiRoute || isPortalApiRoute) {
        return new Response(JSON.stringify({ error: 'Unauthorized' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (isPortalRoute) {
        return context.redirect('/auth/portal-login')
      }
      return context.redirect('/auth/login')
    }

    // Role gate: admin routes admit only role='admin'. Portal routes admit
    // role='client' AND role='prospect' (ADR 0002 — Outside View prospects
    // share the portal surface with clients; tab visibility differentiates).
    const sessionRole = context.locals.session.role
    const isAdminAccess = (isAdminRoute || isAdminApiRoute) && sessionRole === 'admin'
    const isPortalAccess =
      (isPortalRoute || isPortalApiRoute) &&
      (sessionRole === 'client' || sessionRole === 'prospect')
    if (!isAdminAccess && !isPortalAccess) {
      if (isAdminApiRoute || isPortalApiRoute) {
        return new Response(JSON.stringify({ error: 'Forbidden' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (isPortalRoute) {
        return context.redirect('/auth/portal-login')
      }
      return context.redirect('/auth/login')
    }
  }

  const response = await next()

  // Session cookie handling on authenticated responses.
  // Invariant: admin cookies only live on admin.*, client cookies only on portal.*.
  // - Refresh (sliding window) when role matches host.
  // - Clear proactively when an admin cookie arrives on the apex — a stale
  //   leftover from pre-migration logins should not linger.
  if (context.locals.session && token) {
    const isPortalHost = hostname.startsWith('portal.')
    const isAdminHost = hostname.startsWith('admin.')
    // Portal sessions cover clients and Outside View prospects (ADR 0002).
    // Both share the portal host; admin sessions remain admin-only.
    const isPortalSession =
      context.locals.session.role === 'client' || context.locals.session.role === 'prospect'
    const isAdminSession = context.locals.session.role === 'admin'
    const hostMatches = (isPortalSession && isPortalHost) || (isAdminSession && isAdminHost)
    if (hostMatches) {
      response.headers.append('Set-Cookie', buildSessionCookie(token, context.locals.session.role))
    } else if (hostname === 'smd.services' && isAdminSession) {
      response.headers.append('Set-Cookie', buildClearSessionCookie())
    }
  }

  return response
})
