import type { APIRoute } from 'astro'
import {
  parseSessionToken,
  destroySession,
  buildClearSessionCookie,
} from '../../../lib/auth/session'

/**
 * POST /api/auth/logout
 *
 * Destroys the current session (D1 + KV), clears the session cookie,
 * and redirects to the login page.
 */
export const POST: APIRoute = async ({ request, locals }) => {
  const cookieHeader = request.headers.get('cookie')
  const token = parseSessionToken(cookieHeader)

  if (token) {
    const env = locals.runtime.env
    await destroySession(env.DB, env.SESSIONS, token)
  }

  const clearCookie = buildClearSessionCookie()
  return new Response(null, {
    status: 302,
    headers: {
      Location: '/auth/login',
      'Set-Cookie': clearCookie,
    },
  })
}
