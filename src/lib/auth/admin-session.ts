/**
 * Admin route guard: asserts `locals.session` carries role='admin'.
 *
 * Three modules share the word "session" and are easy to confuse:
 *   - admin-session.ts (this file): the guard. Reads what middleware put
 *     in locals.session. Mints nothing, touches no store.
 *   - admin-session-shim.ts: populates locals.session on admin paths by
 *     adapting Clerk identity into the legacy SessionData shape. This is
 *     the live admin path.
 *   - session.ts: the legacy magic-link D1 + KV session store, retained
 *     only as a portal fallback for in-flight client invitations.
 */
import { resolveAdminSessionFromClerk } from './admin-session-shim'

export interface AdminSession {
  userId: string
  orgId: string
  role: 'admin'
  email: string
  expiresAt: string
}

export type RequireAdminSessionResult =
  { ok: true; session: AdminSession } | { ok: false; response: Response }

export function requireAdminSession(
  locals: Pick<App.Locals, 'session'>
): RequireAdminSessionResult {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return { ok: false, response: adminUnauthorizedResponse() }
  }
  return { ok: true, session: { ...session, role: 'admin' } }
}

/**
 * Resolve the admin session for a route that lives OUTSIDE the `/admin` and
 * `/api/admin` prefixes, where the middleware never populates
 * `locals.session` (it runs the Clerk-to-admin shim only on those paths).
 *
 * `/api/auth/google/connect` is the case that found this (code review
 * 2026-09-10, docs wave): it gated on `locals.session`, which is always null
 * on `/api/auth/*`, so the Google Calendar connect link on the admin settings
 * page bounced every admin to sign-in. A route on such a path resolves the
 * admin identity itself, through the same shim the middleware uses on admin
 * paths, so the gate is the same gate rather than a second, weaker one.
 *
 * Returns the session when `locals.session` already carries an admin (the
 * middleware populated it), else when the Clerk identity in `locals.auth()`
 * maps to a `role = 'admin'` users row; null otherwise.
 */
export async function resolveAdminSessionForRoute(
  locals: Pick<App.Locals, 'session'> & { auth?: () => { userId?: string | null } },
  db: D1Database,
  kv: KVNamespace
): Promise<AdminSession | null> {
  const fromMiddleware = requireAdminSession(locals)
  if (fromMiddleware.ok) return fromMiddleware.session
  const clerkUserId = typeof locals.auth === 'function' ? locals.auth().userId : null
  if (!clerkUserId) return null
  const resolved = await resolveAdminSessionFromClerk(clerkUserId, db, kv)
  return resolved ? { ...resolved, role: 'admin' } : null
}

function adminUnauthorizedResponse(): Response {
  return new Response(JSON.stringify({ error: 'Unauthorized' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  })
}
