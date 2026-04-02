/**
 * Contact data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

export interface Contact {
  id: string
  org_id: string
  entity_id: string
  name: string
  email: string | null
  phone: string | null
  title: string | null
  role: string | null
  created_at: string
}

export interface CreateContactData {
  name: string
  email?: string | null
  phone?: string | null
  title?: string | null
  role?: string | null
}

export interface UpdateContactData {
  name?: string
  email?: string | null
  phone?: string | null
  title?: string | null
  role?: string | null
}

/**
 * List contacts for an entity, scoped to an organization.
 */
export async function listContacts(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Contact[]> {
  const result = await db
    .prepare('SELECT * FROM contacts WHERE org_id = ? AND entity_id = ? ORDER BY name ASC')
    .bind(orgId, entityId)
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
 * Create a new contact linked to an entity. Returns the created contact record.
 */
export async function createContact(
  db: D1Database,
  orgId: string,
  entityId: string,
  data: CreateContactData
): Promise<Contact> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO contacts (id, org_id, entity_id, name, email, phone, title, role)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      entityId,
      data.name,
      data.email ?? null,
      data.phone ?? null,
      data.title ?? null,
      data.role ?? null
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

  if (data.role !== undefined) {
    fields.push('role = ?')
    params.push(data.role)
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

  await db.prepare('DELETE FROM contacts WHERE id = ? AND org_id = ?').bind(contactId, orgId).run()

  return true
}
