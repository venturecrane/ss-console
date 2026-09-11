/**
 * Behavioural tests for quotes: the data layer (src/lib/db/quotes.ts), the
 * R2 key helpers it stores, the two admin create routes, and the entity
 * detail loader's new-quote rule (#472).
 *
 * Until 2026-09-11 this file matched source text (review 2026-09-10, Testing
 * 3). Money math, the state machine and its two gates (authored content
 * before send, a signed artifact before acceptance), the open-quote rule and
 * the active-quote priority are all asserted on rows in a migrated D1. The
 * cross-org paths of getQuote, updateQuote and updateQuoteStatus are covered
 * in tests/db-cross-org.test.ts and not repeated here.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createQuote,
  getActiveQuotesForEntities,
  getQuote,
  hasOpenQuoteForEntity,
  listQuotes,
  QUOTE_STATUSES,
  updateQuote,
  updateQuoteStatus,
  VALID_TRANSITIONS,
  type QuoteStatus,
} from '../src/lib/db/quotes'
import { createContact } from '../src/lib/db/contacts'
import { getSowRevisionSignedKey, getSowRevisionUnsignedKey } from '../src/lib/storage/r2'
import { loadEntityDetailPage } from '../src/lib/admin/entity-detail-page'
import { POST as createRoute } from '../src/pages/api/admin/quotes/index'
import { POST as newQuoteRoute } from '../src/pages/api/admin/entities/[id]/quotes'
import {
  adminSession,
  bindEnv,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedAssessment,
  seedEntity,
  seedOrg,
} from './_stubs/behavioural'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A1 = 'ent-a1'
const ENT_A2 = 'ent-a2'
const ENT_B1 = 'ent-b1'
const ASSESS_A1 = 'assess-a1'
const ASSESS_A2 = 'assess-a2'
const ASSESS_B1 = 'assess-b1'

const LINE_ITEMS = [
  { problem: 'intake', description: 'Rebuild the intake form', estimated_hours: 10 },
  { problem: 'scheduling', description: 'Tidy the dispatch board', estimated_hours: 6 },
]
const AUTHORED = {
  schedule: [{ label: 'Week 1', body: 'We shadow the desk.' }],
  deliverables: [{ title: 'Intake form', body: 'One form, one queue.' }],
}

async function seedAll(db: D1Database) {
  await seedOrg(db, ORG_A)
  await seedOrg(db, ORG_B)
  await seedEntity(db, { id: ENT_A1, orgId: ORG_A, stage: 'prospect' })
  await seedEntity(db, { id: ENT_A2, orgId: ORG_A, stage: 'prospect' })
  await seedEntity(db, { id: ENT_B1, orgId: ORG_B, stage: 'prospect' })
  await seedAssessment(db, { id: ASSESS_A1, orgId: ORG_A, entityId: ENT_A1 })
  await seedAssessment(db, { id: ASSESS_A2, orgId: ORG_A, entityId: ENT_A2 })
  await seedAssessment(db, { id: ASSESS_B1, orgId: ORG_B, entityId: ENT_B1 })
}

const draftA1 = (db: D1Database, extra: Partial<Parameters<typeof createQuote>[2]> = {}) =>
  createQuote(db, ORG_A, {
    entityId: ENT_A1,
    assessmentId: ASSESS_A1,
    lineItems: LINE_ITEMS,
    rate: 175,
    ...extra,
  })

async function setStatus(db: D1Database, id: string, status: string) {
  await db.prepare('UPDATE quotes SET status = ? WHERE id = ?').bind(status, id).run()
}

async function stampUpdated(db: D1Database, id: string, updatedAt: string) {
  await db.prepare('UPDATE quotes SET updated_at = ? WHERE id = ?').bind(updatedAt, id).run()
}

/**
 * The acceptance guard reads signature_requests for a completed row with a
 * persisted signed artifact. FKs are enforced, so the whole chain is seeded:
 * contact -> sow_revision -> send_authorization -> signature_request.
 */
