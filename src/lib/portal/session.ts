/**
 * Portal session helpers.
 *
 * Resolves the local user + entity for an authenticated portal request.
 * Identity is owned by Clerk (see src/lib/auth/clerk-bridge.ts); SS
 * stores shadow rows keyed by clerk_user_id / clerk_org_id. The bridge
 * JIT-creates the local users row (or auto-links a pre-Clerk row by
 * email) on first login. The local entity is NOT JIT-created — a Clerk
 * user with no binding returns `client: null`, and the caller renders
 * the "no portal access yet" state.
 *
 * Entity resolution order (handled in resolveClerkPortalContext):
 *   1. users.entity_id  — direct binding (admin-provisioned single-user)
 *   2. entities.clerk_org_id — via active Clerk Organization (Operator)
 *
 * Magic-link auth on src/lib/auth/session.ts is retained for client
 * invitation acceptance only.
 */

import { resolveClerkPortalContext, type PortalContext } from '../auth/clerk-bridge'
import { clerkProfile } from '../auth/clerk-profile'

export type { PortalContext } from '../auth/clerk-bridge'

/**
 * Resolve the portal context for the current Astro request.
 *
 * Reads Clerk auth state and user profile from `Astro.locals` (populated
 * by `clerkMiddleware()` in src/middleware.ts). Returns:
 *   - null                              — no Clerk session (redirect to sign-in)
 *   - { user, client: null }            — signed in, no entity provisioned yet
 *   - { user, client: <Entity> }        — fully provisioned, render portal
 */
export async function getPortalClient(
  db: D1Database,
  locals: App.Locals
): Promise<PortalContext | null> {
  const auth = locals.auth()
  if (!auth.userId) return null

  const clerkUser = await locals.currentUser()
  if (!clerkUser) return null

  return resolveClerkPortalContext(
    db,
    { userId: auth.userId, orgId: auth.orgId, sessionId: auth.sessionId },
    // Verified-primary-only email extraction (clerk-profile.ts): an
    // unverified address yields email:null and the bridge refuses to
    // auto-link or JIT-create.
    clerkProfile(clerkUser),
    // Fire-and-forget on the Workers request path; awaited when no
    // ExecutionContext is available (local dev, tests).
    { waitUntil: locals.cfContext ? (p) => locals.cfContext!.waitUntil(p) : undefined }
  )
}
