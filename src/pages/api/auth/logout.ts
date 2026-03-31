import type { APIRoute } from 'astro'
import {
  parseSessionToken,
  validateSession,
  destroySession,
  buildClearSessionCookie,
} from '../../../lib/auth/session'

/**
 * POST /api/auth/logout
 *
 * Destroys the current session (D1 + KV), clears the session cookie,
 * and redirects to the appropriate login page based on user role.
 *
 * - Admin users → /auth/login
 * - Client users → /auth/portal-login
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const cookieHeader = request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)

  let redirectTo = '/auth/login'

  if (token) {
    const env = locals.runtime.env

    // Check role before destroying session to determine redirect
    const session = await validateSession(env.DB, env.SESSIONS, token)
    if (session?.role === 'client') {
      redirectTo = '/auth/portal-login'
    }

    await destroySession(env.DB, env.SESSIONS, token)
  }

  const clearCookie = buildClearSessionCookie()
  return new Response(null, {
    status: 302,
    headers: {
      Location: redirectTo,
      'Set-Cookie': clearCookie,
    },
  })
}
