/**
 * Bridge between Clerk identity (the source of truth for portal users,
 * organizations, memberships, and sessions) and local SS business state
 * (entities, subscriptions, product_roles, engagements, invoices, etc.).
 *
 * Schema bridge columns:
 *   - users.clerk_user_id      (one local user per Clerk user)
 *   - entities.clerk_org_id    (one local entity per Clerk Organization)
 *
 * On every authenticated portal request the middleware reads Clerk's
 * `locals.auth()` state, then calls `resolveClerkPortalContext` to load
 * the matching local rows. If the Clerk user is new to SS we JIT-create
 * a local users row keyed by `clerk_user_id` so downstream code can
 * reference users.id in foreign keys (product_roles, audit log, etc.).
 *
 * The local entity is NOT JIT-created. A Clerk Organization without a
 * matching `entities.clerk_org_id` means the customer hasn't been
 * provisioned in SS yet — the portal renders a "no access yet" state
 * rather than fabricating an entity record.
 */

import { ORG_ID } from '../constants'
import { EMAIL_IDENTITY_PREDICATE, normalizeEmail } from '../identity/email'
import type { Entity } from '../db/entities'
import { stampLoginIfNewSession } from './login-events'

export interface ClerkAuthState {
  userId: string | null | undefined
  orgId: string | null | undefined
  /** Clerk session id; stable within a session, new on each real sign-in. */
  sessionId?: string | null | undefined
}

export interface PortalUserRow {
  id: string
  org_id: string
  email: string
  name: string
  role: string
  entity_id: string | null
  clerk_user_id: string | null
  /** Skip cache for login detection (0098). Truth is the unique index on portal_login_events. */
  last_clerk_session_id: string | null
}

export interface ClerkUserProfile {
  /**
   * Clerk's VERIFIED PRIMARY email, or null when Clerk holds no verified
   * primary address (see src/lib/auth/clerk-profile.ts). Null disables
   * email-based auto-linking and JIT creation in ensureLocalUser — an
   * unverified address must never bind a Clerk identity to a local row.
   */
  email: string | null
  name: string
}

export interface PortalContext {
  user: PortalUserRow
  client: Entity | null
}

/**
 * Look up the local users row keyed by clerk_user_id, JIT-creating one
 * if absent. Caller must provide the Clerk profile (email + name) for
 * the JIT path — typically fetched from `clerkClient.users.getUser()`
 * inside the page or API handler.
 *
 * role is hardcoded to 'client' for portal users. Admin role is never
 * granted here: since the 2026-05-25 auth unification Clerk is primary
 * for admin too, and `resolveAdminSessionFromClerk` (admin-session-shim.ts)
 * maps a Clerk user_id to an existing local users row gated on
 * role='admin'. That path never JIT-creates a row, so admin cannot be
 * self-provisioned through this bridge. Magic-link auth survives only as
 * a legacy portal fallback for in-flight client invitations.
 *
 * Returns null when the Clerk user has no existing binding AND no trusted
 * (verified primary) email: with nothing to key on, auto-linking would
 * trust an unverified address and JIT creation would fabricate an
 * identity. Callers treat null as "no portal access".
 */
