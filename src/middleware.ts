import { defineMiddleware } from 'astro:middleware'
import { parseSessionToken, validateSession, renewSession } from './lib/auth/session'

/**
 * Astro middleware — handles auth for protected routes.
 *
 * Route protection:
 *   /admin/*      → requires role='admin', redirects to /auth/login
 *   /api/admin/*  → requires role='admin', returns 401 JSON
 *   /portal/*     → requires role='client', redirects to /auth/portal-login
 *   /auth/*       → public (login pages)
 *   everything else → public
 *
 * On valid session: renews expiration (sliding window) and attaches session to locals.
 */
export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url

  // Initialize session as null for all routes
  context.locals.session = null

  // Determine route type
  const isAdminRoute = pathname.startsWith('/admin')
  const isAdminApiRoute = pathname.startsWith('/api/admin')
  const isPortalRoute = pathname.startsWith('/portal')
  const isProtectedRoute = isAdminRoute || isAdminApiRoute || isPortalRoute

  if (!isProtectedRoute) {
    return next()
  }

  // Extract session token from cookie
  const cookieHeader = context.request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)

  if (!token) {
    if (isAdminApiRoute) {
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

  // Validate session
  const env = context.locals.runtime.env
  const sessionData = await validateSession(env.DB, env.SESSIONS, token)

  if (!sessionData) {
    if (isAdminApiRoute) {
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

  // Role-based access control
  const requiredRole = isAdminRoute || isAdminApiRoute ? 'admin' : 'client'
  if (sessionData.role !== requiredRole) {
    if (isAdminApiRoute) {
      return new Response(JSON.stringify({ error: 'Forbidden' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    // Wrong role — redirect to appropriate login
    if (isPortalRoute) {
      return context.redirect('/auth/portal-login')
    }
    return context.redirect('/auth/login')
  }

  // Renew session (sliding window) — fire and forget to avoid blocking
  renewSession(env.DB, env.SESSIONS, token, sessionData).catch(() => {
    // Session renewal failure is non-critical — log but don't block
  })

  // Attach session to locals for downstream pages
  context.locals.session = sessionData

  return next()
})