async function seedCompletedSignature(db: D1Database, orgId: string, quoteId: string) {
  const contact = await createContact(db, orgId, ENT_A1, { name: 'Signer', email: 's@example.com' })
  await db
    .prepare(
      `INSERT INTO sow_revisions (id, org_id, quote_id, quote_version, sow_number, status, unsigned_storage_key, checksum_sha256, rendered_by, rendered_at)
       VALUES ('rev-1', ?, ?, 1, 'SOW-202609-001', 'signed', 'k/unsigned.pdf', 'abc', 'admin', '2026-09-01T00:00:00Z')`
    )
    .bind(orgId, quoteId)
    .run()
  await db
    .prepare(
      `INSERT INTO sow_send_authorizations (id, org_id, quote_id, sow_revision_id, signer_contact_id, signer_snapshot_json, checksum_sha256, authorized_by, authorized_at)
       VALUES ('auth-1', ?, ?, 'rev-1', ?, '{}', 'abc', 'admin', '2026-09-01T00:00:00Z')`
    )
    .bind(orgId, quoteId, contact.id)
    .run()
  await db
    .prepare(
      `INSERT INTO signature_requests (id, org_id, quote_id, sow_revision_id, send_authorization_id, provider, provider_request_id, status, signer_snapshot_json, provider_payload_json, signed_storage_key)
       VALUES ('sig-1', ?, ?, 'rev-1', 'auth-1', 'signwell', 'sw-1', 'completed', '{}', '{}', 'k/signed.pdf')`
    )
    .bind(orgId, quoteId)
    .run()
}

