import { defineMiddleware } from 'astro:middleware'
import {
  parseSessionToken,
  validateSession,
  renewSession,
  buildSessionCookie,
  buildClearSessionCookie,
} from './lib/auth/session'

/**
 * Astro middleware — handles auth for protected routes.
 *
 * Host → path mapping (three custom domains on one Pages project):
 *   admin.smd.services/*   → rewritten to /admin/* (admin console, role=admin)
 *   portal.smd.services/*  → rewritten to /portal/* (client portal, role=client)
 *   smd.services/*         → marketing (public); /admin/* and /auth/login 301
 *                            to admin.smd.services for backwards compat
 *
 * Route protection:
 *   /admin/*       → requires role='admin', redirects to /auth/login
 *   /api/admin/*   → requires role='admin', returns 401 JSON
 *   /portal/*      → requires role='client', redirects to /auth/portal-login
 *   /api/portal/*  → requires role='client', returns 401 JSON
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

  // Initialize session as null for all routes
  context.locals.session = null

  // Determine route type
  const isAdminRoute = pathname.startsWith('/admin')
  const isAdminApiRoute = pathname.startsWith('/api/admin')
  const isPortalRoute = pathname.startsWith('/portal')
  const isPortalApiRoute = pathname.startsWith('/api/portal')
  const isProtectedRoute = isAdminRoute || isAdminApiRoute || isPortalRoute || isPortalApiRoute

  // Always try to resolve session from cookie (even on unprotected routes)
  // so endpoints like /api/auth/google/connect can read locals.session
  const cookieHeader = context.request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)

  if (token) {
    const env = context.locals.runtime.env
    const sessionData = await validateSession(env.DB, env.SESSIONS, token)
    if (sessionData) {
      context.locals.session = sessionData
      // Renew session (sliding window) — fire and forget
      renewSession(env.DB, env.SESSIONS, token, sessionData).catch(() => {})
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

    const requiredRole = isAdminRoute || isAdminApiRoute ? 'admin' : 'client'
    if (context.locals.session.role !== requiredRole) {
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
    const isClientSession = context.locals.session.role === 'client'
    const isAdminSession = context.locals.session.role === 'admin'
    const hostMatches = (isClientSession && isPortalHost) || (isAdminSession && isAdminHost)
    if (hostMatches) {
      response.headers.append('Set-Cookie', buildSessionCookie(token, context.locals.session.role))
    } else if (hostname === 'smd.services' && isAdminSession) {
      response.headers.append('Set-Cookie', buildClearSessionCookie())
    }
  }

  return response
})
