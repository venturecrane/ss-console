/**
 * GET /portal/products/operator/oauth/[connector]/callback
 *
 * Customer-facing OAuth callback. Lives on the portal subdomain
 * (`portal.smd.services`) per the resolved decision in
 * `docs/specs/operator/oauth-lifecycle.md` § "Re-consent callback URL":
 * customer consent flows belong on the portal where the authenticated
 * customer is already operating; the admin subdomain stays role-gated
 * for SMD operations only.
 *
 * The admin-side callback at /api/oauth/callback (PR #936) was the v1
 * backstop; it was deleted 2026-09-10 because nothing ever issued a state
 * that returned to it (the only `issueOAuthState` caller is the portal
 * initiator). This route is the one consent surface.
 *
 * Flow:
 *
 *   1. Verify the signed state parameter (HMAC-SHA256, 10-minute TTL).
 *   2. Verify the state's `reviewer_id` matches the currently
 *      authenticated portal user (Clerk session populated by
 *      middleware). A leaked state is useless without the customer's
 *      Clerk cookie.
 *   3. Dispatch to the provider registry for token exchange.
 *   4. Proxy the resulting token to the per-customer Hermes Machine
 *      via the OAuthTokenStore interface (currently a no-op pending
 *      the Hermes Machine relay; see src/lib/oauth/store.ts).
 *   5. Audit-log the outcome (no token material).
 *   6. Redirect the customer back into the Operator settings page
 *      with status=connected or status=failed&reason=<short>.
 *
 * Failure modes redirect with the same short reason vocabulary as the
 * admin-side callback: provider_error, missing_params, bad_state,
 * expired_state, reviewer_mismatch, access_revoked, unknown_provider,
 * unknown_connector, exchange_failed, store_failed.
 *
 * No token material is logged or returned in URLs. Issuer error
 * payloads stay server-side.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'

import { verifyOAuthState, type OAuthStatePayload } from '../../../../../../lib/oauth/state.js'
import {
  getOAuthProvider,
  type ProviderTokenResponse,
} from '../../../../../../lib/oauth/providers.js'
import { getDefaultTokenStore } from '../../../../../../lib/oauth/store.js'
import { emitAuditEvent } from '../../../../../../lib/oauth/audit.js'
import { requirePortalBaseUrl } from '../../../../../../lib/config/app-url.js'
import { resolveOperatorAccess } from '../../../../../../lib/portal/operator-access.js'

type RedirectFn = (path: string, status?: 300 | 301 | 302 | 303 | 304 | 307 | 308) => Response

interface CallbackCtx {
  redirect: RedirectFn
  portalBase: string
  connectorParam: string | null
}

/**
 * The settings page to return to. The instance slug is the state's `customer_id`
 * (bound at authorize time), so a verified callback lands on the RIGHT operator's
 * settings; a pre-verification failure with no known instance falls back to the
 * bare operator root.
 */
function buildResultUrl(
  portalBase: string,
  instanceSlug: string | null,
  params: Record<string, string>
): string {
  const path = instanceSlug
    ? `/portal/products/operator/${instanceSlug}/settings`
    : '/portal/products/operator'
  const url = new URL(`${portalBase}${path}`)
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v)
  return url.toString()
}

async function reject(
  ctx: CallbackCtx,
  reason: string,
  meta: {
    customer_id: string | null
    provider: string | null
    reviewer_id: string | null
    auditReason?: string
  }
): Promise<Response> {
  await emitAuditEvent({
    action: 'token-rejected',
    customer_id: meta.customer_id,
    provider: meta.provider,
    reviewer_id: meta.reviewer_id,
    reason: meta.auditReason ?? reason,
  })
  const params: Record<string, string> = { status: 'failed', reason }
  if (meta.provider) params.provider = meta.provider
  return ctx.redirect(buildResultUrl(ctx.portalBase, meta.customer_id, params), 302)
}