describe('quotes data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  describe('createQuote', () => {
    it('freezes the rate and computes hours, price, and the default 50% deposit (Decisions #14, #16)', async () => {
      const quote = await draftA1(db)
      expect(quote).toMatchObject({
        org_id: ORG_A,
        entity_id: ENT_A1,
        assessment_id: ASSESS_A1,
        status: 'draft',
        version: 1,
        rate: 175,
        total_hours: 16,
        total_price: 2800,
        deposit_pct: 0.5,
        deposit_amount: 1400,
        parent_quote_id: null,
        sent_at: null,
        expires_at: null,
        accepted_at: null,
      })
      expect(JSON.parse(quote.line_items)).toEqual(LINE_ITEMS)
      expect(await getQuote(db, ORG_A, quote.id)).toEqual(quote)
    })

    it('an explicit deposit percentage is honoured', async () => {
      const quote = await draftA1(db, { depositPct: 0.25 })
      expect(quote.deposit_amount).toBe(700)
    })

    it('a repeat quote links its parent and leaves the parent status alone (#472)', async () => {
      const parent = await draftA1(db)
      await setStatus(db, parent.id, 'declined')
      const child = await draftA1(db, { parentQuoteId: parent.id })
      expect(child.parent_quote_id).toBe(parent.id)
      expect((await getQuote(db, ORG_A, parent.id))?.status).toBe('declined')
    })
  })

  describe('listQuotes', () => {
    it("lists the org's quotes most recently updated first, with an optional entity filter", async () => {
      const older = await draftA1(db)
      const newer = await createQuote(db, ORG_A, {
        entityId: ENT_A2,
        assessmentId: ASSESS_A2,
        lineItems: [],
        rate: 175,
      })
      await createQuote(db, ORG_B, {
        entityId: ENT_B1,
        assessmentId: ASSESS_B1,
        lineItems: [],
        rate: 175,
      })
      await stampUpdated(db, older.id, '2026-01-01T00:00:00.000Z')
      await stampUpdated(db, newer.id, '2026-02-01T00:00:00.000Z')

      expect((await listQuotes(db, ORG_A)).map((q) => q.id)).toEqual([newer.id, older.id])
      expect((await listQuotes(db, ORG_A, ENT_A1)).map((q) => q.id)).toEqual([older.id])
      expect(await listQuotes(db, ORG_A, ENT_B1)).toEqual([])
    })
  })

  describe('updateQuote', () => {
    it('a pricing change recalculates the totals and bumps the version', async () => {
      const quote = await draftA1(db)
      const repriced = await updateQuote(db, ORG_A, quote.id, {
        lineItems: [{ problem: 'intake', description: 'Form only', estimated_hours: 4 }],
        rate: 200,
        depositPct: 0.4,
      })
      expect(repriced).toMatchObject({
        version: 2,
        total_hours: 4,
        rate: 200,
        total_price: 800,
        deposit_pct: 0.4,
        deposit_amount: 320,
      })
    })

    it('a line-item-only change reprices at the frozen rate', async () => {
      const quote = await draftA1(db)
      const updated = await updateQuote(db, ORG_A, quote.id, {
        lineItems: [{ problem: 'intake', description: 'x', estimated_hours: 2 }],
      })
      expect(updated).toMatchObject({ total_hours: 2, rate: 175, total_price: 350 })
    })

    it('authored content is stored as JSON; an explicit null clears it; no fields leaves the version alone', async () => {
      const quote = await draftA1(db)
      const authored = await updateQuote(db, ORG_A, quote.id, {
        ...AUTHORED,
        engagementOverview: 'We rebuild the intake path together.',
      })
      expect(JSON.parse(authored!.schedule!)).toEqual(AUTHORED.schedule)
      expect(JSON.parse(authored!.deliverables!)).toEqual(AUTHORED.deliverables)
      expect(authored?.engagement_overview).toBe('We rebuild the intake path together.')
      expect(authored?.version).toBe(2)

      const cleared = await updateQuote(db, ORG_A, quote.id, { engagementOverview: null })
      expect(cleared?.engagement_overview).toBeNull()
      expect(await updateQuote(db, ORG_A, quote.id, {})).toEqual(cleared)
    })
  })

  describe('updateQuoteStatus', () => {
    it('the vocabulary and the state machine', () => {
      expect(QUOTE_STATUSES.map((s) => s.value)).toEqual([
        'draft',
        'sent',
        'accepted',
        'declined',
        'expired',
        'superseded',
      ])
      expect(VALID_TRANSITIONS).toEqual({
        draft: ['sent', 'superseded'],
        sent: ['accepted', 'declined', 'expired', 'superseded'],
        accepted: [],
        declined: [],
        expired: [],
        superseded: [],
      })
    })

    it('draft -> sent is refused until the schedule and deliverables are authored (#377)', async () => {
      const quote = await draftA1(db)
      await expect(updateQuoteStatus(db, ORG_A, quote.id, 'sent')).rejects.toThrow(
        'missing authored client-facing content (schedule, deliverables)'
      )
      expect((await getQuote(db, ORG_A, quote.id))?.status).toBe('draft')
    })

    it('draft -> sent stamps sent_at and a five-day expires_at (Decision #18)', async () => {
      const quote = await draftA1(db)
      await updateQuote(db, ORG_A, quote.id, AUTHORED)
      const before = Date.now()
      const sent = await updateQuoteStatus(db, ORG_A, quote.id, 'sent')
      expect(sent?.status).toBe('sent')
      const sentAt = Date.parse(sent!.sent_at!)
      expect(sentAt).toBeGreaterThanOrEqual(before - 1000)
      expect(Date.parse(sent!.expires_at!) - sentAt).toBe(5 * 24 * 60 * 60 * 1000)
    })

    it('sent -> accepted is refused without a completed signature request carrying a signed artifact', async () => {
      const quote = await draftA1(db)
      await setStatus(db, quote.id, 'sent')
      await expect(updateQuoteStatus(db, ORG_A, quote.id, 'accepted')).rejects.toThrow(
        'completed signed signature request'
      )
      expect((await getQuote(db, ORG_A, quote.id))?.status).toBe('sent')
    })

    it('sent -> accepted stamps accepted_at once the signed artifact exists', async () => {
      const quote = await draftA1(db)
      await setStatus(db, quote.id, 'sent')
      await seedCompletedSignature(db, ORG_A, quote.id)
      const before = Date.now()
      const accepted = await updateQuoteStatus(db, ORG_A, quote.id, 'accepted')
      expect(accepted?.status).toBe('accepted')
      expect(Date.parse(accepted!.accepted_at!)).toBeGreaterThanOrEqual(before - 1000)
    })

    it('refuses a move the table does not allow, naming both states', async () => {
      const quote = await draftA1(db)
      await expect(updateQuoteStatus(db, ORG_A, quote.id, 'accepted')).rejects.toThrow(
        'Invalid status transition: draft -> accepted'
      )
    })

    it('accepted, declined, expired and superseded are terminal', async () => {
      for (const terminal of ['accepted', 'declined', 'expired', 'superseded'] as const) {
        const quote = await draftA1(db)
        await setStatus(db, quote.id, terminal)
        for (const target of Object.keys(VALID_TRANSITIONS) as QuoteStatus[]) {
          await expect(updateQuoteStatus(db, ORG_A, quote.id, target)).rejects.toThrow(
            'none (terminal state)'
          )
        }
      }
    })
  })

  describe('hasOpenQuoteForEntity (#472)', () => {
    it('is true while a draft or sent quote exists, false once every quote is terminal, and org-scoped', async () => {
      expect(await hasOpenQuoteForEntity(db, ORG_A, ENT_A1)).toBe(false)
      const quote = await draftA1(db)
      expect(await hasOpenQuoteForEntity(db, ORG_A, ENT_A1)).toBe(true)
      await setStatus(db, quote.id, 'sent')
      expect(await hasOpenQuoteForEntity(db, ORG_A, ENT_A1)).toBe(true)
      for (const terminal of ['accepted', 'declined', 'expired', 'superseded']) {
        await setStatus(db, quote.id, terminal)
        expect(await hasOpenQuoteForEntity(db, ORG_A, ENT_A1), terminal).toBe(false)
      }
      await setStatus(db, quote.id, 'draft')
      expect(await hasOpenQuoteForEntity(db, ORG_B, ENT_A1)).toBe(false)
    })
  })

  describe('getActiveQuotesForEntities', () => {
    it('returns the sent quote over accepted over draft, ignores terminal-and-useless statuses, and is org-scoped', async () => {
      expect((await getActiveQuotesForEntities(db, ORG_A, [])).size).toBe(0)

      const draft = await draftA1(db)
      const accepted = await draftA1(db)
      const sent = await draftA1(db)
      const declined = await draftA1(db)
      await setStatus(db, accepted.id, 'accepted')
      await setStatus(db, sent.id, 'sent')
      await setStatus(db, declined.id, 'declined')
      const onlyDeclined = await createQuote(db, ORG_A, {
        entityId: ENT_A2,
        assessmentId: ASSESS_A2,
        lineItems: [],
        rate: 175,
      })
      await setStatus(db, onlyDeclined.id, 'expired')
      await createQuote(db, ORG_B, {
        entityId: ENT_B1,
        assessmentId: ASSESS_B1,
        lineItems: [],
        rate: 175,
      })

      const map = await getActiveQuotesForEntities(db, ORG_A, [ENT_A1, ENT_A2, ENT_B1])
      expect(map.get(ENT_A1)?.id).toBe(sent.id)
      expect(map.has(ENT_A2)).toBe(false)
      expect(map.has(ENT_B1)).toBe(false)

      await setStatus(db, sent.id, 'superseded')
      expect((await getActiveQuotesForEntities(db, ORG_A, [ENT_A1])).get(ENT_A1)?.id).toBe(
        accepted.id
      )
      await setStatus(db, accepted.id, 'declined')
      expect((await getActiveQuotesForEntities(db, ORG_A, [ENT_A1])).get(ENT_A1)?.id).toBe(draft.id)
    })
  })
})

