/**
 * Retainer data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID().
 *
 * Business rules:
 * - Retainers are post-delivery recurring service agreements
 * - Created when an engagement completes and the client opts into ongoing support
 * - Status machine: active -> paused -> active (toggle), active/paused -> cancelled (terminal)
 * - next_billing_date initialized to start_date, advanced by recordRetainerBilling
 * - The follow-up processor's billing handler reads next_billing_date to generate monthly invoices
 */

export interface Retainer {
  id: string
  org_id: string
  entity_id: string
  engagement_id: string | null
  monthly_rate: number
  included_hours: number | null
  scope_description: string | null
  terms: string | null
  cancellation_policy: string | null
  start_date: string
  end_date: string | null
  status: RetainerStatus
  last_billed_at: string | null
  next_billing_date: string
  created_at: string
  updated_at: string
}

export type RetainerStatus = 'active' | 'paused' | 'cancelled'

export const RETAINER_STATUSES: { value: RetainerStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'paused', label: 'Paused' },
  { value: 'cancelled', label: 'Cancelled' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * active    -> paused | cancelled
 * paused    -> active | cancelled
 * cancelled -> (terminal)
 */
export const VALID_TRANSITIONS: Record<RetainerStatus, RetainerStatus[]> = {
  active: ['paused', 'cancelled'],
  paused: ['active', 'cancelled'],
  cancelled: [],
}

export interface CreateRetainerData {
  entity_id: string
  engagement_id?: string | null
  monthly_rate: number
  included_hours?: number | null
  scope_description?: string | null
  terms?: string | null
  cancellation_policy?: string | null
  start_date: string
  end_date?: string | null
}

/**
 * Get a single retainer by ID, scoped to an organization.
 */
export async function getRetainer(
  db: D1Database,
  orgId: string,
  retainerId: string
): Promise<Retainer | null> {
  const result = await db
    .prepare('SELECT * FROM retainers WHERE id = ? AND org_id = ?')
    .bind(retainerId, orgId)
    .first<Retainer>()

  return result ?? null
}

/**
 * Create a new retainer. Sets next_billing_date = start_date.
 * Returns the created retainer record.
 */
export async function createRetainer(
  db: D1Database,
  orgId: string,
  data: CreateRetainerData
): Promise<Retainer> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO retainers (id, org_id, entity_id, engagement_id, monthly_rate, included_hours, scope_description, terms, cancellation_policy, start_date, end_date, status, next_billing_date, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entity_id,
      data.engagement_id ?? null,
      data.monthly_rate,
      data.included_hours ?? null,
      data.scope_description ?? null,
      data.terms ?? null,
      data.cancellation_policy ?? null,
      data.start_date,
      data.end_date ?? null,
      data.start_date, // next_billing_date = start_date
      now,
      now
    )
    .run()

  const retainer = await getRetainer(db, orgId, id)
  if (!retainer) {
    throw new Error('Failed to retrieve created retainer')
  }
  return retainer
}

/**
 * List active retainers for an organization (status = 'active').
 */
export async function listActiveRetainers(db: D1Database, orgId: string): Promise<Retainer[]> {
  const result = await db
    .prepare(
      `SELECT * FROM retainers WHERE org_id = ? AND status = 'active' ORDER BY next_billing_date ASC`
    )
    .bind(orgId)
    .all<Retainer>()
  return result.results
}

/**
 * List retainers for a specific entity, all statuses.
 */
export async function listRetainersForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Retainer[]> {
  const result = await db
    .prepare(`SELECT * FROM retainers WHERE org_id = ? AND entity_id = ? ORDER BY created_at DESC`)
    .bind(orgId, entityId)
    .all<Retainer>()
  return result.results
}

/**
 * Transition retainer status with validation.
 * Returns the updated record or null if the retainer was not found.
 * Throws if the transition is invalid.
 */
export async function updateRetainerStatus(
  db: D1Database,
  orgId: string,
  retainerId: string,
  newStatus: RetainerStatus
): Promise<Retainer | null> {
  const existing = await getRetainer(db, orgId, retainerId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  await db
    .prepare(
      `UPDATE retainers SET status = ?, updated_at = datetime('now') WHERE id = ? AND org_id = ?`
    )
    .bind(newStatus, retainerId, orgId)
    .run()

  return getRetainer(db, orgId, retainerId)
}

/**
 * Record a billing event for a retainer.
 * Advances next_billing_date by 1 month and sets last_billed_at to now.
 * Returns the updated retainer record.
 */
export async function recordRetainerBilling(
  db: D1Database,
  orgId: string,
  retainerId: string
): Promise<Retainer | null> {
  const existing = await getRetainer(db, orgId, retainerId)
  if (!existing) {
    return null
  }

  // Advance next_billing_date by 1 month
  const currentBillingDate = new Date(existing.next_billing_date)
  currentBillingDate.setMonth(currentBillingDate.getMonth() + 1)
  const nextBillingDate = currentBillingDate.toISOString().split('T')[0]

  const now = new Date().toISOString()

  await db
    .prepare(
      `UPDATE retainers SET last_billed_at = ?, next_billing_date = ?, updated_at = datetime('now') WHERE id = ? AND org_id = ?`
    )
    .bind(now, nextBillingDate, retainerId, orgId)
    .run()

  return getRetainer(db, orgId, retainerId)
}
