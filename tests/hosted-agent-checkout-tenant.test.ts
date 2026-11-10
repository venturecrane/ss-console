/**
 * The hosted-agent checkout's team alert names the buyer's entity only when
 * that entity belongs to SMD's organization (2026-11-10 architecture review,
 * finding 11: it was the one of eight entity-name lookups with no org binding).
 *
 * Real migrated D1 and the real handler; only the Resend send is faked, so the
 * alert's subject is what the test reads. A user row bound to an entity owned by
 * another organization must not pull that entity's name into SMD's alert; the
 * control case proves the alert does carry the name when the entity is SMD's.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../src/lib/constants'
import { HOSTED_AGENT_PRODUCT_SLUG } from '../src/lib/portal/hosted-agent-access'
import { migratedDb, seedEntity, seedOrg } from './_stubs/behavioural'

const sent: Array<{ to: string; subject: string }> = []
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: async (_key: string | undefined, payload: { to: string; subject: string }) => {
    sent.push({ to: payload.to, subject: payload.subject })
    return { success: true }
  },
}))

// Import AFTER the mock so the handler binds the fake sender.
import { handleHostedAgentCheckoutCompleted } from '../src/lib/webhooks/hosted-agent-checkout-handler'

const OTHER_ORG = 'org-someone-else'
const CLERK_ID = 'user_tenant_probe'

async function seedBuyer(db: D1Database, entityId: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
       VALUES (?, ?, ?, ?, 'client', ?, ?)`
    )
    .bind('u-tenant-probe', ORG_ID, 'buyer@example.com', 'Buyer', entityId, CLERK_ID)
    .run()
}

async function checkout(db: D1Database): Promise<Response> {
  return handleHostedAgentCheckoutCompleted(
    db,
    're_test_key',
    'https://portal.smd.services',
    'https://admin.smd.services/admin/queue',
    {
      id: 'cs_tenant_probe',
      client_reference_id: CLERK_ID,
      customer: 'cus_probe',
      subscription: 'sub_probe',
      amount_total: 100,
      customer_details: { email: 'buyer@example.com', name: 'Buyer' },
      metadata: { product_slug: HOSTED_AGENT_PRODUCT_SLUG },
    }
  )
}

function teamAlertSubject(): string {
  const alert = sent.find((m) => m.to === 'team@smd.services')
  expect(alert, 'the team alert was sent').toBeDefined()
  return alert!.subject
}

describe('hosted-agent checkout team alert: entity lookup binds the organization', () => {
  let db: D1Database

  beforeEach(async () => {
    sent.length = 0
    db = await migratedDb()
    await seedOrg(db, ORG_ID)
    await seedOrg(db, OTHER_ORG)
  })

  it('never names an entity that belongs to another organization', async () => {
    await seedEntity(db, { id: 'ent-foreign', orgId: OTHER_ORG, name: 'Foreign Firm' })
    await seedBuyer(db, 'ent-foreign')

    expect((await checkout(db)).status).toBe(200)

    const subject = teamAlertSubject()
    expect(subject).not.toContain('Foreign Firm')
    expect(subject).toBe('Self-serve purchase: ent-foreign (standard)')
  })

  it("names the entity when it is SMD's own (control: the lookup can find a name)", async () => {
    await seedEntity(db, { id: 'ent-own', orgId: ORG_ID, name: 'Own Firm' })
    await seedBuyer(db, 'ent-own')

    expect((await checkout(db)).status).toBe(200)

    expect(teamAlertSubject()).toBe('Self-serve purchase: Own Firm (standard)')
  })
})
