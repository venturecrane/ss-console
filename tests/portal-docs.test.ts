import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('portal: engagement progress page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/portal/engagement/index.astro'), 'utf-8') +
    readFileSync(resolve('src/layouts/PortalShell.astro'), 'utf-8')

  it('engagement progress page exists', () => {
    expect(existsSync(resolve('src/pages/portal/engagement/index.astro'))).toBe(true)
  })

  it('loads engagement data via listEngagements', () => {
    expect(source()).toContain('resolvePortalOfferings')
  })

  it('loads milestones via listMilestones', () => {
    expect(source()).toContain('listMilestones')
  })

  it('resolves entity via getPortalClient (Clerk-aware signature)', () => {
    // After PR #906 the portal session resolver takes Astro.locals
    // (Clerk-aware) instead of (userId, orgId). session.userId is
    // gone from portal pages.
    const code = source()
    expect(code).toContain('getPortalClient(env.DB, Astro.locals)')
  })

  it('separates terminal engagements from the active one (offerings resolver)', () => {
    // The completed/cancelled filter moved into the shared offerings
    // resolver during the portal IA rebuild; the page renders active and
    // past engagements from its output.
    const resolver = readFileSync(resolve('src/lib/portal/offerings.ts'), 'utf-8')
    expect(resolver).toContain("'completed'")
    expect(resolver).toContain("'cancelled'")
    expect(source()).toContain('pastEngagements')
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
  const source = () =>
    readFileSync(resolve('src/pages/portal/engagement/documents/index.astro'), 'utf-8') +
    readFileSync(resolve('src/layouts/PortalShell.astro'), 'utf-8')

  it('documents page exists', () => {
    expect(existsSync(resolve('src/pages/portal/engagement/documents/index.astro'))).toBe(true)
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
    // After PR #906 orgId is resolved from the Clerk-bridged local user
    // (portalData.user.org_id) rather than the magic-link session.
    const code = source()
    expect(code).toContain('portalData.user.org_id')
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

  it('verifies portal session via Clerk identity bridge', () => {
    // Portal API routes now authenticate via getPortalClient (Clerk-aware)
    // rather than inspecting session.role. Unauthenticated requests get
    // 401; authenticated-but-unprovisioned get 403 Forbidden.
    const code = source()
    expect(code).toContain('getPortalClient(env.DB, locals)')
    expect(code).toContain('unauthorized')
  })

  it('prevents path traversal with .. check', () => {
    const code = source()
    expect(code).toContain("'..'")
    expect(code).toContain("'//'")
    expect(code).toContain('Forbidden')
  })

  it('verifies key starts with org prefix', () => {
    // After PR #906 the orgId for path-prefix checks is sourced from
    // portalData.user.org_id (resolved via the Clerk bridge), not the
    // magic-link session.
    const code = source()
    expect(code).toContain('portalData.user.org_id')
    expect(code).toContain('startsWith')
  })

  it('verifies key belongs to client engagement or quote', () => {
    const code = source()
    expect(code).toContain('isEngagementDoc')
    expect(code).toContain('isQuoteDoc')
    expect(code).toContain('listEngagements')
  })

  it('accepts SOW revision keys under the orgs/{orgId}/quotes/ prefix', () => {
    // SOW revisions are stored at `orgs/{orgId}/quotes/{qid}/sow/...` per
    // getSowRevisionSignedKey(). The handler must not reject these as off-org.
    // After PR #906 the orgId interpolation uses portalData.user.org_id.
    const code = source()
    expect(code).toContain('orgs/${portalData.user.org_id}/')
    expect(code).toContain('orgs/${portalData.user.org_id}/quotes/${qid}/')
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
