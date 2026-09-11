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

import { getDefaultOriginatingSignalId } from './signal-attribution'
import { signedArtifactExists } from './signature-requests'
import { getMissingAuthoredContent, parseLineItems } from './quote-content'

export interface Quote {
  id: string
  org_id: string
  entity_id: string
  /**
   * Legacy FK into assessments.id. NOT NULL at the schema level. During the
   * monitoring window this value equals meeting_id (#469 preserves IDs across
   * the meetings backfill) and assessments rows are dual-written on intake.
   * A follow-up drop migration removes both the column and the assessments
   * table together.
   */
  assessment_id: string
  /**
   * New reference to meetings.id (#469). Nullable for pre-migration rows
   * until the backfill in migration 0025 populates it from assessment_id.
   * New code should prefer this column.
   */
  meeting_id: string | null
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
  // Authored client-facing content (#377). NULL until populated by the admin.
  // Render code falls back to rendering nothing — never to synthesized defaults.
  schedule: string | null // JSON array of ScheduleRow
  deliverables: string | null // JSON array of DeliverableRow
  engagement_overview: string | null
  milestone_label: string | null
  /**
   * Originating-signal attribution (#589). NULL until set; see
   * `signal-attribution.ts`. Set by default at quote creation to the most
   * recent signal on the entity.
   */
  originating_signal_id: string | null
  created_at: string
  updated_at: string
}

export interface LineItem {
  problem: string
  description: string
  estimated_hours: number
}

/**
 * A row in the per-quote "How we'll work" schedule rendered on the proposal
 * page. Authored by the admin; never synthesized.
 */
export interface ScheduleRow {
  label: string
  body: string
}

/**
 * A row in the per-quote deliverables list rendered on the proposal page and
 * the SOW PDF "Items" section. Authored by the admin; never derived from line
 * items at render time.
 */
export interface DeliverableRow {
  title: string
  body: string
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
  /**
   * Legacy assessment id. Still written to the NOT NULL `assessment_id`
   * column until the follow-up drop migration removes it. Callers should
   * pass `meetingId` for new work; when both are present they must match
   * (and currently do by construction — see migration 0025).
   */
  assessmentId: string
  /**
   * New canonical reference to meetings.id (#469). When omitted the caller
   * is asserting this quote pre-dates the meetings migration; in that case
   * createQuote copies assessmentId into meeting_id for forward compatibility.
   */
  meetingId?: string
  lineItems: LineItem[]
  rate: number
  depositPct?: number
  schedule?: ScheduleRow[]
  deliverables?: DeliverableRow[]
  engagementOverview?: string
  milestoneLabel?: string
  /**
   * Optional reference to a prior quote this one supersedes. Used by the
   * repeat-quote flow (#472) to link v2 → v1 after a decline/expiry. The
   * caller is responsible for explicitly transitioning the parent to
   * `superseded` — createQuote does not mutate the parent record.
   */
  parentQuoteId?: string | null
  /**
   * Originating signal attribution (#589). Same three-state contract as
   * `CreateEngagementData.originating_signal_id`: undefined → default,
   * string → explicit, null → skip default. Validation of the id belonging
   * to the entity/org is the caller's responsibility.
   */
  originatingSignalId?: string | null
}

export interface UpdateQuoteData {
  lineItems?: LineItem[]
  rate?: number
  depositPct?: number
  schedule?: ScheduleRow[] | null
  deliverables?: DeliverableRow[] | null
  engagementOverview?: string | null
  milestoneLabel?: string | null
  /**
   * Edit attribution post-creation (#589). `null` clears, string sets, omit
   * leaves unchanged. Recalculation of totals/version below is unaffected;
   * attribution is metadata, not part of the quote's pricing identity.
   */
  originatingSignalId?: string | null
}

/**
 * Quote statuses that block a new draft from being created on the same entity.
 * Draft and sent are "open" — actively in play in the sales flow. Other
 * statuses (accepted, declined, expired, superseded) are terminal and do not
 * block a repeat quote (#472).
 */
