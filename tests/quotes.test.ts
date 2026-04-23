import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('quotes: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/quotes.ts'), 'utf-8')

  it('quotes.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/quotes.ts'))).toBe(true)
  })

  it('exports listQuotes function', () => {
    expect(source()).toContain('export async function listQuotes')
  })

  it('exports getQuote function', () => {
    expect(source()).toContain('export async function getQuote')
  })

  it('exports createQuote function', () => {
    expect(source()).toContain('export async function createQuote')
  })

  it('exports updateQuote function', () => {
    expect(source()).toContain('export async function updateQuote')
  })

  it('exports updateQuoteStatus function', () => {
    expect(source()).toContain('export async function updateQuoteStatus')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('scopes all queries to org_id', () => {
    const code = source()
    expect(code).toContain("'org_id = ?'")
    expect(code).toContain('org_id = ?')
  })

  it('supports optional entity_id filter in listQuotes', () => {
    const code = source()
    expect(code).toContain("'entity_id = ?'")
  })

  it('defines all valid quote statuses', () => {
    const code = source()
    expect(code).toContain("'draft'")
    expect(code).toContain("'sent'")
    expect(code).toContain("'accepted'")
    expect(code).toContain("'declined'")
    expect(code).toContain("'expired'")
    expect(code).toContain("'superseded'")
  })

  it('exports QUOTE_STATUSES constant', () => {
    expect(source()).toContain('export const QUOTE_STATUSES')
  })

  it('exports VALID_TRANSITIONS for status state machine', () => {
    expect(source()).toContain('export const VALID_TRANSITIONS')
  })

  it('enforces valid status transitions', () => {
    const code = source()
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('Invalid status transition')
  })

  it('defines valid transitions: draft -> sent | superseded', () => {
    const code = source()
    expect(code).toContain("draft: ['sent', 'superseded']")
  })

  it('defines valid transitions: sent -> accepted | declined | expired | superseded', () => {
    const code = source()
    expect(code).toContain("sent: ['accepted', 'declined', 'expired', 'superseded']")
  })

  it('accepted, declined, expired, and superseded are terminal states', () => {
    const code = source()
    expect(code).toContain('accepted: []')
    expect(code).toContain('declined: []')
    expect(code).toContain('expired: []')
    expect(code).toContain('superseded: []')
  })

  it('calculates total_hours as sum of line item hours', () => {
    const code = source()
    expect(code).toContain('totalHours')
    expect(code).toContain('estimated_hours')
    expect(code).toContain('reduce')
  })

  it('calculates total_price as total_hours * rate', () => {
    const code = source()
    expect(code).toContain('totalHours * data.rate')
    // Also check recalculation path
    expect(code).toContain('totalHours * effectiveRate')
  })

  it('calculates deposit_amount as total_price * deposit_pct', () => {
    const code = source()
    expect(code).toContain('totalPrice * depositPct')
  })

  it('sets sent_at and expires_at when transitioning to sent (5-day deadline)', () => {
    const code = source()
    expect(code).toContain("newStatus === 'sent'")
    expect(code).toContain('sent_at')
    expect(code).toContain('expires_at')
    expect(code).toContain('5 * 24 * 60 * 60 * 1000')
  })

  it('sets accepted_at when transitioning to accepted', () => {
    const code = source()
    expect(code).toContain("newStatus === 'accepted'")
    expect(code).toContain('accepted_at')
  })

  it('stores line items as JSON string', () => {
    const code = source()
    expect(code).toContain('JSON.stringify(data.lineItems)')
  })

  it('defaults deposit_pct to 0.5 (50%)', () => {
    const code = source()
    expect(code).toContain('data.depositPct ?? 0.5')
  })

  it('exports LineItem interface', () => {
    const code = source()
    expect(code).toContain('export interface LineItem')
    expect(code).toContain('problem: string')
    expect(code).toContain('description: string')
    expect(code).toContain('estimated_hours: number')
  })

  it('exports Quote interface', () => {
    expect(source()).toContain('export interface Quote')
  })

  it('exports QuoteStatus type', () => {
    expect(source()).toContain('export type QuoteStatus')
  })

  it('documents business rules in comments', () => {
    const code = source()
    expect(code).toContain('Decision #16')
    expect(code).toContain('Decision #18')
    expect(code).toContain('Decision #14')
  })

  it('recalculates totals when line items change on update', () => {
    const code = source()
    // updateQuote should recalculate when lineItems are provided
    expect(code).toContain('effectiveItems')
    expect(code).toContain('effectiveRate')
  })

  it('exports getActiveQuotesForEntities batch helper', () => {
    expect(source()).toContain('export async function getActiveQuotesForEntities')
  })

  it('getActiveQuotesForEntities returns empty for empty id list', () => {
    const code = source()
    // Short-circuit guard: D1 rejects `IN ()`, so we must bail early.
    expect(code).toMatch(/entityIds\.length === 0[\s\S]+?return result/)
  })

  it('getActiveQuotesForEntities prioritizes sent > accepted > draft', () => {
    const code = source()
    expect(code).toContain("WHEN 'sent'     THEN 0")
    expect(code).toContain("WHEN 'accepted' THEN 1")
    expect(code).toContain("WHEN 'draft'    THEN 2")
  })

  it('getActiveQuotesForEntities ignores terminal statuses (declined/expired/superseded)', () => {
    const code = source()
    // Only the three "active" statuses should appear in the SQL filter.
    expect(code).toContain("q.status IN ('sent', 'accepted', 'draft')")
  })

  it('getActiveQuotesForEntities is scoped to org_id', () => {
    const code = source()
    // The new query must bind org_id — prevent cross-tenant leakage (#399 class).
    expect(code).toMatch(/getActiveQuotesForEntities[\s\S]+?q\.org_id = \?/)
  })
})

