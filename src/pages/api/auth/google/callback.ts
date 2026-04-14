import type { APIRoute } from 'astro'
import { consumeOAuthState } from '../../../../lib/db/oauth-states.js'
import { upsertIntegration } from '../../../../lib/db/integrations.js'
import { exchangeAuthCode } from '../../../../lib/booking/google-calendar.js'
import { encrypt } from '../../../../lib/booking/encryption.js'
import { requireAdminBaseUrl } from '../../../../lib/config/app-url.js'

/**
 * GET /api/auth/google/callback
 *
 * Google OAuth callback. Receives the authorization code after the user
 * consents. This endpoint:
 *
 * 1. Validates and atomically consumes the state nonce
 * 2. Exchanges the auth code for access + refresh tokens
 * 3. Fetches the user's email from Google userinfo
 * 4. Encrypts the refresh token with BOOKING_ENCRYPTION_KEY
 * 5. Upserts the integration record in D1
 * 6. Redirects back to the admin settings page
 *
 * Admin session is NOT required on this endpoint because the OAuth
 * redirect goes through Google and returns without cookies in some
 * browsers. Security is ensured by the single-use state nonce which
 * was created in an authenticated context.
 */

const USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'

export const GET: APIRoute = async ({ request, locals, redirect }) => {
  const env = locals.runtime.env
  const url = new URL(request.url)

  const error = url.searchParams.get('error')
  if (error) {
    console.error(`[google/callback] OAuth error: ${error}`)
    return redirect(`/admin/settings/google-connect?error=${encodeURIComponent(error)}`, 302)
  }

  const code = url.searchParams.get('code')
  const stateParam = url.searchParams.get('state')

  if (!code || !stateParam) {
    return redirect('/admin/settings/google-connect?error=missing_params', 302)
  }

  // 1. Consume state nonce (atomic, single-use)
  const stateRecord = await consumeOAuthState(env.DB, stateParam)
  if (!stateRecord) {
    return redirect('/admin/settings/google-connect?error=invalid_state', 302)
  }

  // 2. Validate required secrets
  const clientId = env.GOOGLE_CLIENT_ID
  const clientSecret = env.GOOGLE_CLIENT_SECRET
  const encryptionKey = env.BOOKING_ENCRYPTION_KEY

  if (!clientId || !clientSecret || !encryptionKey) {
    console.error('[google/callback] Missing required env vars')
    return redirect('/admin/settings/google-connect?error=config', 302)
  }

  try {
    // Must match the redirect_uri registered with Google AND used by /connect.
    const adminBase = requireAdminBaseUrl(env)
    const redirectUri = `${adminBase}/api/auth/google/callback`

    // 3. Exchange code for tokens
    const tokens = await exchangeAuthCode(clientId, clientSecret, code, redirectUri)

    if (!tokens.refresh_token) {
      console.error(
        '[google/callback] No refresh_token in response — user may need to revoke and reconnect'
      )
      return redirect('/admin/settings/google-connect?error=no_refresh_token', 302)
    }

    // 4. Get user email from Google
    const userinfoRes = await fetch(USERINFO_URL, {
      headers: { Authorization: `Bearer ${tokens.access_token}` },
    })

    if (!userinfoRes.ok) {
      console.error(`[google/callback] userinfo failed: ${userinfoRes.status}`)
      return redirect('/admin/settings/google-connect?error=userinfo_failed', 302)
    }

    const userinfo = (await userinfoRes.json()) as { email: string; id: string }

    // 5. Encrypt refresh token
    const refreshCiphertext = await encrypt(encryptionKey, tokens.refresh_token)

    // 6. Compute access token expiry
    const expiresAt = new Date(Date.now() + tokens.expires_in * 1000).toISOString()

    // 7. Upsert integration
    await upsertIntegration(env.DB, {
      org_id: stateRecord.org_id,
      provider: 'google_calendar',
      account_email: userinfo.email,
      account_id: userinfo.id,
      scopes: tokens.scope ?? '',
      refresh_token_ciphertext: refreshCiphertext,
      access_token: tokens.access_token,
      access_expires_at: expiresAt,
    })

    return redirect('/admin/settings/google-connect?success=connected', 302)
  } catch (err) {
    console.error('[google/callback] Error:', err)
    return redirect('/admin/settings/google-connect?error=exchange_failed', 302)
  }
}
