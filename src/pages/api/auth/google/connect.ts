import type { APIRoute } from 'astro'
import { createOAuthState } from '../../../../lib/db/oauth-states.js'
import { requireAdminBaseUrl } from '../../../../lib/config/app-url.js'
import { resolveAdminSessionForRoute } from '../../../../lib/auth/admin-session'
import { env } from 'cloudflare:workers'

/**
 * GET /api/auth/google/connect
 *
 * Initiates the Google OAuth consent flow. Admin-only. Creates a single-use
 * state nonce in D1, then redirects the browser to Google's OAuth consent
 * screen.
 *
 * The admin identity is resolved HERE, not read from `locals.session`: this
 * path is `/api/auth/*`, which the middleware exempts from the admin rewrite
 * and never runs the admin session shim on, so `locals.session` is always
 * null here. Until 2026-09-10 the route gated on it anyway and bounced every
 * admin who clicked Connect on the settings page back to sign-in
 * (tests/google-connect-route.test.ts pins both directions).
 *
 * Scopes requested:
 * - openid + email (required so the callback can fetch the connecting
 *   user's email from the userinfo endpoint to populate the integration)
 * - calendar.events (create/update/delete events)
 * - calendar.freebusy (slot availability queries)
 */

const GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
const SCOPES = [
  'openid',
  'email',
  'https://www.googleapis.com/auth/calendar.events',
  'https://www.googleapis.com/auth/calendar.freebusy',
].join(' ')

export const GET: APIRoute = async ({ locals, redirect }) => {
  const session = await resolveAdminSessionForRoute(locals, env.DB, env.SESSIONS)
  if (!session) {
    return redirect('/auth/sign-in?error=unauthorized', 302)
  }

  const clientId = env.GOOGLE_CLIENT_ID
  if (!clientId) {
    console.error('[google/connect] GOOGLE_CLIENT_ID not configured')
    return redirect('/admin/settings/google-connect?error=config', 302)
  }

  // OAuth redirect URI must land on admin.smd.services so the callback
  // arrives on the host that holds the admin session cookie.
  const adminBase = requireAdminBaseUrl(env)
  const redirectUri = `${adminBase}/api/auth/google/callback`

  const state = await createOAuthState(env.DB, session.orgId, 'google_calendar', session.userId)

  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: SCOPES,
    state,
    access_type: 'offline',
    prompt: 'consent',
  })

  return redirect(`${GOOGLE_AUTH_URL}?${params.toString()}`, 302)
}