export async function ensureLocalUser(
  db: D1Database,
  clerkUserId: string,
  profile: ClerkUserProfile
): Promise<PortalUserRow | null> {
  const existing = await db
    .prepare('SELECT * FROM users WHERE clerk_user_id = ?')
    .bind(clerkUserId)
    .first<PortalUserRow>()

  if (existing) return existing

  // No trusted email → no email-based linking, no JIT creation. An
  // already-bound user resolved above regardless; a new-to-SS Clerk user
  // without a verified primary address gets the explicit "no access"
  // state, never a fabricated or mis-linked row.
  if (!profile.email) return null

  // Auto-link path: a users row may already exist for this email from
  // before the Clerk migration (or from an admin-created client invite
  // that hasn't been redeemed via Clerk yet). UNIQUE(org_id, email) means
  // we cannot INSERT a duplicate row; we must bind the existing row to
  // this Clerk identity instead. Only links when clerk_user_id IS NULL
  // so we never overwrite an existing binding. Email comes from Clerk's
  // verified primary email, which is the trust anchor — ENFORCED by the
  // null-guard above + clerk-profile.ts (unverified addresses never get here).
  //
  // Matched through the shared identity normalization (src/lib/identity/email.ts):
  // trimmed and lowercased on the input side, `lower()` on the column side.
  // The `users.email` column carries no
  // COLLATE NOCASE, so a seeded row's casing had to match Clerk's byte for
  // byte or the link silently missed — the JIT INSERT below would then
  // succeed (UNIQUE(org_id,email) is case-sensitive too), stranding the
  // person on a second, entity-less row: signed in, "not connected to a
  // customer", locked out of a seat that was waiting for them. Nobody
  // controls which casing an IdP returns (Microsoft/Google OAuth echo the
  // directory's own), and email addresses are treated case-insensitively in
  // practice, so the lookup must be too.
  const emailMatch = await db
    .prepare(
      `SELECT * FROM users
       WHERE org_id = ? AND ${EMAIL_IDENTITY_PREDICATE} AND clerk_user_id IS NULL
       LIMIT 1`
    )
    .bind(ORG_ID, normalizeEmail(profile.email))
    .first<PortalUserRow>()

  if (emailMatch) {
    await db
      .prepare('UPDATE users SET clerk_user_id = ? WHERE id = ?')
      .bind(clerkUserId, emailMatch.id)
      .run()
    return { ...emailMatch, clerk_user_id: clerkUserId }
  }

  // Stored VERBATIM, not normalized. This is Clerk's verified primary address
  // — the trust anchor — and it is what the admin console displays. The lookup
  // above folds case; the record keeps the IdP's spelling. See the second rule
  // in src/lib/identity/email.ts for why those are deliberately separate.
  const id = crypto.randomUUID()
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id, created_at)
       VALUES (?, ?, ?, ?, 'client', ?, datetime('now'))`
    )
    .bind(id, ORG_ID, profile.email, profile.name, clerkUserId)
    .run()

  const created = await db
    .prepare('SELECT * FROM users WHERE id = ?')
    .bind(id)
    .first<PortalUserRow>()

  if (!created) {
    throw new Error(`Failed to load just-created user ${id} for clerk_user_id ${clerkUserId}`)
  }
  return created
}

/**
 * Resolve the active Clerk Organization to a local entities row via
 * entities.clerk_org_id. Returns null when the Clerk org has not been
 * provisioned in SS (no entity bound to it yet).
 *
 * Unlike users, entities are NOT JIT-created. A new Clerk Organization
 * without a matching SS entity reflects a customer who signed up or
 * was invited but hasn't been provisioned yet. The portal renders an
 * explicit "no access" state per docs/style/empty-state-pattern.md
 * (no fabrication).
 */
export async function resolveClerkEntity(
  db: D1Database,
  clerkOrgId: string
): Promise<Entity | null> {
  return await db
    .prepare(`SELECT * FROM entities WHERE clerk_org_id = ? AND org_id = ?`)
    .bind(clerkOrgId, ORG_ID)
    .first<Entity>()
}

/**
 * Resolve a local entity from a users.entity_id reference. Used when the
 * client is bound to an entity directly (without an intermediating Clerk
 * Organization). The Operator product flows still rely on Clerk Orgs
 * for invitation + member management, so resolveClerkEntity(orgId) stays
 * the fallback when entity_id is null.
 */
async function resolveEntityByUserBinding(
  db: D1Database,
  entityId: string
): Promise<Entity | null> {
  return await db
    .prepare(`SELECT * FROM entities WHERE id = ? AND org_id = ?`)
    .bind(entityId, ORG_ID)
    .first<Entity>()
}

/**
 * Resolve the full portal context for an authenticated Clerk session.
 * Returns `null` when there is no Clerk session (caller should redirect
 * to sign-in). Returns `{ user, client: null }` when the user is
 * authenticated but isn't yet bound to a customer (no users.entity_id,
 * and no matching Clerk Organization → entities.clerk_org_id either).
 *
 * Resolution order:
 *   1. users.entity_id (explicit binding, set by admin tools)
 *   2. entities.clerk_org_id (Clerk Organization binding, used by
 *      Operator invitation flows)
 *
 * The two paths coexist because Operator features still need Clerk
 * Organizations for multi-user matter access; direct binding via
 * entity_id is the simpler path for single-user portal access.
 *
 * Also returns null when ensureLocalUser cannot establish a local row
 * (unbound Clerk user with no verified primary email) — the caller's
 * sign-in redirect converges on the no_subscription state via
 * /auth/after-sign-in.
 */
export async function resolveClerkPortalContext(
  db: D1Database,
  auth: ClerkAuthState,
  profile: ClerkUserProfile,
  opts?: { waitUntil?: (p: Promise<unknown>) => void }
): Promise<PortalContext | null> {
  if (!auth.userId) return null

  const user = await ensureLocalUser(db, auth.userId, profile)
  if (!user) return null

  let client: Entity | null = null
  if (user.entity_id) {
    client = await resolveEntityByUserBinding(db, user.entity_id)
  }
  if (!client && auth.orgId) {
    client = await resolveClerkEntity(db, auth.orgId)
  }

  // Login accountability: stamp last_login_at + append sign-in history when
  // this Clerk session has never been seen. After entity resolution so the
  // event carries the entity scope. Never throws.
  await stampLoginIfNewSession(db, user, auth.sessionId, client?.id ?? null, opts?.waitUntil)

  return { user, client }
}
