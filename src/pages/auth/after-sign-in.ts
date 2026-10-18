import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveAdminSessionFromClerk } from '../../lib/auth/admin-session-shim'
import { ensureLocalUser, resolveClerkEntity } from '../../lib/auth/clerk-bridge'
import { stampLoginIfNewSession } from '../../lib/auth/login-events'
import {
  buildAdminUrl,
  buildPortalUrl,
  getAdminBaseUrl,
  getPortalBaseUrl,
} from '../../lib/config/app-url'
import { chooseSignedInSurface, hostnameOf } from '../../lib/auth/after-sign-in-target'
import { clerkProfile } from '../../lib/auth/clerk-profile'
import { honoredReturnPath } from '../../lib/auth/auth-redirect'
import { getCouplesInstanceByEntity } from '../../lib/db/couples-onboarding'
import { COUPLES_LANDING_PATH } from '../../lib/portal/couples-access'

/**
 * Post-sign-in dispatcher.
 *
 * Clerk's <SignIn /> redirects here (relative forceRedirectUrl, so we land on
 * whichever subdomain the user signed in from) after authentication completes.
 * We resolve the authenticated user's eligibility for each surface and forward
 * them appropriately:
 *
 *   - admin-eligible (users.role='admin')                 → admin host
 *   - portal-eligible (bound to a customer entity)         → portal host
 *   - both                                                 → host they signed
 *                                                            in from (then
 *                                                            admin-first default)
 *   - neither                                              → /auth/sign-in?status=no_subscription
 *
 * The dual-eligible case is real: SMD operates its own dogfooded Operator, so
 * the founder is both the admin AND a customer-seat principal. We honor the
 * sign-in host (server-observed, never a user-supplied redirect URL — Clerk's
 * forceRedirectUrl ignores redirect_url params anyway) so being one does not
 * trump reaching the other. See src/lib/auth/after-sign-in-target.ts.
 *
 * We route eligibility through ensureLocalUser FIRST so a pre-Clerk users row
 * (admin seed or legacy magic-link invite) gets auto-linked to the Clerk
 * identity by email on first sign-in — before the admin shim reads it — which
 * also makes admin eligibility resolve correctly on that first login.
 *
 * Landing path within the chosen surface: a `?redirect_url=` passed through
 * from /auth/sign-in is honored only when it is a safe same-origin path under
 * that surface's own prefix (auth-redirect.ts honoredReturnPath), so it can
 * never pick the surface or the host. Without one, a portal user on a couples
 * entity lands on the couples onboarding page: the portal home would show them
 * the single-person Hosted Agent setup card, which is not theirs to complete.
 *
 * When host base URLs are not configured (local dev without
 * ADMIN_BASE_URL/PORTAL_BASE_URL), fall back to relative paths so the
 * subdomain rewrite in src/middleware.ts handles routing on a single host.
 */

export const GET: APIRoute = async ({ locals, redirect, url }) => {
  const auth = locals.auth()
  if (!auth.userId) {
    return redirect('/auth/sign-in', 302)
  }

  // Fetch the Clerk profile so ensureLocalUser can auto-link an existing
  // users row by email (UNIQUE(org_id, email) blocks a duplicate insert) or
  // JIT-create one. Required for both eligibility checks below.
  const clerkUser = await locals.currentUser()
  if (!clerkUser) {
    return redirect('/auth/sign-in', 302)
  }

  // Link/JIT the local row first; the admin shim then sees the linked row.
  // Null when the Clerk user has no local binding AND no verified primary
  // email (clerk-profile.ts) — nothing to key on, so no row is linked or
  // created and both eligibility checks below fail closed to the
  // no_subscription state.
  const userRow = await ensureLocalUser(env.DB, auth.userId, clerkProfile(clerkUser))

  let entityId: string | null = null
  if (userRow) {
    entityId =
      userRow.entity_id ??
      (auth.orgId ? ((await resolveClerkEntity(env.DB, auth.orgId))?.id ?? null) : null)
    // Login accountability: record the sign-in at the true sign-in moment.
    // Covers admin-only and dual-eligible users who may never hit a portal
    // route (entity_id is NULL for admin-only). Never throws.
    await stampLoginIfNewSession(env.DB, userRow, auth.sessionId, entityId)
  }

  const adminSession = await resolveAdminSessionFromClerk(auth.userId, env.DB, env.SESSIONS)

  const adminEligible = adminSession !== null
  // Portal eligibility is binding-based, NOT role-gated: an admin who is also
  // bound to a customer entity is a legitimate portal (Operator) user. Per
  // operator-access.ts, in-product authorization keys off product_roles, not
  // users.role — so we must not exclude an admin from the portal seat here.
  // Requires a local row: without one, an active Clerk org alone must not
  // grant a portal surface (getPortalClient would bounce it anyway).
  const portalEligible = Boolean(userRow && (userRow.entity_id || auth.orgId))

  // Host-aware selection. Fail-safe: any throw falls back to chooseSignedInSurface
  // with unknown hosts, which yields the admin-first default — so a dispatcher
  // bug can never strand the sole admin.
  let surface: 'admin' | 'portal' | 'none'
  try {
    surface = chooseSignedInSurface({
      host: url.hostname,
      adminHost: hostnameOf(getAdminBaseUrl(env)),
      portalHost: hostnameOf(getPortalBaseUrl(env)),
      adminEligible,
      portalEligible,
    })
  } catch {
    surface = chooseSignedInSurface({
      host: '',
      adminHost: null,
      portalHost: null,
      adminEligible,
      portalEligible,
    })
  }

  if (surface === 'none') return redirect('/auth/sign-in?status=no_subscription', 302)
  return redirect(await landingUrl(surface, url.searchParams.get('redirect_url'), entityId), 302)
}

/**
 * Where a signed-in user lands on the chosen surface: an honored redirect_url,
 * else (portal) the couples landing for a couples entity, else the surface home.
 */
async function landingUrl(
  surface: 'admin' | 'portal',
  requestedReturn: string | null,
  entityId: string | null
): Promise<string> {
  if (surface === 'admin') {
    const path = honoredReturnPath(requestedReturn, 'admin')
    if (getAdminBaseUrl(env)) return buildAdminUrl(env, path ?? '/')
    return path ?? '/admin'
  }
  const path =
    honoredReturnPath(requestedReturn, 'portal') ?? (await couplesLandingFor(entityId)) ?? '/portal'
  return getPortalBaseUrl(env) ? buildPortalUrl(env, path) : path
}

/** The couples landing path when the entity is a couples instance, else null. */
async function couplesLandingFor(entityId: string | null): Promise<string | null> {
  if (!entityId) return null
  try {
    return (await getCouplesInstanceByEntity(env.DB, entityId)) ? COUPLES_LANDING_PATH : null
  } catch {
    // A failed lookup must not strand the user: fall back to the portal home.
    return null
  }
}
