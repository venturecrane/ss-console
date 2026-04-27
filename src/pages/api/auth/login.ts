import type { APIRoute } from 'astro'
import { verifyPassword } from '../../../lib/auth/password'
import { createSession, buildSessionCookie } from '../../../lib/auth/session'
import { ORG_ID } from '../../../lib/constants'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { env } from 'cloudflare:workers'

interface UserRow {
  id: string
  org_id: string
  email: string
  name: string
  role: string
  password_hash: string | null
}

/**
 * POST /api/auth/login
 *
 * Validates email + password credentials, creates a session,
 * sets the session cookie, and redirects to /admin.
 *
 * On failure, redirects to /auth/login with an error query param.
 */
export const POST: APIRoute = async ({ request, redirect }) => {
  try {
    // Host guard: admin login must happen on admin.smd.services so the
    // session cookie lands on the correct origin. A POST to the apex
    // bypasses the middleware redirect (which only catches GET /auth/login),
    // so we enforce the invariant here too.
    const requestHost = new URL(request.url).hostname
    const isLocalDev = requestHost === 'localhost' || requestHost === '127.0.0.1'
    if (!isLocalDev && !requestHost.startsWith('admin.')) {
      return redirect('/auth/login?error=wrong_host', 302)
    }

    // Rate limit: 10 attempts/hour per IP — checked before any DB lookup
    const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
    const rateLimitResult = await rateLimitByIp(env.BOOKING_CACHE, 'auth-login', clientIp, 10)
    if (!rateLimitResult.allowed) {
      return redirect('/auth/login?error=rate_limited', 302)
    }

    const formData = await request.formData()
    const email = formData.get('email')
    const password = formData.get('password')

    if (!email || !password || typeof email !== 'string' || typeof password !== 'string') {
      return redirect('/auth/login?error=missing', 302)
    }

    // Look up the admin within the current app org. Email is not globally
    // unique across organizations.
    const user = await env.DB.prepare(
      `SELECT * FROM users WHERE org_id = ? AND email = ? AND role = 'admin'`
    )
      .bind(ORG_ID, email.toLowerCase().trim())
      .first<UserRow>()

    if (!user || !user.password_hash) {
      return redirect('/auth/login?error=invalid', 302)
    }

    // Verify password
    const valid = await verifyPassword(password, user.password_hash)
    if (!valid) {
      return redirect('/auth/login?error=invalid', 302)
    }

    // Update last_login_at
    await env.DB.prepare(`UPDATE users SET last_login_at = datetime('now') WHERE id = ?`)
      .bind(user.id)
      .run()

    // Create session
    const token = await createSession(env.DB, env.SESSIONS, {
      id: user.id,
      orgId: user.org_id,
      role: user.role,
      email: user.email,
    })

    // Redirect to admin with session cookie
    const cookie = buildSessionCookie(token, user.role)
    return new Response(null, {
      status: 302,
      headers: {
        Location: '/admin',
        'Set-Cookie': cookie,
      },
    })
  } catch {
    return new Response(null, {
      status: 302,
      headers: { Location: '/auth/login?error=server' },
    })
  }
}
