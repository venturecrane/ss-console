/**
 * Portal session helpers.
 *
 * Resolves the entity record for an authenticated portal user.
 * Portal users are linked to entities via users.entity_id. Both 'client'
 * and 'prospect' roles are portal-eligible per ADR 0002 (Outside View).
 */

import type { Entity } from '../db/entities'

interface UserRow {
  id: string
  org_id: string
  email: string
  name: string
  role: string
  entity_id: string | null
}

/**
 * Resolve the entity record for the current portal session.
 *
 * Looks up the user by session.userId AND org_id to get entity_id,
 * then fetches the entity record. The org_id scope prevents a valid
 * portal user ID from one org from resolving against a different org's
 * session.
 *
 * Returns null if the user or entity is not found. The function name
 * `getPortalClient` predates the prospect role and is kept for caller
 * stability — `user.role` carries the discrimination if a caller needs
 * to branch on client-vs-prospect.
 */
export async function getPortalClient(
  db: D1Database,
  userId: string,
  orgId: string
): Promise<{ user: UserRow; client: Entity } | null> {
  const user = await db
    .prepare(`SELECT * FROM users WHERE id = ? AND role IN ('client', 'prospect') AND org_id = ?`)
    .bind(userId, orgId)
    .first<UserRow>()

  if (!user || !user.entity_id) {
    return null
  }

  const client = await db
    .prepare('SELECT * FROM entities WHERE id = ? AND org_id = ?')
    .bind(user.entity_id, orgId)
    .first<Entity>()

  if (!client) {
    return null
  }

  return { user, client }
}
