/**
 * Parking lot data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 *
 * Parking lot items capture scope requests that surface during an engagement
 * but fall outside the current SOW. Items are dispositioned as fold_in (absorb
 * into current scope), follow_on (create a new quote), or dropped.
 */

export interface ParkingLotItem {
  id: string
  engagement_id: string
  description: string
  requested_by: string | null
  requested_at: string
  disposition: string | null
  disposition_note: string | null
  reviewed_at: string | null
  follow_on_quote_id: string | null
  created_at: string
}

export type Disposition = 'fold_in' | 'follow_on' | 'dropped'

export const DISPOSITIONS: { value: Disposition; label: string }[] = [
  { value: 'fold_in', label: 'Fold In' },
  { value: 'follow_on', label: 'Follow-on' },
  { value: 'dropped', label: 'Dropped' },
]

export interface CreateParkingLotItemData {
  description: string
  requested_by?: string | null
}

export interface UpdateParkingLotItemData {
  description?: string
  requested_by?: string | null
}

/**
 * List parking lot items for an engagement, ordered by created_at DESC.
 */
export async function listParkingLotItems(
  db: D1Database,
  engagementId: string
): Promise<ParkingLotItem[]> {
  const result = await db
    .prepare('SELECT * FROM parking_lot WHERE engagement_id = ? ORDER BY created_at DESC')
    .bind(engagementId)
    .all<ParkingLotItem>()
  return result.results
}

/**
 * Get a single parking lot item by ID.
 */
export async function getParkingLotItem(
  db: D1Database,
  id: string
): Promise<ParkingLotItem | null> {
  const result = await db
    .prepare('SELECT * FROM parking_lot WHERE id = ?')
    .bind(id)
    .first<ParkingLotItem>()

  return result ?? null
}

/**
 * Create a new parking lot item linked to an engagement. Returns the created record.
 */
export async function createParkingLotItem(
  db: D1Database,
  engagementId: string,
  data: CreateParkingLotItemData
): Promise<ParkingLotItem> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO parking_lot (id, engagement_id, description, requested_by)
     VALUES (?, ?, ?, ?)`
    )
    .bind(id, engagementId, data.description, data.requested_by ?? null)
    .run()

  const item = await getParkingLotItem(db, id)
  if (!item) {
    throw new Error('Failed to retrieve created parking lot item')
  }
  return item
}

/**
 * Update an existing parking lot item (description and requested_by only).
 * Returns the updated record.
 */
export async function updateParkingLotItem(
  db: D1Database,
  id: string,
  data: UpdateParkingLotItemData
): Promise<ParkingLotItem | null> {
  const existing = await getParkingLotItem(db, id)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.description !== undefined) {
    fields.push('description = ?')
    params.push(data.description)
  }

  if (data.requested_by !== undefined) {
    fields.push('requested_by = ?')
    params.push(data.requested_by)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE parking_lot SET ${fields.join(', ')} WHERE id = ?`
  params.push(id)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getParkingLotItem(db, id)
}

/**
 * Dispose of a parking lot item. Sets disposition, disposition_note, and reviewed_at.
 * Returns the updated record.
 */
export async function disposeParkingLotItem(
  db: D1Database,
  id: string,
  disposition: Disposition,
  note?: string | null
): Promise<ParkingLotItem | null> {
  const existing = await getParkingLotItem(db, id)
  if (!existing) {
    return null
  }

  const now = new Date().toISOString()

  await db
    .prepare(
      'UPDATE parking_lot SET disposition = ?, disposition_note = ?, reviewed_at = ? WHERE id = ?'
    )
    .bind(disposition, note ?? null, now, id)
    .run()

  return getParkingLotItem(db, id)
}

/**
 * Link a follow-on quote to a parking lot item.
 * Returns the updated record.
 */
export async function linkFollowOnQuote(
  db: D1Database,
  id: string,
  quoteId: string
): Promise<ParkingLotItem | null> {
  const existing = await getParkingLotItem(db, id)
  if (!existing) {
    return null
  }

  await db
    .prepare('UPDATE parking_lot SET follow_on_quote_id = ? WHERE id = ?')
    .bind(quoteId, id)
    .run()

  return getParkingLotItem(db, id)
}

/**
 * Delete a parking lot item. Returns true if the item was found and deleted.
 */
export async function deleteParkingLotItem(db: D1Database, id: string): Promise<boolean> {
  const existing = await getParkingLotItem(db, id)
  if (!existing) {
    return false
  }

  await db.prepare('DELETE FROM parking_lot WHERE id = ?').bind(id).run()

  return true
}
