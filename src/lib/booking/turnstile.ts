/**
 * Cloudflare Turnstile verification.
 *
 * Used by `/api/booking/reserve` to verify the guest is not a bot before
 * accepting a booking. The site key is public (`PUBLIC_TURNSTILE_SITE_KEY`)
 * and embedded in the /book widget; the secret key (`TURNSTILE_SECRET_KEY`)
 * lives in worker secrets and never reaches the client.
 *
 * In dev/test environments where the secret is not set, verification is
 * skipped (returns success). This matches the Resend wrapper's dev pattern
 * and lets local testing proceed without configuring Turnstile.
 */

const VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

export interface TurnstileVerifyResult {
  success: boolean
  error?: string
}

/**
 * Verify a Turnstile token against Cloudflare's siteverify endpoint.
 *
 * @param secret - TURNSTILE_SECRET_KEY from worker env. If undefined/empty,
 *   verification is skipped (dev mode).
 * @param token - The cf-turnstile-response token from the form submission.
 * @param remoteIp - Optional client IP. Pass when available — Turnstile uses
 *   it for risk scoring.
 */
export async function verifyTurnstileToken(
  secret: string | undefined,
  token: string | null | undefined,
  remoteIp?: string | null
): Promise<TurnstileVerifyResult> {
  // Dev mode: no secret configured → skip verification
  if (!secret) {
    return { success: true }
  }

  // Reject missing token (real production environment requires it)
  if (!token) {
    return { success: false, error: 'Missing turnstile token' }
  }

  const body = new URLSearchParams()
  body.set('secret', secret)
  body.set('response', token)
  if (remoteIp) body.set('remoteip', remoteIp)

  let response: Response
  try {
    response = await fetch(VERIFY_URL, { method: 'POST', body })
  } catch (err) {
    return {
      success: false,
      error: `Turnstile request failed: ${err instanceof Error ? err.message : String(err)}`,
    }
  }

  if (!response.ok) {
    return { success: false, error: `Turnstile API ${response.status}` }
  }

  const data = (await response.json()) as { success: boolean; 'error-codes'?: string[] }
  if (!data.success) {
    return {
      success: false,
      error: `Turnstile rejected: ${(data['error-codes'] ?? []).join(', ') || 'unknown'}`,
    }
  }

  return { success: true }
}
