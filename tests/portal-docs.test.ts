import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('portal: engagement progress page', () => {
  const source = () => readFileSync(resolve('src/pages/portal/engagement/index.astro'), 'utf-8')

  it('engagement progress page exists', () => {
    expect(existsSync(resolve('src/pages/portal/engagement/index.astro'))).toBe(true)
  })

  it('loads engagement data via listEngagements', () => {
    expect(source()).toContain('listEngagements')
  })

  it('loads milestones via listMilestones', () => {
    expect(source()).toContain('listMilestones')
  })

  it('resolves entity via getPortalClient', () => {
    const code = source()
    expect(code).toContain('getPortalClient')
    expect(code).toContain('session.userId')
  })

  it('filters out completed and cancelled engagements', () => {
    const code = source()
    expect(code).toContain("'completed'")
    expect(code).toContain("'cancelled'")
  })

  it('shows milestone status indicators for all states', () => {
    const code = source()
    expect(code).toContain('pending')
    expect(code).toContain('in_progress')
    expect(code).toContain('completed')
    expect(code).toContain('skipped')
  })

  it('displays scope summary', () => {
    expect(source()).toContain('scope_summary')
  })

  it('displays timeline information', () => {
    const code = source()
    expect(code).toContain('start_date')
    expect(code).toContain('estimated_end')
  })

  it('shows empty state when no active engagement', () => {
    expect(source()).toContain('No active engagement')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('portal: documents page', () => {
  const source = () => readFileSync(resolve('src/pages/portal/documents/index.astro'), 'utf-8')

  it('documents page exists', () => {
    expect(existsSync(resolve('src/pages/portal/documents/index.astro'))).toBe(true)
  })

  it('lists R2 documents via listDocuments', () => {
    expect(source()).toContain('listDocuments')
  })

  it('includes SOW PDF from quote', () => {
    const code = source()
    expect(code).toContain('getSOWStateForQuote')
    expect(code).toContain('downloadableRevision')
    expect(code).toContain('Statement of Work')
  })

  it('scopes document listing to org/engagement prefix', () => {
    const code = source()
    expect(code).toContain('session.orgId')
    expect(code).toContain('engagement.id')
    expect(code).toContain('/docs/')
  })

  it('shows empty state when no documents', () => {
    expect(source()).toContain('Documents will appear here as your engagement progresses')
  })

  it('provides download links to document API', () => {
    expect(source()).toContain('/api/portal/documents/')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('portal: document download route', () => {
  const source = () => readFileSync(resolve('src/pages/api/portal/documents/[...key].ts'), 'utf-8')

  it('document download route exists', () => {
    expect(existsSync(resolve('src/pages/api/portal/documents/[...key].ts'))).toBe(true)
  })

  it('verifies portal session', () => {
    const code = source()
    expect(code).toContain("session.role !== 'client'")
    expect(code).toContain('Unauthorized')
  })

  it('prevents path traversal with .. check', () => {
    const code = source()
    expect(code).toContain("'..'")
    expect(code).toContain("'//'")
    expect(code).toContain('Forbidden')
  })

  it('verifies key starts with org prefix', () => {
    const code = source()
    expect(code).toContain('session.orgId')
    expect(code).toContain('startsWith')
  })

  it('verifies key belongs to client engagement or quote', () => {
    const code = source()
    expect(code).toContain('isEngagementDoc')
    expect(code).toContain('isQuoteDoc')
    expect(code).toContain('listEngagements')
  })

  it('streams document from R2', () => {
    expect(source()).toContain('streamDocument')
  })

  it('sets Content-Disposition inline for PDFs', () => {
    const code = source()
    expect(code).toContain('inline')
    expect(code).toContain('attachment')
    expect(code).toContain('Content-Disposition')
  })

  it('sets Content-Type based on file extension', () => {
    const code = source()
    expect(code).toContain('Content-Type')
    expect(code).toContain('application/pdf')
    expect(code).toContain('application/octet-stream')
  })
})

describe('R2 helpers: listDocuments and streamDocument', () => {
  const source = () => readFileSync(resolve('src/lib/storage/r2.ts'), 'utf-8')

  it('exports listDocuments function', () => {
    expect(source()).toContain('export async function listDocuments')
  })

  it('exports streamDocument function', () => {
    expect(source()).toContain('export async function streamDocument')
  })

  it('listDocuments uses prefix parameter', () => {
    const code = source()
    expect(code).toContain('prefix')
    expect(code).toContain('r2.list')
  })

  it('streamDocument returns R2 object', () => {
    const code = source()
    expect(code).toContain('r2.get')
  })
})
