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
})

describe('quotes: R2 storage helpers', () => {
  const source = () => readFileSync(resolve('src/lib/storage/r2.ts'), 'utf-8')

  it('r2.ts exists', () => {
    expect(existsSync(resolve('src/lib/storage/r2.ts'))).toBe(true)
  })

  it('exports uploadPdf function', () => {
    expect(source()).toContain('export async function uploadPdf')
  })

  it('exports getPdfUrl function', () => {
    expect(source()).toContain('export function getPdfUrl')
  })

  it('exports getPdf function', () => {
    expect(source()).toContain('export async function getPdf')
  })

  it('uses structured key pattern with org scoping for PDFs', () => {
    const code = source()
    expect(code).toContain('/quotes/')
    expect(code).toContain('/sow.pdf')
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
    expect(code).toContain('renderSow')
    expect(code).toContain('uploadPdf')
  })

  it('update endpoint handles send action', () => {
    const code = readFileSync(resolve('src/pages/api/admin/quotes/[id].ts'), 'utf-8')
    expect(code).toContain("action === 'send'")
    expect(code).toContain('updateQuoteStatus')
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

describe('quotes: email template', () => {
  const source = () => readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')

  it('exports quoteSentEmailHtml function', () => {
    expect(source()).toContain('export function quoteSentEmailHtml')
  })

  it('quote sent email includes proposal language', () => {
    const code = source()
    expect(code).toContain('Your proposal from SMD Services is ready for review')
  })

  it('quote sent email includes portal link', () => {
    const code = source()
    expect(code).toContain('View Your Proposal')
    expect(code).toContain('portalUrl')
  })
})

describe('quotes: create form page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/quotes/new.astro'), 'utf-8')

  it('create page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/quotes/new.astro'))).toBe(true)
  })

  it('form posts to /api/admin/quotes', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/quotes"')
  })

  it('includes hidden client_id field', () => {
    expect(source()).toContain('name="client_id"')
  })

  it('includes assessment selection dropdown', () => {
    const code = source()
    expect(code).toContain('name="assessment_id"')
    expect(code).toContain('Select Assessment')
  })

  it('includes rate input with default $150', () => {
    const code = source()
    expect(code).toContain('name="rate"')
    expect(code).toContain('defaultRate')
  })

  it('includes deposit percentage input defaulting to 0.5', () => {
    const code = source()
    expect(code).toContain('name="deposit_pct"')
    expect(code).toContain('value="0.5"')
  })

  it('includes dynamic line item editor', () => {
    const code = source()
    expect(code).toContain('line-items-container')
    expect(code).toContain('add-line-item')
    expect(code).toContain('Add Line Item')
  })

  it('includes auto-calculated totals display', () => {
    const code = source()
    expect(code).toContain('total-hours')
    expect(code).toContain('total-price')
    expect(code).toContain('deposit-amount')
  })

  it('includes notes textarea', () => {
    expect(source()).toContain('name="notes"')
  })

  it('loads client data for display', () => {
    expect(source()).toContain('getClient')
  })

  it('filters assessments to completed and converted only', () => {
    const code = source()
    expect(code).toContain("a.status === 'completed'")
    expect(code).toContain("a.status === 'converted'")
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin/clients')
    expect(code).toContain('client.business_name')
    expect(code).toContain('New Quote')
  })

  it('serializes line items as JSON before submit', () => {
    const code = source()
    expect(code).toContain('line_items_json')
    expect(code).toContain('JSON.stringify')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('quotes: detail/edit page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/quotes/[quoteId].astro'), 'utf-8')

  it('detail page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/quotes/[quoteId].astro'))).toBe(true)
  })

  it('loads quote via getQuote', () => {
    expect(source()).toContain('getQuote')
  })

  it('loads client via getClient for breadcrumb', () => {
    expect(source()).toContain('getClient')
  })

  it('displays line items table', () => {
    const code = source()
    expect(code).toContain('lineItems')
    expect(code).toContain('Problem')
    expect(code).toContain('Description')
    expect(code).toContain('Hours')
  })

  it('displays pricing summary with total, deposit, and balance', () => {
    const code = source()
    expect(code).toContain('Total Hours')
    expect(code).toContain('Project Total')
    expect(code).toContain('Deposit')
    expect(code).toContain('Balance Due')
  })

  it('shows client sees project total only note (Decision #16)', () => {
    const code = source()
    expect(code).toContain('Client sees project total only')
    expect(code).toContain('Hourly rate is internal')
  })

  it('includes Generate SOW button for draft quotes', () => {
    const code = source()
    expect(code).toContain('Generate SOW')
    expect(code).toContain('generate-pdf')
  })

  it('includes Send to Client button when SOW exists and draft', () => {
    const code = source()
    expect(code).toContain('Send to Client')
    expect(code).toContain('value="send"')
  })

  it('shows SOW PDF status when sow_path exists', () => {
    const code = source()
    expect(code).toContain('SOW PDF generated')
    expect(code).toContain('sow_path')
  })

  it('shows expiration countdown when status is sent', () => {
    const code = source()
    expect(code).toContain('expirationText')
    expect(code).toContain('Expires in')
    expect(code).toContain('Expired')
  })

  it('displays current status badge with correct colors', () => {
    const code = source()
    expect(code).toContain('QUOTE_STATUSES')
    expect(code).toContain('statusColorMap')
  })

  it('includes edit form for draft quotes', () => {
    const code = source()
    expect(code).toContain('Edit Quote')
    expect(code).toContain('isDraft')
  })

  it('shows status timeline with dates', () => {
    const code = source()
    expect(code).toContain('quote.created_at')
    expect(code).toContain('quote.sent_at')
    expect(code).toContain('quote.expires_at')
    expect(code).toContain('quote.accepted_at')
  })

  it('shows success message after save', () => {
    const code = source()
    expect(code).toContain("get('saved')")
    expect(code).toContain('Quote updated successfully')
  })

  it('has breadcrumb back to client', () => {
    const code = source()
    expect(code).toContain('/admin/clients/${client.id}')
    expect(code).toContain('client.business_name')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('quotes: client detail page integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/[id].astro'), 'utf-8')

  it('client detail page imports listQuotes', () => {
    expect(source()).toContain('listQuotes')
  })

  it('client detail page loads quotes for this client', () => {
    const code = source()
    expect(code).toContain('listQuotes')
    expect(code).toContain('quotes')
  })

  it('client detail page has quotes section', () => {
    const code = source()
    expect(code).toContain('Quotes')
    expect(code).toContain('New Quote')
  })

  it('client detail page links to new quote form', () => {
    const code = source()
    expect(code).toContain('/quotes/new')
  })

  it('client detail page links to quote detail', () => {
    const code = source()
    expect(code).toContain('/quotes/${q.id}')
  })

  it('shows quote status in the list', () => {
    const code = source()
    expect(code).toContain('QUOTE_STATUSES')
    expect(code).toContain('q.status')
  })

  it('shows quote total price in the list', () => {
    const code = source()
    expect(code).toContain('q.total_price')
  })

  it('shows empty state when no quotes', () => {
    const code = source()
    expect(code).toContain('No quotes yet')
    expect(code).toContain('Create the first quote')
  })
})
