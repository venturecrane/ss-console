import type { APIRoute } from 'astro'
import { createOAuthState } from '../../../../lib/db/oauth-states.js'
import { requireAdminBaseUrl } from '../../../../lib/config/app-url.js'

/**
 * GET /api/auth/google/connect
 *
 * Initiates the Google OAuth consent flow. Admin-only (session required).
 * Creates a single-use state nonce in D1, then redirects the browser
 * to Google's OAuth consent screen.
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
  const session = locals.session
  if (!session) {
    return redirect('/auth/login?error=unauthorized', 302)
  }

  const env = locals.runtime.env

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
