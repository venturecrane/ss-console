/**
 * Engagement contacts data access layer.
 *
 * Per PRD US-003 (CM-3, CM-4) and Decision Stack: each engagement has
 * a set of contact role assignments — owner, decision_maker, champion —
 * and at most one row may carry is_primary = 1 (the engagement's primary
 * point of contact). The same contact can be assigned multiple roles
 * (OQ-003: owner-as-champion is the common case).
 *
 * Schema note: engagement_contacts has NO `org_id` column. Org scoping
 * is enforced by JOINing through engagements.org_id on every read, and
 * the endpoint layer pre-validates engagement + contact ownership before
 * any write. Same pattern as parking-lot.ts.
 *
 * Single-primary invariant is enforced at the application layer because
 * the schema does not (the migration uses UNIQUE(engagement_id, contact_id,
 * role), not a partial unique on is_primary).
 *
 * All queries are parameterized to prevent SQL injection.
 */

export type EngagementContactRole = 'owner' | 'decision_maker' | 'champion'

export const ENGAGEMENT_CONTACT_ROLES: { value: EngagementContactRole; label: string }[] = [
  { value: 'owner', label: 'Owner' },
  { value: 'decision_maker', label: 'Decision Maker' },
  { value: 'champion', label: 'Champion' },
]

export interface EngagementContact {
  id: string
  engagement_id: string
  contact_id: string
  role: EngagementContactRole
  is_primary: number
  notes: string | null
  created_at: string
}

export interface EngagementContactWithDetails extends EngagementContact {
  contact_name: string
  contact_email: string | null
  contact_phone: string | null
  contact_title: string | null
}

export interface AddEngagementContactData {
  contact_id: string
  role: EngagementContactRole
  is_primary?: boolean
  notes?: string | null
}

/**
 * List engagement contact assignments joined with contact details, ordered
 * by primary first, then role, then contact name. Scoped to the caller's
 * org via JOIN to prevent cross-tenant reads.
 */
export async function listEngagementContacts(
  db: D1Database,
  orgId: string,
  engagementId: string
): Promise<EngagementContactWithDetails[]> {
  const result = await db
    .prepare(
      `SELECT
         ec.id,
         ec.engagement_id,
         ec.contact_id,
         ec.role,
         ec.is_primary,
         ec.notes,
         ec.created_at,
         c.name AS contact_name,
         c.email AS contact_email,
         c.phone AS contact_phone,
         c.title AS contact_title
       FROM engagement_contacts ec
       INNER JOIN engagements e ON e.id = ec.engagement_id
       INNER JOIN contacts c ON c.id = ec.contact_id
       WHERE ec.engagement_id = ? AND e.org_id = ?
       ORDER BY ec.is_primary DESC, ec.role ASC, c.name ASC`
    )
    .bind(engagementId, orgId)
    .all<EngagementContactWithDetails>()
  return result.results
}

/**
 * Get a single engagement contact assignment by id, scoped to the caller's
 * org via JOIN. Returns null (not 403) when the row exists but belongs to
 * a different org, to prevent tenant enumeration.
 */
export async function getEngagementContact(
  db: D1Database,
  orgId: string,
  engagementContactId: string
): Promise<EngagementContact | null> {
  const result = await db
    .prepare(
      `SELECT ec.* FROM engagement_contacts ec
       INNER JOIN engagements e ON e.id = ec.engagement_id
       WHERE ec.id = ? AND e.org_id = ?`
    )
    .bind(engagementContactId, orgId)
    .first<EngagementContact>()
  return result ?? null
}

/**
 * Add a contact-role assignment to an engagement. The caller MUST validate
 * that contact_id belongs to the engagement's entity (not just the org)
 * before calling — the endpoint layer enforces this.
 *
 * If `is_primary` is true, all other rows on the engagement are cleared
 * first (single-primary invariant). The clear + insert run as best-effort
 * sequential calls; D1 lacks multi-statement transactions in the bound
 * client, so a failed insert after a successful clear leaves the prior
 * primary unset. This is acceptable: the operator sees an error and re-adds.
 *
 * UNIQUE(engagement_id, contact_id, role) prevents duplicate role
 * assignments. The schema-level UNIQUE will surface as a thrown D1 error
 * which the endpoint translates to a friendly message.
 */
export async function addEngagementContact(
  db: D1Database,
  engagementId: string,
  data: AddEngagementContactData
): Promise<EngagementContact> {
  const id = crypto.randomUUID()
  const isPrimary = data.is_primary ? 1 : 0

  if (isPrimary === 1) {
    await db
      .prepare(
        `UPDATE engagement_contacts SET is_primary = 0
         WHERE engagement_id = ? AND is_primary = 1`
      )
      .bind(engagementId)
      .run()
  }

  await db
    .prepare(
      `INSERT INTO engagement_contacts (id, engagement_id, contact_id, role, is_primary, notes)
       VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind(id, engagementId, data.contact_id, data.role, isPrimary, data.notes ?? null)
    .run()

  const row = await db
    .prepare('SELECT * FROM engagement_contacts WHERE id = ?')
    .bind(id)
    .first<EngagementContact>()
  if (!row) {
    throw new Error('Failed to retrieve created engagement contact')
  }
  return row
}

/**
 * Mark a single engagement contact row as the primary POC. Clears any other
 * primary on the same engagement first. Returns the updated row, or null if
 * the row is not in the caller's org.
 */
export async function setEngagementContactPrimary(
  db: D1Database,
  orgId: string,
  engagementContactId: string
): Promise<EngagementContact | null> {
  const existing = await getEngagementContact(db, orgId, engagementContactId)
  if (!existing) return null

  await db
    .prepare(
      `UPDATE engagement_contacts SET is_primary = 0
       WHERE engagement_id = ? AND is_primary = 1 AND id <> ?`
    )
    .bind(existing.engagement_id, engagementContactId)
    .run()

  await db
    .prepare('UPDATE engagement_contacts SET is_primary = 1 WHERE id = ?')
    .bind(engagementContactId)
    .run()

  return getEngagementContact(db, orgId, engagementContactId)
}

/**
 * Remove an engagement contact assignment. Returns true on success, false
 * if the row was not in the caller's org.
 */
export async function removeEngagementContact(
  db: D1Database,
  orgId: string,
  engagementContactId: string
): Promise<boolean> {
  const existing = await getEngagementContact(db, orgId, engagementContactId)
  if (!existing) return false

  await db.prepare('DELETE FROM engagement_contacts WHERE id = ?').bind(engagementContactId).run()
  return true
}