async function handleProviderError(
  ctx: CallbackCtx,
  url: URL,
  providerError: string,
  providerErrorDescription: string | null
): Promise<Response> {
  // Provider rejected consent (customer denied, scope mismatch, tenant
  // admin policy block, etc). Try to read the state for audit context
  // but don't fail the failure path on a missing/invalid state.
  const rawState = url.searchParams.get('state')
  let customer: string | null = null
  let provider: string | null = null
  if (rawState) {
    const result = await verifyOAuthState(rawState)
    if (result.ok) {
      customer = result.payload.customer_id
      provider = result.payload.provider
    }
  }
  const detail = providerErrorDescription ? `:${providerErrorDescription}` : ''
  return reject(ctx, 'provider_error', {
    customer_id: customer,
    provider,
    reviewer_id: null,
    auditReason: `provider_error:${providerError}${detail}`,
  })
}

interface ValidatedState {
  payload: OAuthStatePayload
}

async function validateStateOrReject(
  ctx: CallbackCtx,
  stateParam: string
): Promise<Response | ValidatedState> {
  const stateResult = await verifyOAuthState(stateParam)
  if (stateResult.ok) return { payload: stateResult.payload }
  const reason = stateResult.error === 'expired' ? 'expired_state' : 'bad_state'
  return reject(ctx, reason, {
    customer_id: null,
    provider: null,
    reviewer_id: null,
    auditReason: `${reason}:${stateResult.error}`,
  })
}

async function exchangeOrReject(
  ctx: CallbackCtx,
  payload: OAuthStatePayload,
  code: string
): Promise<Response | ProviderTokenResponse> {
  const provider = getOAuthProvider(payload.provider)
  if (!provider) {
    return reject(ctx, 'unknown_provider', {
      customer_id: payload.customer_id,
      provider: payload.provider,
      reviewer_id: payload.reviewer_id,
    })
  }
  try {
    return await provider.exchange_code({
      code,
      // The redirect_uri sent to the token endpoint MUST match the one
      // used in the authorize step. The Azure AD app registration knows
      // it as the portal subdomain URL.
      redirect_uri: portalCallbackUrl(ctx.portalBase, payload.provider),
    })
  } catch (err) {
    const detail = err instanceof Error ? err.message : 'unknown'
    console.error(`[portal/oauth/callback] exchange_failed provider=${payload.provider}: ${detail}`)
    return reject(ctx, 'exchange_failed', {
      customer_id: payload.customer_id,
      provider: payload.provider,
      reviewer_id: payload.reviewer_id,
    })
  }
}

function portalCallbackUrl(portalBase: string, providerSlug: string): string {
  return `${portalBase}/portal/products/operator/oauth/${encodeURIComponent(providerSlug)}/callback`
}

/**
 * Map a store-failure reason to the user-facing redirect reason. Actionable
 * reasons (e.g. Google returned no refresh_token → revoke + reconnect) surface
 * to the settings page; everything else collapses to a generic store_failed.
 * The full store reason is preserved in the audit trail regardless.
 */
function storeFailureReason(reason: string): string {
  return reason === 'missing_refresh_token' ? 'missing_refresh_token' : 'store_failed'
}

function reviewerMatchesClerk(
  authResult: { userId?: string | null } | null,
  reviewer_id: string
): boolean {
  return Boolean(authResult && authResult.userId && authResult.userId === reviewer_id)
}

/**
 * Two checks on WHO is finishing the consent, in order:
 *
 *   1. The signed state's reviewer_id must equal the Clerk user the middleware
 *      populated on locals. A leaked state is useless without that cookie.
 *   2. That reviewer must STILL be a principal on the instance, re-resolved at
 *      callback time with the gate the initiator used. The state proves who
 *      started the consent, not that they may still finish it; a reviewer
 *      whose role was removed inside the state's TTL must not be able to store
 *      a token against the firm (2026-09-10 review, Security LOW 8).
 *
 * Returns the rejection Response, or null when both checks pass.
 */