describe('quotes: SOW artifact keys are org-scoped and revisioned', () => {
  it('unsigned and signed artifacts of one revision sit side by side under the org', () => {
    expect(getSowRevisionUnsignedKey('org-a', 'q-1', 'rev-1')).toBe(
      'orgs/org-a/quotes/q-1/sow/rev-1/unsigned.pdf'
    )
    expect(getSowRevisionSignedKey('org-a', 'q-1', 'rev-1')).toBe(
      'orgs/org-a/quotes/q-1/sow/rev-1/signed.pdf'
    )
  })
})

describe('POST /api/admin/quotes', () => {
  let db: D1Database

  const call = (
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    createRoute(
      routeContext({
        request: formRequest('http://test.local/api/admin/quotes', fields),
        session,
      }) as unknown as Parameters<typeof createRoute>[0]
    )

  const valid = {
    entity_id: ENT_A1,
    assessment_id: ASSESS_A1,
    line_items: JSON.stringify(LINE_ITEMS),
    rate: '175',
  }

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
    bindEnv({ DB: db })
  })

  it('answers 401 with no admin session and writes nothing', async () => {
    expect((await call(valid, null)).status).toBe(401)
    expect(await listQuotes(db, ORG_A)).toEqual([])
  })

  it('names what is wrong: missing fields, unparseable line items, a non-positive rate', async () => {
    expect(locationQuery(await call({ ...valid, rate: '' })).get('error')).toBe('missing')
    expect(locationQuery(await call({ ...valid, line_items: 'not json' })).get('error')).toBe(
      'invalid_line_items'
    )
    expect(locationQuery(await call({ ...valid, line_items: '[]' })).get('error')).toBe(
      'invalid_line_items'
    )
    expect(locationQuery(await call({ ...valid, rate: '0' })).get('error')).toBe('invalid_rate')
    expect(await listQuotes(db, ORG_A)).toEqual([])
  })

  it('creates the draft with the computed totals and lands on the quote builder', async () => {
    const res = await call({ ...valid, deposit_pct: '0.3' })
    const [quote] = await listQuotes(db, ORG_A)
    expect(locationOf(res)).toBe(`/admin/entities/${ENT_A1}/quotes/${quote.id}?saved=1`)
    expect(quote).toMatchObject({
      status: 'draft',
      total_hours: 16,
      total_price: 2800,
      deposit_pct: 0.3,
      deposit_amount: 840,
    })
  })
})

