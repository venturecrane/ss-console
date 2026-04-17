/**
 * Portal session helpers.
 *
 * Resolves the entity record for an authenticated portal user.
 * Portal users are linked to entities via users.entity_id.
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
 * then fetches the entity record. The org_id scope prevents a valid client
 * user ID from one org from resolving against a different org's session.
 *
 * Returns null if the user or entity is not found.
 */
export async function getPortalClient(
  db: D1Database,
  userId: string,
  orgId: string
): Promise<{ user: UserRow; client: Entity } | null> {
  const user = await db
    .prepare(`SELECT * FROM users WHERE id = ? AND role = 'client' AND org_id = ?`)
    .bind(userId, orgId)
    .first<UserRow>()

  if (!user || !user.entity_id) {
    return null
  }

  const client = await db
    .prepare('SELECT * FROM entities WHERE id = ?')
    .bind(user.entity_id)
    .first<Entity>()

  if (!client) {
    return null
  }

  return { user, client }
}
