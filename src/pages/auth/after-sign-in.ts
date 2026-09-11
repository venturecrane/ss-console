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

  if (userRow) {
    // Login accountability: record the sign-in at the true sign-in moment.
    // Covers admin-only and dual-eligible users who may never hit a portal
    // route (entity_id is NULL for admin-only). Never throws.
    const loginEntityId =
      userRow.entity_id ??
      (auth.orgId ? ((await resolveClerkEntity(env.DB, auth.orgId))?.id ?? null) : null)
    await stampLoginIfNewSession(env.DB, userRow, auth.sessionId, loginEntityId)
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

  if (surface === 'admin') {
    return redirect(getAdminBaseUrl(env) ? buildAdminUrl(env, '/') : '/admin', 302)
  }
  if (surface === 'portal') {
    return redirect(getPortalBaseUrl(env) ? buildPortalUrl(env, '/portal') : '/portal', 302)
  }
  return redirect('/auth/sign-in?status=no_subscription', 302)
}
