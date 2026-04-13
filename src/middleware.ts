import { defineMiddleware } from 'astro:middleware'
import { parseSessionToken, validateSession, renewSession } from './lib/auth/session'

/**
 * Astro middleware — handles auth for protected routes.
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

  // Unprotected routes: session is attached if valid, but not required
  if (!isProtectedRoute) {
    return next()
  }

  // Protected routes: enforce session + role
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

  return next()
})
