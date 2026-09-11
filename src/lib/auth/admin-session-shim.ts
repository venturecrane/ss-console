/**
 * Admin session shim — Clerk identity → legacy SessionData shape.
 *
 * Sibling modules (all three say "session"; only this one is the live
 * admin path): `admin-session.ts` is the requireAdminSession guard that
 * reads the locals.session this shim populates; `session.ts` is the
 * legacy magic-link D1 + KV store, now a portal-only fallback.
 *
 * Background. Admin auth used to be custom PBKDF2 + a D1 `sessions` table.
 * 73 call sites across src/ read `locals.session.{userId, orgId, role, email}`
 * — entity queries, OAuth callback CSRF checks, follow-up tenant scoping,
 * email display in the admin chrome. Switching admin to Clerk means we
 * either rewrite 73 call sites or build an adapter. We chose the adapter.
 *
 * This shim resolves a Clerk user_id to the local `users` row and returns
 * the same `SessionData` shape the call sites already read. The middleware
 * populates `locals.session` from this helper on admin paths.
 *
 * Cache. Per-Clerk-user lookups go through KV with a 120s TTL. Role changes
 * propagate within 2 minutes via natural TTL expiry; explicit invalidation
 * is not needed for the single-admin venture posture (one admin, role
 * doesn't churn), but the TTL bounds drift.
 *
 * Failure modes. Returns null when no matching admin row exists — caller
 * (middleware) redirects to /auth/sign-in. Returns null when the row exists
 * but `role !== 'admin'` — same redirect path, prevents privilege confusion.
 */

import type { SessionData } from './session'

const ADMIN_SESSION_CACHE_TTL_SECONDS = 120

interface AdminUserRow {
  id: string
  org_id: string
  email: string
  role: string
}

/**
 * Parse a KV-cached admin session into SessionData, or null when corrupt
 * (unparseable or wrong shape). The shim only ever caches role 'admin';
 * anything else in the cache is treated as corrupt.
 */
function parseCachedAdminSession(cached: string): SessionData | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(cached)
  } catch {
    return null
  }
  if (parsed === null || typeof parsed !== 'object') return null
  const candidate = parsed as Record<string, unknown>
  if (
    typeof candidate.userId !== 'string' ||
    typeof candidate.orgId !== 'string' ||
    typeof candidate.email !== 'string' ||
    typeof candidate.expiresAt !== 'string' ||
    candidate.role !== 'admin'
  ) {
    return null
  }
  return candidate as unknown as SessionData
}

/**
 * Resolve a Clerk user_id to the legacy SessionData shape, gated to
 * `role === 'admin'`. Reads from KV first; falls back to D1; repopulates KV.
 */
export async function resolveAdminSessionFromClerk(
  clerkUserId: string,
  db: D1Database,
  kv: KVNamespace
): Promise<SessionData | null> {
  const cacheKey = `admin-session:${clerkUserId}`

  const cached = await kv.get(cacheKey)
  if (cached) {
    // Issue #834: a corrupt KV entry must fall through to the D1 path
    // (which repopulates the cache), never 500 an authenticated request.
    const parsed = parseCachedAdminSession(cached)
    if (parsed) return parsed
    await kv.delete(cacheKey)
  }

  const row = await db
    .prepare(
      `SELECT id, org_id, email, role
         FROM users
        WHERE clerk_user_id = ? AND role = 'admin'
        LIMIT 1`
    )
    .bind(clerkUserId)
    .first<AdminUserRow>()

  if (!row) return null
  if (row.role !== 'admin') return null

  // expiresAt is part of the SessionData contract for legacy session.ts
  // consumers (renewSession), but the shim is stateless — Clerk owns
  // expiration. Synthesize a forward-dated value matching the cache TTL
  // so any consumer that inspects it sees a "valid" window.
  const expiresAt = new Date(Date.now() + ADMIN_SESSION_CACHE_TTL_SECONDS * 1000).toISOString()

  const sessionData: SessionData = {
    userId: row.id,
    orgId: row.org_id,
    role: 'admin',
    email: row.email,
    expiresAt,
  }

  await kv.put(cacheKey, JSON.stringify(sessionData), {
    expirationTtl: ADMIN_SESSION_CACHE_TTL_SECONDS,
  })

  return sessionData
}

// An explicit invalidateAdminSessionCache(clerkUserId, kv) helper existed
// here but had zero production call sites — no admin route mutates a user's
// role or email today (resend-invitation only touches role='client' rows,
// which never enter this cache). Deleted 2026-08-14 (code review); the 120s
// TTL above IS the invalidation story. If a role/email mutation surface is
// ever built, reintroduce the helper (a kv.delete of the admin-session:<id>
// key) and call it from that mutation path.
