/**
 * Invoice data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 *
 * Business rules:
 * - Deposit invoice: 50% at signing, auto-created by SignWell webhook (Decision #14)
 * - Completion invoice: remaining balance at engagement completion
 * - Milestone invoices: for 40+ hour engagements (3-milestone billing)
 * - Assessment invoice: standalone paid assessment
 * - Retainer invoice: monthly post-delivery support
 * - Status machine enforced at application layer
 * - Client sees total project price only, never hourly breakdown (Decision #16)
 */

export interface Invoice {
  id: string
  org_id: string
  engagement_id: string | null
  entity_id: string
  type: string
  amount: number
  description: string | null
  status: string
  stripe_invoice_id: string | null
  stripe_hosted_url: string | null
  due_date: string | null
  sent_at: string | null
  paid_at: string | null
  payment_method: string | null
  created_at: string
  updated_at: string
}

export type InvoiceType = 'deposit' | 'completion' | 'milestone' | 'assessment' | 'retainer'

export type InvoiceStatus = 'draft' | 'sent' | 'paid' | 'overdue' | 'void'

export const INVOICE_STATUSES: { value: InvoiceStatus; label: string }[] = [
  { value: 'draft', label: 'Draft' },
  { value: 'sent', label: 'Sent' },
  { value: 'paid', label: 'Paid' },
  { value: 'overdue', label: 'Overdue' },
  { value: 'void', label: 'Void' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * draft    -> sent | void
 * sent     -> paid | overdue | void
 * overdue  -> paid | void
 * paid     -> (terminal)
 * void     -> (terminal)
 */
export const VALID_TRANSITIONS: Record<InvoiceStatus, InvoiceStatus[]> = {
  draft: ['sent', 'void'],
  sent: ['paid', 'overdue', 'void'],
  overdue: ['paid', 'void'],
  paid: [],
  void: [],
}

export interface CreateInvoiceData {
  entity_id: string
  engagement_id?: string | null
  type: InvoiceType
  amount: number
  description?: string | null
  due_date?: string | null
}

export interface UpdateInvoiceData {
  amount?: number
  description?: string | null
  due_date?: string | null
  stripe_invoice_id?: string | null
  stripe_hosted_url?: string | null
}

export interface InvoiceFilters {
  entityId?: string
  engagementId?: string
  status?: InvoiceStatus
}

/**
 * List invoices for an organization, optionally filtered by entity, engagement, or status.
 */
export async function listInvoices(
  db: D1Database,
  orgId: string,
  filters?: InvoiceFilters
): Promise<Invoice[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.entityId) {
    conditions.push('entity_id = ?')
    params.push(filters.entityId)
  }

  if (filters?.engagementId) {
    conditions.push('engagement_id = ?')
    params.push(filters.engagementId)
  }

  if (filters?.status) {
    conditions.push('status = ?')
    params.push(filters.status)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM invoices WHERE ${where} ORDER BY created_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Invoice>()
  return result.results
}

/**
 * Get a single invoice by ID, scoped to an organization.
 */
export async function getInvoice(
  db: D1Database,
  orgId: string,
  invoiceId: string
): Promise<Invoice | null> {
  const result = await db
    .prepare('SELECT * FROM invoices WHERE id = ? AND org_id = ?')
    .bind(invoiceId, orgId)
    .first<Invoice>()

  return result ?? null
}

/**
 * Create a new invoice. Returns the created invoice record.
 */
export async function createInvoice(
  db: D1Database,
  orgId: string,
  data: CreateInvoiceData
): Promise<Invoice> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO invoices (id, org_id, entity_id, engagement_id, type, amount, description, status, due_date, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entity_id,
      data.engagement_id ?? null,
      data.type,
      data.amount,
      data.description ?? null,
      data.due_date ?? null,
      now,
      now
    )
    .run()

  const invoice = await getInvoice(db, orgId, id)
  if (!invoice) {
    throw new Error('Failed to retrieve created invoice')
  }
  return invoice
}

/**
 * Update an existing invoice. Returns the updated invoice record.
 * Only draft invoices should be updated (caller enforces this).
 */
export async function updateInvoice(
  db: D1Database,
  orgId: string,
  invoiceId: string,
  data: UpdateInvoiceData
): Promise<Invoice | null> {
  const existing = await getInvoice(db, orgId, invoiceId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.amount !== undefined) {
    fields.push('amount = ?')
    params.push(data.amount)
  }

  if (data.description !== undefined) {
    fields.push('description = ?')
    params.push(data.description)
  }

  if (data.due_date !== undefined) {
    fields.push('due_date = ?')
    params.push(data.due_date)
  }

  if (data.stripe_invoice_id !== undefined) {
    fields.push('stripe_invoice_id = ?')
    params.push(data.stripe_invoice_id)
  }

  if (data.stripe_hosted_url !== undefined) {
    fields.push('stripe_hosted_url = ?')
    params.push(data.stripe_hosted_url)
  }

  if (fields.length === 0) {
    return existing
  }

  fields.push("updated_at = datetime('now')")

  const sql = `UPDATE invoices SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(invoiceId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getInvoice(db, orgId, invoiceId)
}

/**
 * Transition invoice status with validation.
 * Returns the updated record or null if the invoice was not found.
 * Throws if the transition is invalid.
 *
 * Side effects:
 * - Any -> paid: sets paid_at to now
 * - draft -> sent: sets sent_at to now
 */
export async function updateInvoiceStatus(
  db: D1Database,
  orgId: string,
  invoiceId: string,
  newStatus: InvoiceStatus
): Promise<Invoice | null> {
  const existing = await getInvoice(db, orgId, invoiceId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status as InvoiceStatus
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  const updates: string[] = ['status = ?']
  const params: (string | number | null)[] = [newStatus]

  if (newStatus === 'paid' && !existing.paid_at) {
    updates.push('paid_at = ?')
    params.push(new Date().toISOString())
  }

  if (newStatus === 'sent' && !existing.sent_at) {
    updates.push('sent_at = ?')
    params.push(new Date().toISOString())
  }

  updates.push("updated_at = datetime('now')")

  const sql = `UPDATE invoices SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(invoiceId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getInvoice(db, orgId, invoiceId)
}

/**
 * Portal-facing statuses visible to clients.
 * Draft and void invoices are internal-only.
 */
const PORTAL_VISIBLE_STATUSES = ['sent', 'paid', 'overdue'] as const

/**
 * List invoices for a specific entity (portal access).
 *
 * Scoped by both `entity_id` and `org_id` for defense-in-depth tenant
 * isolation (#399). Only returns invoices visible to clients (sent, paid,
 * overdue).
 */
export async function listInvoicesForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Invoice[]> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM invoices WHERE entity_id = ? AND org_id = ? AND status IN (${placeholders}) ORDER BY created_at DESC`

  const result = await db
    .prepare(sql)
    .bind(entityId, orgId, ...PORTAL_VISIBLE_STATUSES)
    .all<Invoice>()
  return result.results
}

/**
 * Get a single invoice for a specific entity (portal access).
 *
 * Scoped by `entity_id` AND `org_id` for defense-in-depth tenant isolation
 * (#399). Only returns invoices in portal-visible statuses (sent, paid,
 * overdue). Draft and void invoices are never exposed.
 */
export async function getInvoiceForEntity(
  db: D1Database,
  orgId: string,
  entityId: string,
  invoiceId: string
): Promise<Invoice | null> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM invoices WHERE id = ? AND entity_id = ? AND org_id = ? AND status IN (${placeholders})`

  const result = await db
    .prepare(sql)
    .bind(invoiceId, entityId, orgId, ...PORTAL_VISIBLE_STATUSES)
    .first<Invoice>()

  return result ?? null
}

export interface InvoiceLineItem {
  id: string
  invoice_id: string
  description: string
  amount_cents: number
  sort_order: number
  created_at: string
}

/**
 * List line items for an invoice, sorted by sort_order then created_at.
 * Returns an empty array if the invoice has no line items — callers are
 * responsible for rendering a fallback row.
 */
export async function listLineItemsForInvoice(
  db: D1Database,
  invoiceId: string
): Promise<InvoiceLineItem[]> {
  const result = await db
    .prepare(
      `SELECT * FROM invoice_line_items
       WHERE invoice_id = ?
       ORDER BY sort_order ASC, created_at ASC`
    )
    .bind(invoiceId)
    .all<InvoiceLineItem>()
  return result.results
}
