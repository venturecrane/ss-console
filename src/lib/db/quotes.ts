/**
 * Quote data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 *
 * Business rules:
 * - Rate is frozen at quote creation time (never changes after)
 * - Client sees total project price only, never hourly breakdown (Decision #16)
 * - 5-day confirmation deadline from when quote is sent (Decision #18)
 * - Deposit is 50% by default, 3-milestone for 40+ hour engagements (Decision #14)
 */

export interface Quote {
  id: string
  org_id: string
  entity_id: string
  assessment_id: string
  version: number
  parent_quote_id: string | null
  line_items: string // JSON array of LineItem
  total_hours: number
  rate: number
  total_price: number
  deposit_pct: number
  deposit_amount: number | null
  status: string
  sent_at: string | null
  expires_at: string | null
  accepted_at: string | null
  sow_path: string | null
  signed_sow_path: string | null
  signwell_doc_id: string | null
  sow_generated_at: string | null
  created_at: string
  updated_at: string
}

export interface LineItem {
  problem: string
  description: string
  estimated_hours: number
}

export type QuoteStatus = 'draft' | 'sent' | 'accepted' | 'declined' | 'expired' | 'superseded'

export const QUOTE_STATUSES: { value: QuoteStatus; label: string }[] = [
  { value: 'draft', label: 'Draft' },
  { value: 'sent', label: 'Sent' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'declined', label: 'Declined' },
  { value: 'expired', label: 'Expired' },
  { value: 'superseded', label: 'Superseded' },
]

/**
 * Valid status transitions enforced at the application layer.
 *
 * draft       -> sent | superseded
 * sent        -> accepted | declined | expired | superseded
 * accepted    -> (terminal)
 * declined    -> (terminal)
 * expired     -> (terminal)
 * superseded  -> (terminal)
 */
export const VALID_TRANSITIONS: Record<QuoteStatus, QuoteStatus[]> = {
  draft: ['sent', 'superseded'],
  sent: ['accepted', 'declined', 'expired', 'superseded'],
  accepted: [],
  declined: [],
  expired: [],
  superseded: [],
}

export interface CreateQuoteData {
  entityId: string
  assessmentId: string
  lineItems: LineItem[]
  rate: number
  depositPct?: number
}

export interface UpdateQuoteData {
  lineItems?: LineItem[]
  rate?: number
  depositPct?: number
  sow_path?: string | null
  sow_generated_at?: string | null
}

/**
 * List quotes for an organization, optionally filtered by entity.
 */
export async function listQuotes(
  db: D1Database,
  orgId: string,
  entityId?: string
): Promise<Quote[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (entityId) {
    conditions.push('entity_id = ?')
    params.push(entityId)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM quotes WHERE ${where} ORDER BY updated_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Quote>()
  return result.results
}

/**
 * Get a single quote by ID, scoped to an organization.
 */
export async function getQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<Quote | null> {
  const result = await db
    .prepare('SELECT * FROM quotes WHERE id = ? AND org_id = ?')
    .bind(quoteId, orgId)
    .first<Quote>()

  return result ?? null
}

/**
 * Create a new quote. Returns the created quote record.
 *
 * Calculates total_hours (sum of line item hours), total_price (total_hours * rate),
 * and deposit_amount (total_price * deposit_pct).
 */
export async function createQuote(
  db: D1Database,
  orgId: string,
  data: CreateQuoteData
): Promise<Quote> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  const totalHours = data.lineItems.reduce((sum, item) => sum + item.estimated_hours, 0)
  const totalPrice = totalHours * data.rate
  const depositPct = data.depositPct ?? 0.5
  const depositAmount = totalPrice * depositPct

  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, version, line_items, total_hours, rate, total_price, deposit_pct, deposit_amount, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entityId,
      data.assessmentId,
      JSON.stringify(data.lineItems),
      totalHours,
      data.rate,
      totalPrice,
      depositPct,
      depositAmount,
      now,
      now
    )
    .run()

  const quote = await getQuote(db, orgId, id)
  if (!quote) {
    throw new Error('Failed to retrieve created quote')
  }
  return quote
}

/**
 * Update an existing quote. Returns the updated quote record.
 *
 * Recalculates totals if line items or rate change.
 * Only draft quotes should be updated (caller enforces this).
 */
