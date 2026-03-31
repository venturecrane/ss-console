import { defineMiddleware } from 'astro:middleware'
import { parseSessionToken, validateSession, renewSession } from './lib/auth/session'

/**
 * Astro middleware — handles auth for /admin/* routes.
 *
 * - Public routes (/, /book, /api/*, /auth/*): pass through, session = null
 * - Admin routes (/admin/*): validate session cookie, redirect to login if invalid
 * - On valid session: renews expiration (sliding window) and attaches session to locals
 */
export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url

  // Initialize session as null for all routes
  context.locals.session = null

  // Only protect /admin/* routes
  const isAdminRoute = pathname.startsWith('/admin')

  if (!isAdminRoute) {
    return next()
  }

  // Extract session token from cookie
  const cookieHeader = context.request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)

  if (!token) {
    return context.redirect('/auth/login')
  }

  // Validate session
  const env = context.locals.runtime.env
  const sessionData = await validateSession(env.DB, env.SESSIONS, token)

  if (!sessionData) {
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