const OPEN_QUOTE_STATUSES: QuoteStatus[] = ['draft', 'sent']

/**
 * Return true if the entity has at least one draft or sent quote.
 * Used by the repeat-quote flow (#472) to gate the "New quote" action on the
 * entity detail page — admins shouldn't be creating v2 while v1 is still live.
 */
export async function hasOpenQuoteForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<boolean> {
  const placeholders = OPEN_QUOTE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT 1 FROM quotes WHERE entity_id = ? AND org_id = ? AND status IN (${placeholders}) LIMIT 1`
  const result = await db
    .prepare(sql)
    .bind(entityId, orgId, ...OPEN_QUOTE_STATUSES)
    .first<{ '1': number }>()
  return result !== null
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

  const scheduleJson =
    data.schedule && data.schedule.length > 0 ? JSON.stringify(data.schedule) : null
  const deliverablesJson =
    data.deliverables && data.deliverables.length > 0 ? JSON.stringify(data.deliverables) : null
  const engagementOverview = data.engagementOverview ?? null
  const milestoneLabel = data.milestoneLabel ?? null
  const parentQuoteId = data.parentQuoteId ?? null

  // Derive the next version number when superseding a prior quote so the
  // portal's "latest version" lookup (parent_quote_id OR assessment_id + version)
  // still resolves correctly. Standalone new quotes start at version 1.
  let version = 1
  if (parentQuoteId) {
    const parent = await db
      .prepare('SELECT version FROM quotes WHERE id = ? AND org_id = ?')
      .bind(parentQuoteId, orgId)
      .first<{ version: number }>()
    if (parent) {
      version = (parent.version ?? 1) + 1
    }
  }

  // meeting_id mirrors assessment_id by construction (meetings preserve the
  // assessment primary key) unless the caller passes an explicit meetingId.
  const meetingIdValue = data.meetingId ?? data.assessmentId

  // Resolve originating-signal attribution (#589). Same three-state contract
  // as createEngagement — undefined defaults to most-recent, null is an
  // explicit skip, string is an explicit override.
  const originatingSignalId =
    data.originatingSignalId === undefined
      ? await getDefaultOriginatingSignalId(db, orgId, data.entityId)
      : data.originatingSignalId

  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, meeting_id, version, parent_quote_id, line_items, total_hours, rate, total_price, deposit_pct, deposit_amount, status, schedule, deliverables, engagement_overview, milestone_label, originating_signal_id, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.entityId,
      data.assessmentId,
      meetingIdValue,
      version,
      parentQuoteId,
      JSON.stringify(data.lineItems),
      totalHours,
      data.rate,
      totalPrice,
      depositPct,
      depositAmount,
      scheduleJson,
      deliverablesJson,
      engagementOverview,
      milestoneLabel,
      originatingSignalId,
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

type QuoteFieldAccumulator = { fields: string[]; params: (string | number | null)[] }

function appendQuoteArrayField(
  acc: QuoteFieldAccumulator,
  column: string,
  value: unknown[] | null | undefined
): void {
  if (value === undefined) return
  acc.fields.push(`${column} = ?`)
  acc.params.push(value && value.length > 0 ? JSON.stringify(value) : null)
}

function appendQuoteTextField(
  acc: QuoteFieldAccumulator,
  column: string,
  value: string | null | undefined
): void {
  if (value === undefined) return
  const t = value?.trim() ?? null
  acc.fields.push(`${column} = ?`)
  acc.params.push(t && t.length > 0 ? t : null)
}

function appendTotalsFields(
  acc: QuoteFieldAccumulator,
  data: UpdateQuoteData,
  existing: Quote
): void {
  const lineItems = data.lineItems
  const rate = data.rate
  if (lineItems !== undefined || rate !== undefined) {
    const effectiveItems = lineItems ?? parseLineItems(existing.line_items)
    const effectiveRate = rate ?? existing.rate
    const totalHours = effectiveItems.reduce((sum, item) => sum + item.estimated_hours, 0)
    const totalPrice = totalHours * effectiveRate
    acc.fields.push('total_hours = ?', 'total_price = ?', 'deposit_amount = ?')
    acc.params.push(totalHours, totalPrice, totalPrice * (data.depositPct ?? existing.deposit_pct))
  } else if (data.depositPct !== undefined) {
    acc.fields.push('deposit_amount = ?')
    acc.params.push(existing.total_price * data.depositPct)
  }
}

function buildQuoteFields(data: UpdateQuoteData, existing: Quote): QuoteFieldAccumulator {
  const acc: QuoteFieldAccumulator = { fields: [], params: [] }

  if (data.lineItems !== undefined) {
    acc.fields.push('line_items = ?')
    acc.params.push(JSON.stringify(data.lineItems))
  }
  if (data.rate !== undefined) {
    acc.fields.push('rate = ?')
    acc.params.push(data.rate)
  }
  if (data.depositPct !== undefined) {
    acc.fields.push('deposit_pct = ?')
    acc.params.push(data.depositPct)
  }
  appendQuoteArrayField(acc, 'schedule', data.schedule)
  appendQuoteArrayField(acc, 'deliverables', data.deliverables)
  appendQuoteTextField(acc, 'engagement_overview', data.engagementOverview)
  appendQuoteTextField(acc, 'milestone_label', data.milestoneLabel)
  if (data.originatingSignalId !== undefined) {
    acc.fields.push('originating_signal_id = ?')
    acc.params.push(data.originatingSignalId)
  }
  appendTotalsFields(acc, data, existing)

  return acc
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
  if (!existing) return null

  const { fields, params } = buildQuoteFields(data, existing)
  if (fields.length === 0) return existing

  const bumpsVersion =
    data.lineItems !== undefined ||
    data.rate !== undefined ||
    data.depositPct !== undefined ||
    data.schedule !== undefined ||
    data.deliverables !== undefined ||
    data.engagementOverview !== undefined ||
    data.milestoneLabel !== undefined

  if (bumpsVersion) fields.push('version = version + 1')
  fields.push("updated_at = datetime('now')")

  const updateSql = `UPDATE quotes SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  await db
    .prepare(updateSql)
    .bind(...params, quoteId, orgId)
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
 * For a set of entity ids, return the "top active" quote per entity — the one
 * an admin scanning a list would care about. Priority:
 *
 *   1. Most recently sent `sent` quote (the one in the client's hands)
 *   2. Otherwise most recently updated `accepted` quote
 *   3. Otherwise most recently updated `draft` quote
 *
 * Terminal non-useful statuses (declined, expired, superseded) are ignored.
 * Returns a Map keyed by entity_id so the caller can render badges inline on
 * a list without an N+1 per-row query.
 *
 * Scoped by org_id for tenant isolation. Empty id list short-circuits to an
 * empty map (D1 rejects `IN ()`).
 */
export async function getActiveQuotesForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, Quote>> {
  const result = new Map<string, Quote>()
  if (entityIds.length === 0) return result

  // D1 caps bound parameters at 100 per statement; pass entity-id list as
  // a single JSON parameter via json_each() so large Proposing tabs don't
  // trip the limit. See https://developers.cloudflare.com/d1/sql-api/query-json/.
  const entityIdsJson = JSON.stringify(entityIds)
  // Priority: sent (0) > accepted (1) > draft (2). We order by priority then by
  // `sent_at` DESC (for sent), else `updated_at` DESC. One row per entity via
  // the ROW_NUMBER() window function. D1 supports SQLite window functions.
  const sql = `
    SELECT id, org_id, entity_id, assessment_id, version, parent_quote_id,
           line_items, total_hours, rate, total_price, deposit_pct,
           deposit_amount, status, sent_at, expires_at, accepted_at,
           schedule, deliverables, engagement_overview, milestone_label,
           originating_signal_id,
           created_at, updated_at
    FROM (
      SELECT q.*,
        ROW_NUMBER() OVER (
          PARTITION BY q.entity_id
          ORDER BY
            CASE q.status
              WHEN 'sent'     THEN 0
              WHEN 'accepted' THEN 1
              WHEN 'draft'    THEN 2
              ELSE 9
            END,
            COALESCE(q.sent_at, q.updated_at) DESC
        ) AS rn
      FROM quotes q
      WHERE q.org_id = ?
        AND q.entity_id IN (SELECT value FROM json_each(?))
        AND q.status IN ('sent', 'accepted', 'draft')
    )
    WHERE rn = 1
  `

  const rows = await db.prepare(sql).bind(orgId, entityIdsJson).all<Quote>()

  for (const row of rows.results) {
    result.set(row.entity_id, row)
  }
  return result
}

/**
 * For a batch of entity ids, return a Map keyed by entity_id whose value
 * is the list of all quotes (any status) for that entity. Used by the
 * meetings-stage list to ask, per row, "is there any quote already
 * linked to a completed meeting?" — which is what makes that meeting
 * NOT draftable.
 *
 * Distinct from `getActiveQuotesForEntities` (top-1 active quote per
 * entity for the proposing tab badge): this returns all quotes,
 * including terminal statuses (declined, expired, superseded). A
 * declined quote means the operator already drafted once — the
 * meeting isn't draftable a second time.
 *
 * Empty input returns an empty Map without touching the DB.
 */
export async function getQuotesForEntities(
  db: D1Database,
  orgId: string,
  entityIds: string[]
): Promise<Map<string, Quote[]>> {
  const result = new Map<string, Quote[]>()
  if (entityIds.length === 0) return result

  const entityIdsJson = JSON.stringify(entityIds)
  const rows = await db
    .prepare(
      `SELECT * FROM quotes
       WHERE org_id = ?
         AND entity_id IN (SELECT value FROM json_each(?))
       ORDER BY entity_id ASC, created_at ASC`
    )
    .bind(orgId, entityIdsJson)
    .all<Quote>()

  for (const row of rows.results ?? []) {
    const list = result.get(row.entity_id)
    if (list) list.push(row)
    else result.set(row.entity_id, [row])
  }
  return result
}

/**
 * List quotes for a specific entity (portal access).
 *
 * Scoped by both `entity_id` and `org_id` — entity IDs are expected unique,
 * but org_id enforces defense-in-depth tenant isolation for the portal (#399).
 * Only returns quotes visible to clients (sent, accepted, declined, expired).
 */
export async function listQuotesForEntity(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<Quote[]> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM quotes WHERE entity_id = ? AND org_id = ? AND status IN (${placeholders}) ORDER BY updated_at DESC`

  const result = await db
    .prepare(sql)
    .bind(entityId, orgId, ...PORTAL_VISIBLE_STATUSES)
    .all<Quote>()
  return result.results
}

/**
 * Get a single quote for an entity (portal access).
 *
 * Scoped by `entity_id` AND `org_id` for defense-in-depth tenant isolation
 * (#399). Same status filter as list.
 */
export async function getQuoteForEntity(
  db: D1Database,
  orgId: string,
  entityId: string,
  quoteId: string
): Promise<Quote | null> {
  const placeholders = PORTAL_VISIBLE_STATUSES.map(() => '?').join(', ')
  const sql = `SELECT * FROM quotes WHERE id = ? AND entity_id = ? AND org_id = ? AND status IN (${placeholders})`

  const result = await db
    .prepare(sql)
    .bind(quoteId, entityId, orgId, ...PORTAL_VISIBLE_STATUSES)
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

  // Send-gating: a draft quote can only be sent once schedule + deliverables
  // are authored. Without these, downstream rendering would either show empty
  // sections or (pre-#377) synthesize fabricated content. See #377.
  if (newStatus === 'sent') {
    const missing = getMissingAuthoredContent(existing)
    if (missing.length > 0) {
      throw new Error(
        `Cannot send quote: missing authored client-facing content (${missing.join(', ')}). Author the schedule and deliverables in the quote builder before sending.`
      )
    }
  }

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
    const acceptanceReady = await signedArtifactExists(db, orgId, quoteId)
    if (!acceptanceReady) {
      throw new Error(
        'Cannot accept quote: a completed signed signature request with a persisted signed artifact is required.'
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
