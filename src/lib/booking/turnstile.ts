/**
 * Cloudflare Turnstile verification.
 *
 * Used by `/api/booking/reserve` and `/api/intake` to verify the client is
 * not a bot before accepting a submission. The site key is public
 * (`PUBLIC_TURNSTILE_SITE_KEY`) and embedded in the widget; the secret
 * key (`TURNSTILE_SECRET_KEY`) lives in worker secrets and never reaches
 * the client.
 *
 * ## Config invariant (#12)
 *
 * Both keys must be set together, or both must be unset — and "both unset"
 * is only valid on localhost. Production must set both. Previous behavior
 * allowed three dangerous states:
 *   - only secret set → widget never renders, every submission 403s
 *   - only public set → server silently skips verification (BOT BYPASS)
 *   - both unset in prod → server silently skips (BOT BYPASS)
 *
 * `resolveTurnstileConfig(env)` is the single entry point: callers receive
 * either an enabled config (with both keys) or a disabled config (localhost
 * dev only). Misconfiguration throws. The type system forces the check.
 */
const VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

export type TurnstileConfig =
  | { mode: 'disabled'; siteKey: '' }
  | { mode: 'enabled'; siteKey: string; secretKey: string }

export interface TurnstileEnv {
  PUBLIC_TURNSTILE_SITE_KEY?: string
  TURNSTILE_SECRET_KEY?: string
  APP_BASE_URL?: string
}

const LOCALHOST_ORIGIN_RE = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$)/i

/**
 * Resolve Turnstile configuration from env. Throws on misconfiguration.
 *
 * Three valid states:
 *   1. Both keys set → `{ mode: 'enabled' }` (production and dev-with-test-keys)
 *   2. Both keys unset + localhost origin → `{ mode: 'disabled' }` (local dev)
 *   3. Any other combination → throw (fail-fast, no silent bypass)
 *
 * Localhost detection uses `APP_BASE_URL`. If `APP_BASE_URL` is a real prod
 * origin and Turnstile keys are missing, this throws rather than letting
 * bots through silently.
 */
export function resolveTurnstileConfig(env: TurnstileEnv): TurnstileConfig {
  const siteKey = (env.PUBLIC_TURNSTILE_SITE_KEY ?? '').trim()
  const secretKey = (env.TURNSTILE_SECRET_KEY ?? '').trim()
  const appUrl = (env.APP_BASE_URL ?? '').trim()
  const isLocalhost = LOCALHOST_ORIGIN_RE.test(appUrl)

  if (siteKey && secretKey) {
    return { mode: 'enabled', siteKey, secretKey }
  }

  if (!siteKey && !secretKey && isLocalhost) {
    return { mode: 'disabled', siteKey: '' }
  }

  throw new Error(
    `Turnstile misconfigured: PUBLIC_TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY must both be set ` +
      `in non-localhost environments. Got: site_key=${siteKey ? 'set' : 'unset'}, ` +
      `secret_key=${secretKey ? 'set' : 'unset'}, APP_BASE_URL=${appUrl || 'unset'}.`
  )
}

export interface TurnstileVerifyResult {
  success: boolean
  error?: string
}

/**
 * Verify a Turnstile token against Cloudflare's siteverify endpoint.
 *
 * Takes a resolved `TurnstileConfig` — callers MUST call
 * `resolveTurnstileConfig(env)` first so misconfiguration fails fast.
 *
 * @param config - Resolved Turnstile config. `mode: 'disabled'` skips verification.
 * @param token - The cf-turnstile-response token from the form submission.
 * @param remoteIp - Optional client IP. Pass when available — Turnstile uses
 *   it for risk scoring.
 */
export async function verifyTurnstileToken(
  config: TurnstileConfig,
  token: string | null | undefined,
  remoteIp?: string | null
): Promise<TurnstileVerifyResult> {
  if (config.mode === 'disabled') {
    return { success: true }
  }

  if (!token) {
    return { success: false, error: 'Missing turnstile token' }
  }

  const body = new URLSearchParams()
  body.set('secret', config.secretKey)
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
