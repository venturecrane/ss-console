import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('portal quotes: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/quotes.ts'), 'utf-8')

  it('exports listQuotesForEntity function', () => {
    expect(source()).toContain('export async function listQuotesForEntity')
  })

  it('exports getQuoteForEntity function', () => {
    expect(source()).toContain('export async function getQuoteForEntity')
  })

  it('listQuotesForEntity scopes by entity_id (not org_id)', () => {
    const code = source()
    // Should use entity_id = ? without org_id in the portal function
    expect(code).toContain('SELECT * FROM quotes WHERE entity_id = ?')
  })

  it('getQuoteForEntity scopes by entity_id and quote_id (not org_id)', () => {
    const code = source()
    expect(code).toContain('SELECT * FROM quotes WHERE id = ? AND entity_id = ?')
  })

  it('portal queries filter to visible statuses only (sent, accepted, declined, expired)', () => {
    const code = source()
    expect(code).toContain("'sent', 'accepted', 'declined', 'expired'")
    // Draft and superseded should not be visible
    expect(code).toContain('PORTAL_VISIBLE_STATUSES')
  })

  it('portal functions do not use org_id parameter', () => {
    const code = source()
    // Extract just the listQuotesForEntity function signature
    const listMatch = code.match(/export async function listQuotesForEntity\([^)]+\)/)
    expect(listMatch).toBeTruthy()
    expect(listMatch![0]).not.toContain('orgId')

    // Extract just the getQuoteForEntity function signature
    const getMatch = code.match(/export async function getQuoteForEntity\([^)]+\)/)
    expect(getMatch).toBeTruthy()
    expect(getMatch![0]).not.toContain('orgId')
  })
})

describe('portal quotes: session helper', () => {
  it('portal session helper exists at src/lib/portal/session.ts', () => {
    expect(existsSync(resolve('src/lib/portal/session.ts'))).toBe(true)
  })

  it('exports getPortalClient function', () => {
    const code = readFileSync(resolve('src/lib/portal/session.ts'), 'utf-8')
    expect(code).toContain('export async function getPortalClient')
  })

  it('resolves client via users.client_id', () => {
    const code = readFileSync(resolve('src/lib/portal/session.ts'), 'utf-8')
    expect(code).toContain('client_id')
    expect(code).toContain("role = 'client'")
  })
})

describe('portal quotes: dashboard', () => {
  const source = () => readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')

  it('portal dashboard exists', () => {
    expect(existsSync(resolve('src/pages/portal/index.astro'))).toBe(true)
  })

  it('shows client business name', () => {
    expect(source()).toContain('client.business_name')
  })

  it('shows pending quotes section', () => {
    const code = source()
    expect(code).toContain('pendingQuotes')
    expect(code).toContain('proposal')
  })

  it('loads quotes via listQuotesForEntity', () => {
    expect(source()).toContain('listQuotesForEntity')
  })

  it('resolves client via getPortalClient', () => {
    expect(source()).toContain('getPortalClient')
  })

  it('shows active engagement status', () => {
    const code = source()
    expect(code).toContain('activeEngagement')
    expect(code).toContain('Current Engagement')
  })

  it('shows current milestone', () => {
    expect(source()).toContain('currentMilestone')
  })

  it('shows recent activity feed', () => {
    expect(source()).toContain('recentActivity')
    expect(source()).toContain('Recent Activity')
  })

  it('has quick links to quotes section', () => {
    expect(source()).toContain('/portal/quotes')
  })

  it('has mobile viewport meta tag', () => {
    expect(source()).toContain('width=device-width, initial-scale=1.0')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })

  it('does not expose hourly rates', () => {
    const code = source()
    expect(code).not.toContain('rate')
    expect(code).not.toContain('/hr')
    expect(code).not.toContain('hourly')
  })
})

