/**
 * Behavioural tests for the portal document surface: the download route
 * (src/pages/api/portal/documents/[...key].ts), the offerings resolver
 * (src/lib/portal/offerings.ts), and the R2 listing helpers.
 *
 * Until 2026-09-11 this file matched source text (review 2026-09-10, Testing
 * 3). The route is now driven end to end: a migrated D1 for the client, the
 * engagement and the agreement rows, an in-memory R2 for the objects, and
 * the Clerk seam faked the way tests/middleware-behavior.test.ts fakes it.
 * Every refusal path is exercised, since the route is the only thing between
 * a signed-in client and every other client's files.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  deriveOfferings,
  hasPortalVisibleInvoices,
  humanizeSlug,
  resolvePortalOfferings,
} from '../src/lib/portal/offerings'
import type { SubscriptionRow } from '../src/lib/portal/product-access'
import type { Engagement } from '../src/lib/db/engagements'
import type { Quote } from '../src/lib/db/quotes'
import { listDocuments, streamDocument } from '../src/lib/storage/r2'
import { GET } from '../src/pages/api/portal/documents/[...key]'
import {
  bindEnv,
  memoryBucket,
  migratedDb,
  portalLocals,
  seedEngagement,
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
const ENG = 'eng-a'
const QUOTE = `quote-${ENG}`
const CLERK_ID = 'user_clerk_docs'
const USER_ID = 'u-docs'

describe('GET /api/portal/documents/[...key]', () => {
  let db: D1Database
  let storage: MemoryBucket

  const call = (key: string | undefined, clerkUserId: string | null = CLERK_ID) =>
    GET({
      locals: portalLocals({ clerkUserId }),
      params: { key },
    } as unknown as Parameters<typeof GET>[0])

  const engagementDoc = `${ORG}/engagements/${ENG}/docs/plan.pdf`

  beforeEach(async () => {
    db = await migratedDb()
    storage = memoryBucket()
    await seedOrg(db, ORG)
    await seedEntity(db, { id: ENT, orgId: ORG, stage: 'engaged' })
    await seedEntity(db, { id: ENT_OTHER, orgId: ORG, stage: 'engaged' })
    await seedEngagement(db, { id: ENG, orgId: ORG, entityId: ENT })
    await seedEngagement(db, { id: 'eng-other', orgId: ORG, entityId: ENT_OTHER })
    await seedPortalUser(db, { id: USER_ID, orgId: ORG, clerkUserId: CLERK_ID, entityId: ENT })
    await seedPortalUser(db, {
      id: 'u-unbound',
      orgId: ORG,
      clerkUserId: 'user_unbound',
      entityId: null,
    })
    await storage.bucket.put(engagementDoc, '%PDF plan')
    bindEnv({ DB: db, STORAGE: storage.bucket })
  })

  it('400 without a key, 401 signed out, 403 for a user with no entity', async () => {
    expect((await call(undefined)).status).toBe(400)
    expect((await call(engagementDoc, null)).status).toBe(401)
    expect((await call(engagementDoc, 'user_unbound')).status).toBe(403)
  })

  it('refuses a key outside the org prefixes, a traversal, and a key under another client of the same org', async () => {
    for (const key of [
      `org-b/engagements/${ENG}/docs/plan.pdf`,
      `${ORG}/engagements/${ENG}/../eng-other/docs/plan.pdf`,
      `${ORG}//engagements/${ENG}/docs/plan.pdf`,
      `${ORG}/engagements/eng-other/docs/plan.pdf`,
      `orgs/${ORG}/quotes/quote-eng-other/sow/r/signed.pdf`,
    ]) {
      const res = await call(key)
      expect(res.status, key).toBe(403)
      expect(await res.json()).toMatchObject({ error: 'forbidden' })
    }
  })

  it('streams an engagement document inline when it is a PDF, as an attachment otherwise, and 404s a missing object', async () => {
    const pdf = await call(engagementDoc)
    expect(pdf.status).toBe(200)
    expect(pdf.headers.get('Content-Type')).toBe('application/pdf')
    expect(pdf.headers.get('Content-Disposition')).toBe('inline; filename="plan.pdf"')
    expect(pdf.headers.get('Cache-Control')).toBe('private, max-age=3600')
    expect(await pdf.text()).toBe('%PDF plan')

    const docxKey = `${ORG}/engagements/${ENG}/docs/notes.docx`
    await storage.bucket.put(docxKey, 'docx')
    const docx = await call(docxKey)
    expect(docx.headers.get('Content-Type')).toBe(
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )
    expect(docx.headers.get('Content-Disposition')).toBe('attachment; filename="notes.docx"')

    expect((await call(`${ORG}/engagements/${ENG}/docs/gone.pdf`)).status).toBe(404)
  })

  it("serves the client's SOW revisions under both the legacy and the orgs/ prefix", async () => {
    const legacy = `${ORG}/quotes/${QUOTE}/sow.pdf`
    const revisioned = `orgs/${ORG}/quotes/${QUOTE}/sow/rev-1/signed.pdf`
    await storage.bucket.put(legacy, 'legacy')
    await storage.bucket.put(revisioned, 'signed')
    expect(await (await call(legacy)).text()).toBe('legacy')
    expect(await (await call(revisioned)).text()).toBe('signed')
  })

  it('an executed Operator agreement is authorized by its own row and a principal or compliance role, never by prefix', async () => {
    const key = `${ORG}/engagements/${ENG}/docs/agreement.pdf`
    await storage.bucket.put(key, 'agreement')
    await db
      .prepare(
        `INSERT INTO operator_agreement_documents (id, org_id, entity_id, instance_slug, title, executed_on, storage_key, file_name)
         VALUES ('agr-1', ?, ?, 'alpha', 'Operator Service Agreement', '2026-09-01', ?, 'agreement.pdf')`
      )
      .bind(ORG, ENT_OTHER, key)
      .run()
    // The key sits under this client's engagement prefix, but the row names
    // another entity: refused, with no second chance at the prefix checks.
    expect((await call(key)).status).toBe(403)

    await db
      .prepare('UPDATE operator_agreement_documents SET entity_id = ? WHERE id = ?')
      .bind(ENT, 'agr-1')
      .run()
    expect((await call(key)).status).toBe(403)

    await db
      .prepare(
        `INSERT INTO product_roles (id, org_id, user_id, entity_id, product_slug, role) VALUES ('pr-1', ?, ?, ?, 'operator', 'principal')`
      )
      .bind(ORG, USER_ID, ENT)
      .run()
    const allowed = await call(key)
    expect(allowed.status).toBe(200)
    expect(await allowed.text()).toBe('agreement')
  })
})

describe('portal offerings', () => {
  const engagement = (id: string, status: string) => ({ id, status }) as Engagement
  const quote = (id: string, status: string) => ({ id, status }) as Quote
  const subscription = (over: Partial<SubscriptionRow>): SubscriptionRow => ({
    id: 'sub',
    org_id: ORG,
    entity_id: ENT,
    product_slug: 'operator',
    instance_slug: 'alpha',
    status: 'active',
    started_at: '',
    ended_at: null,
    settings_json: null,
    service_id: null,
    stripe_subscription_id: null,
    created_at: '',
    updated_at: '',
    ...over,
  })

  it('humanizeSlug title-cases a kebab slug', () => {
    expect(humanizeSlug('pilot-smokeball')).toBe('Pilot Smokeball')
    expect(humanizeSlug('alpha')).toBe('Alpha')
    expect(humanizeSlug('a--b')).toBe('A B')
  })

  it('separates the active engagement from past ones and finds the open proposal; both can be true at once', () => {
    const offerings = deriveOfferings({
      engagements: [
        engagement('done', 'completed'),
        engagement('live', 'active'),
        engagement('gone', 'cancelled'),
      ],
      quotes: [quote('q1', 'accepted'), quote('q2', 'sent')],
      subscriptions: [],
      operatorConfigs: [],
      hasInvoices: false,
    })
    expect(offerings.engagement.present).toBe(true)
    expect(offerings.engagement.activeEngagement?.id).toBe('live')
    expect(offerings.engagement.pastEngagements.map((e) => e.id)).toEqual(['done', 'gone'])
    expect(offerings.engagement.openProposal?.id).toBe('q2')
    expect(offerings.operators).toEqual([])
    expect(offerings.hostedAgent).toBeNull()
    expect(offerings.preGoLiveLanding).toBeNull()
  })

  it('a billing relationship exists once there is invoice history or a subscription past provisioning', () => {
    const base = { engagements: [], quotes: [], operatorConfigs: [] }
    expect(
      deriveOfferings({ ...base, subscriptions: [], hasInvoices: false }).hasBillingRelationship
    ).toBe(false)
    expect(
      deriveOfferings({ ...base, subscriptions: [], hasInvoices: true }).hasBillingRelationship
    ).toBe(true)
    expect(
      deriveOfferings({
        ...base,
        subscriptions: [subscription({ status: 'provisioning' })],
        hasInvoices: false,
      }).hasBillingRelationship
    ).toBe(false)
    expect(
      deriveOfferings({
        ...base,
        subscriptions: [subscription({ status: 'active' })],
        hasInvoices: false,
      }).hasBillingRelationship
    ).toBe(true)
  })

  it('lists one operator per subscription, named by its active persona or the humanized slug, dropping a slug-less row', () => {
    const offerings = deriveOfferings({
      engagements: [],
      quotes: [],
      subscriptions: [
        subscription({ id: 's1', instance_slug: 'alpha' }),
        subscription({ id: 's2', instance_slug: 'beta-law' }),
        subscription({ id: 's3', instance_slug: null }),
        subscription({ id: 's4', product_slug: 'hosted-agent', instance_slug: null }),
      ],
      operatorConfigs: [{ customer_slug: 'alpha', displayName: 'Rae' }],
      hasInvoices: true,
    })
    expect(offerings.operators.map((o) => [o.slug, o.displayName])).toEqual([
      ['alpha', 'Rae'],
      ['beta-law', 'Beta Law'],
    ])
    expect(offerings.hostedAgent?.id).toBe('s4')
  })

  it('pre-go-live, an operators-only client lands on the instance (one) or the list (many); anything live or engaged cancels it', () => {
    const base = { engagements: [], quotes: [], operatorConfigs: [], hasInvoices: false }
    const one = deriveOfferings({
      ...base,
      subscriptions: [subscription({ status: 'provisioning' })],
    })
    expect(one.preGoLiveLanding).toBe('/portal/products/operator/alpha')
    const many = deriveOfferings({
      ...base,
      subscriptions: [
        subscription({ id: 's1', status: 'provisioning', instance_slug: 'alpha' }),
        subscription({ id: 's2', status: 'provisioning', instance_slug: 'beta' }),
      ],
    })
    expect(many.preGoLiveLanding).toBe('/portal/products/operator')
    expect(
      deriveOfferings({ ...base, subscriptions: [subscription({ status: 'active' })] })
        .preGoLiveLanding
    ).toBeNull()
    expect(
      deriveOfferings({
        ...base,
        quotes: [quote('q', 'sent')],
        subscriptions: [subscription({ status: 'provisioning' })],
      }).preGoLiveLanding
    ).toBeNull()
  })

  describe('against real D1', () => {
    let db: D1Database

    beforeEach(async () => {
      db = await migratedDb()
      await seedOrg(db, ORG)
      await seedEntity(db, { id: ENT, orgId: ORG, stage: 'engaged' })
      await seedEngagement(db, { id: ENG, orgId: ORG, entityId: ENT, status: 'completed' })
    })

    it('hasPortalVisibleInvoices sees sent, paid and overdue invoices, not drafts or voids', async () => {
      expect(await hasPortalVisibleInvoices(db, ENT)).toBe(false)
      for (const [id, status] of [
        ['inv-draft', 'draft'],
        ['inv-void', 'void'],
      ]) {
        await db
          .prepare(
            `INSERT INTO invoices (id, org_id, entity_id, type, amount, status) VALUES (?, ?, ?, 'deposit', 100, ?)`
          )
          .bind(id, ORG, ENT, status)
          .run()
      }
      expect(await hasPortalVisibleInvoices(db, ENT)).toBe(false)
      await db
        .prepare(
          `INSERT INTO invoices (id, org_id, entity_id, type, amount, status) VALUES ('inv-sent', ?, ?, 'deposit', 100, 'sent')`
        )
        .bind(ORG, ENT)
        .run()
      expect(await hasPortalVisibleInvoices(db, ENT)).toBe(true)
    })

    it('resolvePortalOfferings reads the rows and derives the same shape', async () => {
      const offerings = await resolvePortalOfferings(db, ORG, ENT)
      expect(offerings.engagement.present).toBe(true)
      expect(offerings.engagement.activeEngagement).toBeNull()
      expect(offerings.engagement.pastEngagements.map((e) => e.id)).toEqual([ENG])
      expect(offerings.hasBillingRelationship).toBe(false)
      expect(offerings.preGoLiveLanding).toBeNull()
    })
  })
})

describe('R2 document helpers', () => {
  it('listDocuments returns the objects under a prefix; streamDocument returns the object or null', async () => {
    const { bucket } = memoryBucket()
    await bucket.put('org/engagements/e1/docs/a.pdf', 'a')
    await bucket.put('org/engagements/e1/docs/b.pdf', 'b')
    await bucket.put('org/engagements/e2/docs/c.pdf', 'c')
    const listed = await listDocuments(bucket, 'org/engagements/e1/docs/')
    expect(listed.map((o) => o.key).sort()).toEqual([
      'org/engagements/e1/docs/a.pdf',
      'org/engagements/e1/docs/b.pdf',
    ])
    expect(await (await streamDocument(bucket, 'org/engagements/e2/docs/c.pdf'))?.text()).toBe('c')
    expect(await streamDocument(bucket, 'nope')).toBeNull()
  })
})