async function gateReviewerOrReject(
  ctx: CallbackCtx,
  locals: App.Locals,
  payload: OAuthStatePayload
): Promise<Response | null> {
  const { customer_id, provider: providerSlug, reviewer_id } = payload
  // Portal auth is owned by Clerk -- middleware populated locals.auth().
  // The auth helper returns `{ userId, sessionId, ... }` when signed in.
  let clerkAuth: { userId?: string | null } | null = null
  try {
    const authFn = locals.auth as undefined | (() => { userId?: string | null })
    if (typeof authFn === 'function') clerkAuth = authFn()
  } catch {
    clerkAuth = null
  }
  if (!reviewerMatchesClerk(clerkAuth, reviewer_id)) {
    return reject(ctx, 'reviewer_mismatch', { customer_id, provider: providerSlug, reviewer_id })
  }

  const access = await resolveOperatorAccess(env.DB, locals, {
    allowedRoles: ['principal'],
    customerSlug: customer_id,
  })
  if (access.kind === 'redirect') {
    return reject(ctx, 'access_revoked', {
      customer_id,
      provider: providerSlug,
      reviewer_id,
      auditReason: 'access_revoked:reviewer no longer principal on instance at callback',
    })
  }
  return null
}

export const GET: APIRoute = async ({ request, redirect, locals, params }) => {
  const url = new URL(request.url)
  const ctx: CallbackCtx = {
    redirect,
    portalBase: requirePortalBaseUrl(env),
    connectorParam: typeof params.connector === 'string' ? params.connector : null,
  }

  const providerError = url.searchParams.get('error')
  if (providerError) {
    return handleProviderError(ctx, url, providerError, url.searchParams.get('error_description'))
  }

  const code = url.searchParams.get('code')
  const stateParam = url.searchParams.get('state')
  if (!code || !stateParam) {
    return reject(ctx, 'missing_params', {
      customer_id: null,
      provider: null,
      reviewer_id: null,
    })
  }

  const stateOrFail = await validateStateOrReject(ctx, stateParam)
  if (stateOrFail instanceof Response) return stateOrFail

  const { customer_id, provider: providerSlug, reviewer_id } = stateOrFail.payload

  // The connector path parameter must match the provider stamped in the
  // signed state. Mismatch indicates a tampered or replayed URL.
  if (ctx.connectorParam && ctx.connectorParam !== providerSlug) {
    return reject(ctx, 'unknown_connector', {
      customer_id,
      provider: providerSlug,
      reviewer_id,
      auditReason: `unknown_connector:path=${ctx.connectorParam},state=${providerSlug}`,
    })
  }

  const reviewerDenial = await gateReviewerOrReject(ctx, locals, stateOrFail.payload)
  if (reviewerDenial) return reviewerDenial

  const tokenOrFail = await exchangeOrReject(ctx, stateOrFail.payload, code)
  if (tokenOrFail instanceof Response) return tokenOrFail

  // Proxy the token to the per-customer Hermes Machine. v1 is a no-op
  // until the Machine relay lands (see src/lib/oauth/store.ts), but
  // the audit-log entry still records the storage intent.
  const storeResult = await getDefaultTokenStore().store({
    customer_id,
    provider: providerSlug,
    reviewer_id,
    token: tokenOrFail,
  })

  if (!storeResult.ok) {
    return reject(ctx, storeFailureReason(storeResult.reason), {
      customer_id,
      provider: providerSlug,
      reviewer_id,
      auditReason: `store_failed:${storeResult.reason}`,
    })
  }

  await emitAuditEvent({
    action: 'token-issued',
    customer_id,
    provider: providerSlug,
    reviewer_id,
  })

  return redirect(
    buildResultUrl(ctx.portalBase, customer_id, {
      status: 'connected',
      provider: providerSlug,
    }),
    302
  )
}