export async function updateQuote(
  db: D1Database,
  orgId: string,
  quoteId: string,
  data: UpdateQuoteData
): Promise<Quote | null> {
  const existing = await getQuote(db, orgId, quoteId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  // Determine effective values for recalculation
  let lineItems: LineItem[] | undefined
  let rate: number | undefined

  if (data.lineItems !== undefined) {
    lineItems = data.lineItems
    fields.push('line_items = ?')
    params.push(JSON.stringify(data.lineItems))
  }

  if (data.rate !== undefined) {
    rate = data.rate
    fields.push('rate = ?')
    params.push(data.rate)
  }

  if (data.depositPct !== undefined) {
    fields.push('deposit_pct = ?')
    params.push(data.depositPct)
  }

  if (data.sow_path !== undefined) {
    fields.push('sow_path = ?')
    params.push(data.sow_path)
  }

  if (data.sow_generated_at !== undefined) {
    fields.push('sow_generated_at = ?')
    params.push(data.sow_generated_at)
  }

  // Recalculate totals if line items or rate changed
  if (lineItems !== undefined || rate !== undefined) {
    const effectiveItems = lineItems ?? (JSON.parse(existing.line_items) as LineItem[])
    const effectiveRate = rate ?? existing.rate
    const totalHours = effectiveItems.reduce((sum, item) => sum + item.estimated_hours, 0)
    const totalPrice = totalHours * effectiveRate

    fields.push('total_hours = ?')
    params.push(totalHours)
    fields.push('total_price = ?')
    params.push(totalPrice)

    const effectiveDepositPct = data.depositPct ?? existing.deposit_pct
    fields.push('deposit_amount = ?')
    params.push(totalPrice * effectiveDepositPct)
  } else if (data.depositPct !== undefined) {
    // Only deposit percentage changed, recalculate deposit amount
    fields.push('deposit_amount = ?')
    params.push(existing.total_price * data.depositPct)
  }

  if (fields.length === 0) {
    return existing
  }

  fields.push("updated_at = datetime('now')")

  const sql = `UPDATE quotes SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(quoteId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getQuote(db, orgId, quoteId)
}

/**
 * Transition quote status with validation.
 * Returns the updated record or null if the quote was not found.
 * Throws if the transition is invalid.
 *
 * Side effects:
 * - draft -> sent: sets sent_at and expires_at (sent_at + 5 days)
 * - sent -> accepted: sets accepted_at
 */
/**
 * Portal-facing statuses visible to clients.
 * Draft and superseded quotes are internal-only.
 */
const PORTAL_VISIBLE_STATUSES = ['sent', 'accepted', 'declined', 'expired'] as const

/**
 * List quotes for a specific entity (portal access).
 *
 * Scoped by entity_id (NOT org_id) — portal users access via their entity_id.
 * Only returns quotes visible to clients (sent, accepted, declined, expired).
 */
export async function listQuotesForEntity(db: D1Database, entityId: string): Promise<Quote[]> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM quotes WHERE entity_id = ? AND status IN (${placeholders}) ORDER BY updated_at DESC`

  const result = await db
    .prepare(sql)
    .bind(entityId, ...PORTAL_VISIBLE_STATUSES)
    .all<Quote>()
  return result.results
}

/**
 * Get a single quote for an entity (portal access).
 *
 * Scoped by entity_id (NOT org_id) — same status filter as list.
 */
export async function getQuoteForEntity(
  db: D1Database,
  entityId: string,
  quoteId: string
): Promise<Quote | null> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM quotes WHERE id = ? AND entity_id = ? AND status IN (${placeholders})`

  const result = await db
    .prepare(sql)
    .bind(quoteId, entityId, ...PORTAL_VISIBLE_STATUSES)
    .first<Quote>()

  return result ?? null
}

export async function updateQuoteStatus(
  db: D1Database,
  orgId: string,
  quoteId: string,
  newStatus: QuoteStatus
): Promise<Quote | null> {
  const existing = await getQuote(db, orgId, quoteId)
  if (!existing) {
    return null
  }

  const currentStatus = existing.status as QuoteStatus
  const validNext = VALID_TRANSITIONS[currentStatus] ?? []

  if (!validNext.includes(newStatus)) {
    throw new Error(
      `Invalid status transition: ${currentStatus} -> ${newStatus}. Valid transitions: ${validNext.join(', ') || 'none (terminal state)'}`
    )
  }

  const updates: string[] = ['status = ?']
  const params: (string | number | null)[] = [newStatus]

  if (newStatus === 'sent' && !existing.sent_at) {
    const sentAt = new Date()
    const expiresAt = new Date(sentAt.getTime() + 5 * 24 * 60 * 60 * 1000)
    updates.push('sent_at = ?')
    params.push(sentAt.toISOString())
    updates.push('expires_at = ?')
    params.push(expiresAt.toISOString())
  }

  // Acceptance guard: require SignWell signing flow completion
  if (newStatus === 'accepted') {
    if (!existing.signwell_doc_id) {
      throw new Error(
        'Cannot accept quote: signwell_doc_id is null. ' +
          'The quote must be sent through SignWell before it can be accepted.'
      )
    }
    if (!existing.signed_sow_path) {
      throw new Error(
        'Cannot accept quote: signed_sow_path is null. ' +
          'The signed SOW must be recorded by the SignWell webhook before acceptance.'
      )
    }
  }

  if (newStatus === 'accepted' && !existing.accepted_at) {
    updates.push('accepted_at = ?')
    params.push(new Date().toISOString())
  }

  updates.push("updated_at = datetime('now')")

  const sql = `UPDATE quotes SET ${updates.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(quoteId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getQuote(db, orgId, quoteId)
}