describe('POST /api/admin/entities/[id]/quotes (repeat quote, #472)', () => {
  let db: D1Database

  const call = (
    entityId: string,
    fields: Record<string, string> = {},
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    newQuoteRoute(
      routeContext({
        request: formRequest(`http://test.local/api/admin/entities/${entityId}/quotes`, fields),
        params: { id: entityId },
        session,
      }) as unknown as Parameters<typeof newQuoteRoute>[0]
    )

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
    bindEnv({ DB: db })
  })

  it('answers 401 with no session; an entity outside the org is not_found', async () => {
    expect((await call(ENT_A1, {}, null)).status).toBe(401)
    expect(locationOf(await call(ENT_B1))).toBe('/admin/entities?error=not_found')
    expect(await listQuotes(db, ORG_A)).toEqual([])
  })

  it('refuses an entity past proposing, an entity with an open quote, and an entity with no assessment', async () => {
    await db.prepare("UPDATE entities SET stage = 'engaged' WHERE id = ?").bind(ENT_A1).run()
    expect(locationQuery(await call(ENT_A1)).get('error')).toContain('from this stage')

    await db.prepare("UPDATE entities SET stage = 'proposing' WHERE id = ?").bind(ENT_A1).run()
    await draftA1(db)
    expect(locationQuery(await call(ENT_A1)).get('error')).toContain('still active')

    await seedEntity(db, { id: 'ent-a3', orgId: ORG_A, stage: 'prospect' })
    expect(locationQuery(await call('ent-a3')).get('error')).toContain('no assessment on file')
    expect(await listQuotes(db, ORG_A)).toHaveLength(1)
  })

  it('creates an empty draft shell at the launch rate on the latest assessment, linked to a valid parent only', async () => {
    const parent = await draftA1(db)
    await setStatus(db, parent.id, 'declined')
    const other = await createQuote(db, ORG_A, {
      entityId: ENT_A2,
      assessmentId: ASSESS_A2,
      lineItems: [],
      rate: 175,
    })

    const wrongParent = await call(ENT_A1, { parent_quote_id: other.id })
    const [shell] = (await listQuotes(db, ORG_A, ENT_A1)).filter((q) => q.status === 'draft')
    expect(locationOf(wrongParent)).toBe(`/admin/entities/${ENT_A1}/quotes/${shell.id}?saved=1`)
    expect(shell).toMatchObject({
      status: 'draft',
      rate: 175,
      total_hours: 0,
      total_price: 0,
      assessment_id: ASSESS_A1,
      parent_quote_id: null,
      schedule: null,
      deliverables: null,
      engagement_overview: null,
    })
    expect(JSON.parse(shell.line_items)).toEqual([])

    await setStatus(db, shell.id, 'superseded')
    await call(ENT_A1, { parent_quote_id: parent.id })
    const linked = (await listQuotes(db, ORG_A, ENT_A1)).find((q) => q.status === 'draft')
    expect(linked?.parent_quote_id).toBe(parent.id)
    expect((await getQuote(db, ORG_A, parent.id))?.status).toBe('declined')
  })
})

describe('entity detail: the new-quote action (#472)', () => {
  let db: D1Database

  const load = (entityId: string) =>
    loadEntityDetailPage({
      db,
      orgId: ORG_A,
      entityId,
      url: new URL(`http://test.local/admin/entities/${entityId}`),
    })

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
    await db
      .prepare(
        "INSERT INTO meetings (id, org_id, entity_id, status) VALUES ('mtg-1', ?, ?, 'completed')"
      )
      .bind(ORG_A, ENT_A1)
      .run()
  })

  it('shows for an entity at proposing or earlier with a meeting on file and no open quote', async () => {
    expect((await load(ENT_A1)).showNewQuoteButton).toBe(true)
  })

  it('hides once a draft or sent quote is open, and past the proposing stage', async () => {
    const quote = await draftA1(db)
    expect((await load(ENT_A1)).showNewQuoteButton).toBe(false)
    await setStatus(db, quote.id, 'declined')
    expect((await load(ENT_A1)).showNewQuoteButton).toBe(true)
    await db.prepare("UPDATE entities SET stage = 'engaged' WHERE id = ?").bind(ENT_A1).run()
    expect((await load(ENT_A1)).showNewQuoteButton).toBe(false)
  })

  it('the page renders the action only behind that flag (drift guard on an Astro component)', () => {
    const actions = readFileSync(resolve('src/components/admin/EntityStageActions.astro'), 'utf-8')
    const page = readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')
    expect(`${page}\n${actions}`).toMatch(/showNewQuoteButton\s*&&/)
  })
})