describe('quotes: R2 storage helpers', () => {
  const source = () => readFileSync(resolve('src/lib/storage/r2.ts'), 'utf-8')

  it('r2.ts exists', () => {
    expect(existsSync(resolve('src/lib/storage/r2.ts'))).toBe(true)
  })

  it('exports revisioned SOW key helpers', () => {
    const code = source()
    expect(code).toContain('export function getSowRevisionUnsignedKey')
    expect(code).toContain('export function getSowRevisionSignedKey')
  })

  it('exports getPdf function', () => {
    expect(source()).toContain('export async function getPdf')
  })

  it('uses revisioned org-scoped storage keys for SOW artifacts', () => {
    const code = source()
    expect(code).toContain('orgs/${orgId}/quotes/${quoteId}/sow/${revisionId}/unsigned.pdf')
    expect(code).toContain('orgs/${orgId}/quotes/${quoteId}/sow/${revisionId}/signed.pdf')
  })

  it('stores content type as application/pdf', () => {
    const code = source()
    expect(code).toContain("'application/pdf'")
  })
})

describe('quotes: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/quotes/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/quotes/index.ts'))).toBe(true)
  })

  it('update endpoint exists at src/pages/api/admin/quotes/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/quotes/[id].ts'))).toBe(true)
  })

  it('create endpoint validates required fields', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/index.ts'), 'utf-8')
    expect(code).toContain('entity_id')
    expect(code).toContain('assessment_id')
    expect(code).toContain('line_items')
    expect(code).toContain('rate')
  })

  it('create endpoint calls createQuote', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/index.ts'), 'utf-8')
    expect(code).toContain('createQuote')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('update endpoint handles generate-pdf action', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/[id].ts'), 'utf-8')
    expect(code).toContain('generate-pdf')
    expect(code).toContain('createSOWRevisionForQuote')
  })

  it('update endpoint handles update action', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/[id].ts'), 'utf-8')
    expect(code).toContain('updateQuote')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/quotes/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/quotes/[id].ts'), 'utf-8')
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })
})