describe('portal quotes: quote list page', () => {
  const source = () => readFileSync(resolve('src/pages/portal/quotes/index.astro'), 'utf-8')

  it('quote list page exists', () => {
    expect(existsSync(resolve('src/pages/portal/quotes/index.astro'))).toBe(true)
  })

  it('loads quotes via listQuotesForEntity', () => {
    expect(source()).toContain('listQuotesForEntity')
  })

  it('resolves client via getPortalClient', () => {
    expect(source()).toContain('getPortalClient')
  })

  it('displays status badges', () => {
    const code = source()
    expect(code).toContain('statusColorMap')
    expect(code).toContain('bg-blue-100')
    expect(code).toContain('bg-green-100')
    expect(code).toContain('bg-red-100')
    expect(code).toContain('bg-amber-100')
  })

  it('displays total price for each quote', () => {
    expect(source()).toContain('quote.total_price')
  })

  it('displays date sent', () => {
    expect(source()).toContain('quote.sent_at')
  })

  it('shows Review & Sign CTA for sent quotes', () => {
    const code = source()
    expect(code).toContain('Review')
    expect(code).toContain('Sign')
    expect(code).toContain("quote.status === 'sent'")
  })

  it('links to detail page', () => {
    expect(source()).toContain('/portal/quotes/${quote.id}')
  })

  it('has mobile viewport meta tag', () => {
    expect(source()).toContain('width=device-width, initial-scale=1.0')
  })

  it('does not expose hourly rates', () => {
    const code = source()
    expect(code).not.toContain('.rate')
    expect(code).not.toContain('/hr')
    expect(code).not.toContain('hourly')
    expect(code).not.toContain('total_hours')
    expect(code).not.toContain('estimated_hours')
  })

  it('does not expose per-item pricing', () => {
    const code = source()
    expect(code).not.toContain('item.estimated_hours')
    expect(code).not.toContain('per item')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('portal quotes: quote detail page', () => {
  const source = () => readFileSync(resolve('src/pages/portal/quotes/[id].astro'), 'utf-8')

  it('quote detail page exists', () => {
    expect(existsSync(resolve('src/pages/portal/quotes/[id].astro'))).toBe(true)
  })

  it('loads quote via getQuoteForEntity', () => {
    expect(source()).toContain('getQuoteForEntity')
  })

  it('resolves client via getPortalClient', () => {
    expect(source()).toContain('getPortalClient')
  })

  it('displays scope with problem descriptions only', () => {
    const code = source()
    expect(code).toContain('lineItems')
    expect(code).toContain('getProblemLabel')
    expect(code).toContain('item.description')
  })

  it('does NOT show hours column in scope table', () => {
    const code = source()
    // The scope section should not reference hours
    expect(code).not.toMatch(/item\.estimated_hours/)
    // Should not have a Hours column header in the scope section
    expect(code).not.toContain('>Hours<')
  })

  it('does NOT show hourly rate', () => {
    const code = source()
    // Extract HTML template (after the closing --- frontmatter delimiter)
    const parts = code.split('---')
    const htmlTemplate = parts.slice(2).join('---')
    expect(htmlTemplate).not.toContain('quote.rate')
    expect(htmlTemplate).not.toContain('/hr')
    expect(htmlTemplate).not.toContain('hourly')
    expect(htmlTemplate).not.toContain('Rate')
  })

  it('does NOT show total_hours to client', () => {
    const code = source()
    // total_hours is used internally for 3-milestone calculation but not displayed
    expect(code).not.toContain('>total_hours<')
    expect(code).not.toContain('Total Hours')
  })

  it('displays total project price', () => {
    expect(source()).toContain('quote.total_price')
  })

  it('displays payment terms with deposit and balance', () => {
    const code = source()
    expect(code).toContain('depositAmount')
    expect(code).toContain('balanceAmount')
    expect(code).toContain('Payment Terms')
  })

  it('handles 3-milestone payment for 40+ hour engagements', () => {
    const code = source()
    expect(code).toContain('isThreeMilestone')
    expect(code).toContain('total_hours >= 40')
  })

  it('includes SOW PDF download link', () => {
    const code = source()
    expect(code).toContain('/api/portal/quotes/')
    expect(code).toContain('/sow')
    expect(code).toContain('Download SOW')
  })

  it('shows signing iframe when signwell_doc_id exists', () => {
    const code = source()
    expect(code).toContain('signwell_doc_id')
    expect(code).toContain('iframe')
    expect(code).toContain('signwell.com')
  })

  it('shows "proposal is being prepared" when no signwell_doc_id (UX-004)', () => {
    expect(source()).toContain('Your proposal is being prepared')
  })

  it('shows Review & Sign section for sent quotes', () => {
    const code = source()
    expect(code).toContain('Review & Sign')
    expect(code).toContain('isSent')
  })

  it('shows accepted confirmation state', () => {
    const code = source()
    expect(code).toContain('Proposal accepted')
    expect(code).toContain("quote.status === 'accepted'")
  })

  it('shows declined and expired states', () => {
    const code = source()
    expect(code).toContain("quote.status === 'declined'")
    expect(code).toContain("quote.status === 'expired'")
  })

  it('has mobile viewport meta tag', () => {
    expect(source()).toContain('width=device-width, initial-scale=1.0')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('portal quotes: SOW download API route', () => {
  it('SOW download route exists at src/pages/api/portal/quotes/[id]/sow.ts', () => {
    expect(existsSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'))).toBe(true)
  })

  it('verifies portal session (client role)', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain("session.role !== 'client'")
  })

  it('scopes quote to entity via getQuoteForEntity', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain('getQuoteForEntity')
    expect(code).toContain('getPortalClient')
  })

  it('verifies sow_path exists', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain('sow_path')
  })

  it('streams PDF with correct Content-Type', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain("'Content-Type': 'application/pdf'")
  })

  it('sets Content-Disposition for download', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain('Content-Disposition')
    expect(code).toContain('attachment')
  })

  it('returns 401 on unauthorized', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain('401')
  })

  it('returns 404 when quote not found', () => {
    const code = readFileSync(resolve('src/pages/api/portal/quotes/[id]/sow.ts'), 'utf-8')
    expect(code).toContain('404')
  })
})

describe('portal quotes: middleware handles /api/portal/* routes', () => {
  const source = () => readFileSync(resolve('src/middleware.ts'), 'utf-8')

  it('middleware detects portal API routes', () => {
    expect(source()).toContain("pathname.startsWith('/api/portal')")
  })

  it('portal API routes return 401 JSON on auth failure (not redirect)', () => {
    const code = source()
    // isPortalApiRoute should lead to JSON 401 response
    expect(code).toContain('isPortalApiRoute')
  })

  it('portal API routes included in protected route check', () => {
    const code = source()
    expect(code).toContain('isPortalApiRoute')
    expect(code).toContain('isProtectedRoute')
  })
})
