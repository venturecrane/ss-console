/**
 * Contact data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

export interface Contact {
  id: string
  org_id: string
  client_id: string
  name: string
  email: string | null
  phone: string | null
  title: string | null
  notes: string | null
  created_at: string
}

export interface CreateContactData {
  name: string
  email?: string | null
  phone?: string | null
  title?: string | null
  notes?: string | null
}

export interface UpdateContactData {
  name?: string
  email?: string | null
  phone?: string | null
  title?: string | null
  notes?: string | null
}

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
  role: string
  is_primary: number
  notes: string | null
  created_at: string
  /** Joined from contacts table */
  contact_name?: string
  contact_email?: string | null
  contact_phone?: string | null
  contact_title?: string | null
}

/**
 * List contacts for a client, scoped to an organization.
 */
export async function listContacts(
  db: D1Database,
  orgId: string,
  clientId: string
): Promise<Contact[]> {
  const result = await db
    .prepare('SELECT * FROM contacts WHERE org_id = ? AND client_id = ? ORDER BY name ASC')
    .bind(orgId, clientId)
    .all<Contact>()
  return result.results
}

/**
 * Get a single contact by ID, scoped to an organization.
 */
export async function getContact(
  db: D1Database,
  orgId: string,
  contactId: string
): Promise<Contact | null> {
  const result = await db
    .prepare('SELECT * FROM contacts WHERE id = ? AND org_id = ?')
    .bind(contactId, orgId)
    .first<Contact>()

  return result ?? null
}

/**
 * Create a new contact linked to a client. Returns the created contact record.
 */
export async function createContact(
  db: D1Database,
  orgId: string,
  clientId: string,
  data: CreateContactData
): Promise<Contact> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO contacts (id, org_id, client_id, name, email, phone, title, notes)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      clientId,
      data.name,
      data.email ?? null,
      data.phone ?? null,
      data.title ?? null,
      data.notes ?? null
    )
    .run()

  const contact = await getContact(db, orgId, id)
  if (!contact) {
    throw new Error('Failed to retrieve created contact')
  }
  return contact
}

/**
 * Update an existing contact. Returns the updated contact record.
 */
export async function updateContact(
  db: D1Database,
  orgId: string,
  contactId: string,
  data: UpdateContactData
): Promise<Contact | null> {
  const existing = await getContact(db, orgId, contactId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | null)[] = []

  if (data.name !== undefined) {
    fields.push('name = ?')
    params.push(data.name)
  }

  if (data.email !== undefined) {
    fields.push('email = ?')
    params.push(data.email)
  }

  if (data.phone !== undefined) {
    fields.push('phone = ?')
    params.push(data.phone)
  }

  if (data.title !== undefined) {
    fields.push('title = ?')
    params.push(data.title)
  }

  if (data.notes !== undefined) {
    fields.push('notes = ?')
    params.push(data.notes)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE contacts SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(contactId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getContact(db, orgId, contactId)
}

/**
 * Delete a contact. Returns true if the contact was found and deleted.
 *
 * Hard delete — contacts table has no soft-delete column.
 * Also removes any engagement_contacts rows referencing this contact.
 */
export async function deleteContact(
  db: D1Database,
  orgId: string,
  contactId: string
): Promise<boolean> {
  const existing = await getContact(db, orgId, contactId)
  if (!existing) {
    return false
  }

  // Remove engagement role assignments first
  await db.prepare('DELETE FROM engagement_contacts WHERE contact_id = ?').bind(contactId).run()

  await db.prepare('DELETE FROM contacts WHERE id = ? AND org_id = ?').bind(contactId, orgId).run()

  return true
}

/**
 * Assign a contact to an engagement role.
 *
 * A single contact can hold multiple roles on the same engagement (OQ-003).
 * The UNIQUE(engagement_id, contact_id, role) constraint prevents duplicate
 * role assignments.
 *
 * If isPrimary is true, clears any existing primary flag on the engagement first.
 */
export async function assignEngagementRole(
  db: D1Database,
  engagementId: string,
  contactId: string,
  role: EngagementContactRole,
  isPrimary: boolean = false,
  notes?: string | null
): Promise<EngagementContact> {
  const id = crypto.randomUUID()

  // If setting as primary, clear existing primary flags on this engagement
  if (isPrimary) {
    await db
      .prepare('UPDATE engagement_contacts SET is_primary = 0 WHERE engagement_id = ?')
      .bind(engagementId)
      .run()
  }

  await db
    .prepare(
      `INSERT INTO engagement_contacts (id, engagement_id, contact_id, role, is_primary, notes)
     VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind(id, engagementId, contactId, role, isPrimary ? 1 : 0, notes ?? null)
    .run()

  const result = await db
    .prepare('SELECT * FROM engagement_contacts WHERE id = ?')
    .bind(id)
    .first<EngagementContact>()

  if (!result) {
    throw new Error('Failed to retrieve created engagement contact')
  }
  return result
}

/**
 * Remove a contact's role assignment from an engagement.
 */
export async function removeEngagementRole(
  db: D1Database,
  engagementContactId: string
): Promise<boolean> {
  const result = await db
    .prepare('DELETE FROM engagement_contacts WHERE id = ?')
    .bind(engagementContactId)
    .run()

  return (result.meta?.changes ?? 0) > 0
}

/**
 * List contacts with their roles for an engagement.
 * Joins engagement_contacts with contacts to include contact details.
 */
export async function getEngagementContacts(
  db: D1Database,
  engagementId: string
): Promise<EngagementContact[]> {
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
      JOIN contacts c ON c.id = ec.contact_id
      WHERE ec.engagement_id = ?
      ORDER BY ec.is_primary DESC, c.name ASC`
    )
    .bind(engagementId)
    .all<EngagementContact>()

  return result.results
}

/**
 * Update the primary POC flag for an engagement contact.
 * Clears all other primary flags on the engagement first.
 */
export async function setEngagementPrimary(
  db: D1Database,
  engagementId: string,
  engagementContactId: string
): Promise<void> {
  await db
    .prepare('UPDATE engagement_contacts SET is_primary = 0 WHERE engagement_id = ?')
    .bind(engagementId)
    .run()

  await db
    .prepare('UPDATE engagement_contacts SET is_primary = 1 WHERE id = ? AND engagement_id = ?')
    .bind(engagementContactId, engagementId)
    .run()
}
