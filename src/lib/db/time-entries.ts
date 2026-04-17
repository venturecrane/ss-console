/**
 * Time entry data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 *
 * After every create/update/delete, recalculateActualHours is called
 * to keep engagements.actual_hours in sync with the SUM of time_entries.
 */

export interface TimeEntry {
  id: string
  engagement_id: string
  date: string
  hours: number
  description: string | null
  category: string | null
  created_at: string
}

export type TimeEntryCategory =
  | 'solution_design'
  | 'implementation'
  | 'training'
  | 'admin'
  | 'other'

export const TIME_ENTRY_CATEGORIES: { value: TimeEntryCategory; label: string }[] = [
  { value: 'solution_design', label: 'Solution Design' },
  { value: 'implementation', label: 'Implementation' },
  { value: 'training', label: 'Training' },
  { value: 'admin', label: 'Admin' },
  { value: 'other', label: 'Other' },
]

export interface CreateTimeEntryData {
  date: string
  hours: number
  description?: string | null
  category?: string | null
}

export interface UpdateTimeEntryData {
  date?: string
  hours?: number
  description?: string | null
  category?: string | null
}

/**
 * List time entries for an engagement, scoped by org.
 * All reads, writes, and the actual-hours recalc are scoped by org_id for
 * defense-in-depth tenant isolation (#399). The DAL primitive cannot be
 * used to read or mutate rows outside the caller's org.
 */
export async function listTimeEntries(
  db: D1Database,
  orgId: string,
  engagementId: string
): Promise<TimeEntry[]> {
  const result = await db
    .prepare('SELECT * FROM time_entries WHERE engagement_id = ? AND org_id = ? ORDER BY date DESC')
    .bind(engagementId, orgId)
    .all<TimeEntry>()
  return result.results
}

/**
 * Get a single time entry by ID, scoped by org.
 */
export async function getTimeEntry(
  db: D1Database,
  orgId: string,
  id: string
): Promise<TimeEntry | null> {
  const result = await db
    .prepare('SELECT * FROM time_entries WHERE id = ? AND org_id = ?')
    .bind(id, orgId)
    .first<TimeEntry>()

  return result ?? null
}

/**
 * Recalculate engagement actual_hours from the SUM of time_entries.hours.
 * Scoped by org_id on both sides (the SUM and the engagement UPDATE).
 * Called automatically after create/update/delete.
 */
export async function recalculateActualHours(
  db: D1Database,
  orgId: string,
  engagementId: string
): Promise<void> {
  const result = await db
    .prepare(
      'SELECT COALESCE(SUM(hours), 0) as total FROM time_entries WHERE engagement_id = ? AND org_id = ?'
    )
    .bind(engagementId, orgId)
    .first<{ total: number }>()

  const total = result?.total ?? 0

  await db
    .prepare(
      "UPDATE engagements SET actual_hours = ?, updated_at = datetime('now') WHERE id = ? AND org_id = ?"
    )
    .bind(total, engagementId, orgId)
    .run()
}

/**
 * Create a new time entry linked to an engagement. Returns the created record.
 * The INSERT pulls org_id from the engagement row, but we also require the
 * caller to pass orgId so a cross-org engagement ID fails cleanly on the
 * subsequent read instead of inserting an orphaned row.
 */
export async function createTimeEntry(
  db: D1Database,
  orgId: string,
  engagementId: string,
  data: CreateTimeEntryData
): Promise<TimeEntry> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO time_entries (id, org_id, engagement_id, date, hours, description, category)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      engagementId,
      data.date,
      data.hours,
      data.description ?? null,
      data.category ?? null
    )
    .run()

  await recalculateActualHours(db, orgId, engagementId)

  const entry = await getTimeEntry(db, orgId, id)
  if (!entry) {
    throw new Error('Failed to retrieve created time entry')
  }
  return entry
}

/**
 * Update an existing time entry, scoped by org. Returns the updated record
 * or null if no matching row exists in the caller's org.
 * Automatically recalculates engagement actual_hours after update.
 */
export async function updateTimeEntry(
  db: D1Database,
  orgId: string,
  id: string,
  data: UpdateTimeEntryData
): Promise<TimeEntry | null> {
  const existing = await getTimeEntry(db, orgId, id)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.date !== undefined) {
    fields.push('date = ?')
    params.push(data.date)
  }

  if (data.hours !== undefined) {
    fields.push('hours = ?')
    params.push(data.hours)
  }

  if (data.description !== undefined) {
    fields.push('description = ?')
    params.push(data.description)
  }

  if (data.category !== undefined) {
    fields.push('category = ?')
    params.push(data.category)
  }

  if (fields.length === 0) {
    return existing
  }

  const sql = `UPDATE time_entries SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(id, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  await recalculateActualHours(db, orgId, existing.engagement_id)

  return getTimeEntry(db, orgId, id)
}

/**
 * Delete a time entry, scoped by org. Returns true if a matching row
 * existed in the caller's org and was deleted.
 * Automatically recalculates engagement actual_hours after delete.
 */
export async function deleteTimeEntry(db: D1Database, orgId: string, id: string): Promise<boolean> {
  const existing = await getTimeEntry(db, orgId, id)
  if (!existing) {
    return false
  }

  const engagementId = existing.engagement_id

  await db.prepare('DELETE FROM time_entries WHERE id = ? AND org_id = ?').bind(id, orgId).run()

  await recalculateActualHours(db, orgId, engagementId)

  return true
}
