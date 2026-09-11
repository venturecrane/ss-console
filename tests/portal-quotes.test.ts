/**
 * Behavioural tests for the portal's proposal surface: the portal quote
 * reads (src/lib/db/quotes.ts), the session resolver
 * (src/lib/portal/session.ts), the detail reader
 * (src/lib/portal/quote-detail.ts), and the SOW download route.
 *
 * Until 2026-09-11 this file matched source text (review 2026-09-10, Testing
 * 3). The cross-org paths of the portal reads are covered in
 * tests/portal/tenant-scoping.cross-org.test.ts; the Clerk bridge behind the
 * session resolver in tests/clerk-bridge.test.ts; the portal API auth gate in
 * tests/middleware-behavior.test.ts. The Astro pages have no handler to
 * invoke; the policy guards over them are at the end, each with its reason.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { createQuote, getQuoteForEntity, listQuotesForEntity } from '../src/lib/db/quotes'
import { getPortalClient } from '../src/lib/portal/session'
import { loadPortalQuoteDetail } from '../src/lib/portal/quote-detail'
import { GET } from '../src/pages/api/portal/quotes/[id]/sow'
import {
  bindEnv,
  memoryBucket,
  migratedDb,
  portalLocals,
  seedAssessment,
  seedEntity,
  seedOrg,
  seedPortalUser,
  type MemoryBucket,
} from './_stubs/behavioural'
import { ORG_ID } from '../src/lib/constants'

// The Clerk bridge binds a portal user to an entity only under the SMD tenant.
const ORG = ORG_ID
const ENT = 'ent-a'
const ENT_OTHER = 'ent-other'
const ASSESS = 'assess-a'
const CLERK_ID = 'user_clerk_portal'
const USER_ID = 'u-portal'

async function seedAll(db: D1Database) {
  await seedOrg(db, ORG)
  await seedEntity(db, { id: ENT, orgId: ORG, stage: 'proposing', name: 'Alpha Plumbing' })
  await seedEntity(db, { id: ENT_OTHER, orgId: ORG, stage: 'proposing' })
  await seedAssessment(db, { id: ASSESS, orgId: ORG, entityId: ENT })
  await seedAssessment(db, { id: 'assess-other', orgId: ORG, entityId: ENT_OTHER })
}

async function quoteIn(db: D1Database, status: string, entityId = ENT, assessmentId = ASSESS) {
  const quote = await createQuote(db, ORG, { entityId, assessmentId, lineItems: [], rate: 175 })
  await db.prepare('UPDATE quotes SET status = ? WHERE id = ?').bind(status, quote.id).run()
  return quote.id
}

async function seedRevision(
  db: D1Database,
  quoteId: string,
  id: string,
  status: string,
  signedKey: string | null = null
) {
  await db
    .prepare(
      `INSERT INTO sow_revisions (id, org_id, quote_id, quote_version, sow_number, status, unsigned_storage_key, signed_storage_key, checksum_sha256, rendered_by, rendered_at)
       VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'abc', 'admin', '2026-09-01T00:00:00Z')`
    )
    .bind(id, ORG, quoteId, `SOW-${id}`, status, `k/${id}/unsigned.pdf`, signedKey)
    .run()
}

describe('portal quote reads: what a client can see', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  it('lists sent, accepted, declined and expired quotes; drafts and superseded quotes stay internal', async () => {
    const visible = new Set<string>()
    for (const status of ['sent', 'accepted', 'declined', 'expired'])
      visible.add(await quoteIn(db, status))
    const draft = await quoteIn(db, 'draft')
    const superseded = await quoteIn(db, 'superseded')
    await quoteIn(db, 'sent', ENT_OTHER, 'assess-other')

    const listed = await listQuotesForEntity(db, ORG, ENT)
    expect(new Set(listed.map((q) => q.id))).toEqual(visible)
    expect(await getQuoteForEntity(db, ORG, ENT, draft)).toBeNull()
    expect(await getQuoteForEntity(db, ORG, ENT, superseded)).toBeNull()
    expect((await getQuoteForEntity(db, ORG, ENT, [...visible][0]))?.id).toBe([...visible][0])
  })

  it('a visible quote asked for under another entity of the same org is not found', async () => {
    const sent = await quoteIn(db, 'sent')
    expect(await getQuoteForEntity(db, ORG, ENT_OTHER, sent)).toBeNull()
  })
})

describe('getPortalClient: the Clerk seam', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  it('no Clerk session resolves to null', async () => {
    expect(
      await getPortalClient(db, portalLocals({ clerkUserId: null }) as unknown as App.Locals)
    ).toBeNull()
  })

  it('a signed-in user bound to an entity through users.entity_id resolves that entity', async () => {
    await seedPortalUser(db, { id: USER_ID, orgId: ORG, clerkUserId: CLERK_ID, entityId: ENT })
    const context = await getPortalClient(
      db,
      portalLocals({ clerkUserId: CLERK_ID }) as unknown as App.Locals
    )
    expect(context?.user.id).toBe(USER_ID)
    expect(context?.client?.id).toBe(ENT)
  })

  it('a signed-in user with no binding gets a user row and client null (no entity is invented)', async () => {
    const context = await getPortalClient(
      db,
      portalLocals({ clerkUserId: 'user_new', email: 'new@example.com' }) as unknown as App.Locals
    )
    expect(context?.user.clerk_user_id).toBe('user_new')
    expect(context?.client).toBeNull()
  })
})

describe('loadPortalQuoteDetail', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  it('returns null for a quote the client cannot see', async () => {
    const draft = await quoteIn(db, 'draft')
    expect(await loadPortalQuoteDetail(db, ORG, ENT, draft)).toBeNull()
  })

  it('carries the quote, its SOW state, the engagement summary, and no superseding quote by default', async () => {
    const sent = await quoteIn(db, 'sent')
    await seedRevision(db, sent, 'rev-1', 'sent')
    await db
      .prepare(
        `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, consultant_name, consultant_phone, next_touchpoint_label)
         VALUES ('eng-1', ?, ?, ?, 'active', 'Sam', '602-555-0100', 'Kickoff walk-through')`
      )
      .bind(ORG, ENT, sent)
      .run()

    const detail = await loadPortalQuoteDetail(db, ORG, ENT, sent)
    expect(detail?.quote.id).toBe(sent)
    expect(detail?.sowState.downloadableRevision?.id).toBe('rev-1')
    expect(detail?.sowState.openSignatureRequest).toBeNull()
    expect(detail?.engagement).toEqual({
      id: 'eng-1',
      consultant_name: 'Sam',
      consultant_phone: '602-555-0100',
      next_touchpoint_label: 'Kickoff walk-through',
    })
    expect(detail?.superseding).toBeNull()
  })

  it('points a declined quote at the quote that superseded it, by parent link or by a newer sent version', async () => {
    const declined = await quoteIn(db, 'declined')
    const byParent = await quoteIn(db, 'sent')
    await db
      .prepare('UPDATE quotes SET parent_quote_id = ? WHERE id = ?')
      .bind(declined, byParent)
      .run()
    expect((await loadPortalQuoteDetail(db, ORG, ENT, declined))?.superseding).toEqual({
      id: byParent,
    })

    const expired = await quoteIn(db, 'expired')
    const newer = await quoteIn(db, 'sent')
    await db.prepare('UPDATE quotes SET version = 3 WHERE id = ?').bind(newer).run()
    await db.prepare('UPDATE quotes SET parent_quote_id = NULL WHERE id = ?').bind(byParent).run()
    const superseding = (await loadPortalQuoteDetail(db, ORG, ENT, expired))?.superseding
    expect(superseding?.id).toBe(newer)
  })
})

describe('GET /api/portal/quotes/[id]/sow', () => {
  let db: D1Database
  let storage: MemoryBucket

  const call = (quoteId: string | undefined, clerkUserId: string | null = CLERK_ID) =>
    GET({
      locals: portalLocals({ clerkUserId }),
      params: { id: quoteId },
    } as unknown as Parameters<typeof GET>[0])

  beforeEach(async () => {
    db = await migratedDb()
    storage = memoryBucket()
    await seedAll(db)
    await seedPortalUser(db, { id: USER_ID, orgId: ORG, clerkUserId: CLERK_ID, entityId: ENT })
    bindEnv({ DB: db, STORAGE: storage.bucket })
  })

  it('400 without a quote id, 401 signed out, 403 for a user with no entity', async () => {
    expect((await call(undefined)).status).toBe(400)
    expect((await call('q', null)).status).toBe(401)
    await seedPortalUser(db, {
      id: 'u-unbound',
      orgId: ORG,
      clerkUserId: 'user_unbound',
      entityId: null,
    })
    const unbound = await call('q', 'user_unbound')
    expect(unbound.status).toBe(403)
    expect(await unbound.json()).toMatchObject({ message: 'Client not found.' })
  })

  it('404 for a quote the client cannot see, for a quote with no downloadable revision, and for a missing object', async () => {
    const draft = await quoteIn(db, 'draft')
    expect((await call(draft)).status).toBe(404)

    const sent = await quoteIn(db, 'sent')
    expect((await call(sent)).status).toBe(404)

    await seedRevision(db, sent, 'rev-1', 'sent')
    const missing = await call(sent)
    expect(missing.status).toBe(404)
    expect(await missing.json()).toMatchObject({ error: 'not_found' })
  })

  it('streams the signed PDF when one exists, else the unsigned one, as an attachment', async () => {
    const sent = await quoteIn(db, 'sent')
    await seedRevision(db, sent, 'rev-1', 'sent')
    await storage.bucket.put('k/rev-1/unsigned.pdf', new TextEncoder().encode('unsigned'))
    const unsigned = await call(sent)
    expect(unsigned.status).toBe(200)
    expect(unsigned.headers.get('Content-Type')).toBe('application/pdf')
    expect(unsigned.headers.get('Content-Disposition')).toContain('attachment')
    expect(await unsigned.text()).toBe('unsigned')

    await seedRevision(db, sent, 'rev-2', 'signed', 'k/rev-2/signed.pdf')
    await storage.bucket.put('k/rev-2/signed.pdf', new TextEncoder().encode('signed'))
    expect(await (await call(sent)).text()).toBe('signed')
  })
})

// The portal pages are Astro; nothing here can invoke them. What is kept is
// policy, not composition, and each guard names the rule it enforces.
describe('portal proposal pages: policy guards', () => {
  const pages = {
    home: readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8'),
    engagement: readFileSync(resolve('src/pages/portal/engagement/index.astro'), 'utf-8'),
    proposal: readFileSync(resolve('src/pages/portal/engagement/proposals/[id].astro'), 'utf-8'),
    sections: readFileSync(resolve('src/components/portal/QuoteProposalSections.astro'), 'utf-8'),
  }
  const shell = readFileSync(resolve('src/layouts/PortalShell.astro'), 'utf-8')
  const template = (source: string) => source.split('---').slice(2).join('---')

  it('never shows the client an hourly rate or an hours column (Decision #16)', () => {
    for (const [name, source] of Object.entries(pages)) {
      const html = template(source)
      expect(html, name).not.toMatch(
        /quote\.rate|item\.estimated_hours|\/hr|hourly|>Hours<|Total Hours/
      )
    }
  })

  it('never promises that someone "will reach out" (#398: no uncontracted future behaviour)', () => {
    for (const [name, source] of Object.entries(pages)) {
      expect(source, name).not.toMatch(/will reach out/i)
    }
  })

  it('the proposal page renders scope from authored deliverables only, never from line items (#398)', () => {
    const combined = `${pages.proposal}\n${pages.sections}`
    expect(combined).toContain('parseDeliverables')
    expect(combined).not.toMatch(/lineItems\.map|getProblemLabel/)
  })

  it('the portal shell is noindex and mobile-scaled for every page', () => {
    expect(shell).toContain('noindex')
    expect(shell).toContain('width=device-width, initial-scale=1.0')
  })
})