describe('quotes: repeat-quote flow (#472)', () => {
  const dalSource = () => readFileSync(resolve('src/lib/db/quotes.ts'), 'utf-8')
  const apiSource = () =>
    readFileSync(resolve('src/pages/api/admin/entities/[id]/quotes.ts'), 'utf-8')
  const entityPageSource = () =>
    readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')

  it('exports hasOpenQuoteForEntity helper', () => {
    expect(dalSource()).toContain('export async function hasOpenQuoteForEntity')
  })

  it('OPEN_QUOTE_STATUSES covers draft and sent', () => {
    const code = dalSource()
    expect(code).toContain('OPEN_QUOTE_STATUSES')
    expect(code).toMatch(/OPEN_QUOTE_STATUSES[^=]*=\s*\[[^\]]*'draft'[^\]]*'sent'/)
  })

  it('createQuote accepts optional parentQuoteId', () => {
    const code = dalSource()
    expect(code).toContain('parentQuoteId?: string | null')
    expect(code).toContain('parent_quote_id')
  })

  it('createQuote does not mutate the parent quote status', () => {
    // Supersede is an explicit admin action, not a side effect of create.
    const code = dalSource()
    // No UPDATE ... SET status = 'superseded' inside createQuote body.
    const createFn = code.slice(code.indexOf('export async function createQuote'))
    const endIdx = createFn.indexOf('export async function updateQuote')
    const body = createFn.slice(0, endIdx)
    expect(body).not.toMatch(/UPDATE\s+quotes[\s\S]*superseded/i)
  })

  it('new-quote API endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/entities/[id]/quotes.ts'))).toBe(true)
  })

  it('new-quote endpoint requires admin session', () => {
    expect(apiSource()).toContain("session.role !== 'admin'")
  })

  it('new-quote endpoint gates on stage (signal through proposing)', () => {
    const code = apiSource()
    expect(code).toContain("'signal'")
    expect(code).toContain("'prospect'")
    expect(code).toContain("'meetings'")
    expect(code).toContain("'proposing'")
  })

  it('new-quote endpoint rejects when an open quote exists', () => {
    expect(apiSource()).toContain('hasOpenQuoteForEntity')
  })

  it('new-quote endpoint reuses the most recent assessment', () => {
    const code = apiSource()
    expect(code).toContain('listAssessments')
    // Most-recent comes from listAssessments order; first element.
    expect(code).toContain('assessments[0]')
  })

  it('new-quote endpoint creates an empty draft shell', () => {
    const code = apiSource()
    expect(code).toContain('createQuote')
    expect(code).toContain('lineItems: []')
  })

  it('new-quote endpoint accepts optional parent_quote_id without auto-superseding', () => {
    const code = apiSource()
    expect(code).toContain('parent_quote_id')
    expect(code).toContain('parentQuoteId')
    // Parent status mutation is NOT part of this endpoint.
    expect(code).not.toContain("'superseded'")
  })

  it('entity detail page computes showNewQuoteButton with correct preconditions', () => {
    const code = entityPageSource()
    expect(code).toContain('showNewQuoteButton')
    expect(code).toContain('hasOpenQuoteForEntity')
    expect(code).toContain('newQuoteStages')
    expect(code).toContain("'proposing'")
  })

  it('entity detail page renders button behind showNewQuoteButton guard', () => {
    const code = entityPageSource()
    // Button is inside a conditional block driven by the computed guard.
    expect(code).toMatch(/showNewQuoteButton\s*&&/)
    expect(code).toContain('New quote')
  })
})

describe('quotes: email template', () => {
  const source = () => readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')

  it('exports quoteSentEmailHtml function', () => {
    expect(source()).toContain('export function quoteSentEmailHtml')
  })

  it('quote sent email includes proposal language', () => {
    const code = source()
    expect(code).toContain('Your proposal from ${BRAND_NAME} is ready for review')
  })

  it('quote sent email includes portal link', () => {
    const code = source()
    expect(code).toContain('View Your Proposal')
    expect(code).toContain('portalUrl')
  })
})
