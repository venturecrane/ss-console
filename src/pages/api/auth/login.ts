import type { APIRoute } from 'astro'
import { verifyPassword } from '../../../lib/auth/password'
import { createSession, buildSessionCookie } from '../../../lib/auth/session'

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
export const POST: APIRoute = async ({ request, locals, redirect }) => {
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

    const formData = await request.formData()
    const email = formData.get('email')
    const password = formData.get('password')

    if (!email || !password || typeof email !== 'string' || typeof password !== 'string') {
      return redirect('/auth/login?error=missing', 302)
    }

    const env = locals.runtime.env

    // Look up user by email
    const user = await env.DB.prepare(`SELECT * FROM users WHERE email = ? AND role = 'admin'`)
      .bind(email.toLowerCase().trim())
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
