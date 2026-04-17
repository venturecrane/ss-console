/**
 * Cross-org behavior tests for the portal DAL surface.
 *
 * Defends against the #399 2026-04-17 audit findings #8 (portal entity
 * resolution), #9 (portal quote/invoice DAL), and the refactor that was
 * required for #10 (portal dashboard queries).
 *
 * Each test seeds two orgs, plants a row in org B, then asks the portal
 * helper or DAL for that row from an org A session. The call must refuse.
 * Same-org positive controls prove the scoped path still works.
 *
 * These tests exercise the DAL primitives directly rather than going
 * through the Astro pages so the predicate is verified end-to-end without
 * the page's layout/HTML rendering surface getting in the way.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { getPortalClient } from '../../src/lib/portal/session'
import { getQuoteForEntity, listQuotesForEntity } from '../../src/lib/db/quotes'
import { getInvoiceForEntity, listInvoicesForEntity } from '../../src/lib/db/invoices'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENTITY_A = 'entity-a'
const ENTITY_B = 'entity-b'
const USER_A = 'user-a'
const USER_B = 'user-b'
const ASSESSMENT_A = 'assessment-a'
const ASSESSMENT_B = 'assessment-b'
const QUOTE_A = 'quote-a'
const QUOTE_B = 'quote-b'
const ENGAGEMENT_A = 'engagement-a'
const ENGAGEMENT_B = 'engagement-b'
const INVOICE_A = 'invoice-a'
const INVOICE_B = 'invoice-b'

describe('portal DAL — cross-org behavior (#399)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Orgs
    for (const [id, name, slug] of [
      [ORG_A, 'Org A', 'org-a'],
      [ORG_B, 'Org B', 'org-b'],
    ]) {
      await db
        .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
        .bind(id, name, slug)
        .run()
    }

    // Entities
    for (const [id, orgId, suffix] of [
      [ENTITY_A, ORG_A, 'a'],
      [ENTITY_B, ORG_B, 'b'],
    ]) {
      await db
        .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
        .bind(id, orgId, `Entity ${suffix.toUpperCase()}`, `entity-${suffix}`)
        .run()
    }

    // Portal client users — one per org, each linked to their own entity.
    for (const [id, orgId, entityId, suffix] of [
      [USER_A, ORG_A, ENTITY_A, 'a'],
      [USER_B, ORG_B, ENTITY_B, 'b'],
    ]) {
      await db
        .prepare(
          'INSERT INTO users (id, org_id, email, name, role, entity_id) VALUES (?, ?, ?, ?, ?, ?)'
        )
        .bind(
          id,
          orgId,
          `client-${suffix}@example.com`,
          `Client ${suffix.toUpperCase()}`,
          'client',
          entityId
        )
        .run()
    }

    // Assessments + sent quotes per org (portal visibility).
    for (const [assessmentId, quoteId, orgId, entityId] of [
      [ASSESSMENT_A, QUOTE_A, ORG_A, ENTITY_A],
      [ASSESSMENT_B, QUOTE_B, ORG_B, ENTITY_B],
    ]) {
      await db
        .prepare('INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, ?)')
        .bind(assessmentId, orgId, entityId, 'completed')
        .run()

      await db
        .prepare(
          `INSERT INTO quotes (id, org_id, entity_id, assessment_id, line_items, total_hours, rate, total_price, status, sent_at)
           VALUES (?, ?, ?, ?, '[]', 10, 175, 1750, 'sent', ?)`
        )
        .bind(quoteId, orgId, entityId, assessmentId, new Date().toISOString())
        .run()
    }

    // Engagements (FK target for invoices).
    for (const [engagementId, orgId, entityId, quoteId] of [
      [ENGAGEMENT_A, ORG_A, ENTITY_A, QUOTE_A],
      [ENGAGEMENT_B, ORG_B, ENTITY_B, QUOTE_B],
    ]) {
      await db
        .prepare(
          `INSERT INTO engagements (id, org_id, entity_id, quote_id, status)
           VALUES (?, ?, ?, ?, 'active')`
        )
        .bind(engagementId, orgId, entityId, quoteId)
        .run()
    }

    // Invoices — sent status so portal visibility filter allows them.
    for (const [id, orgId, entityId, engagementId] of [
      [INVOICE_A, ORG_A, ENTITY_A, ENGAGEMENT_A],
      [INVOICE_B, ORG_B, ENTITY_B, ENGAGEMENT_B],
    ]) {
      await db
        .prepare(
          `INSERT INTO invoices (id, org_id, entity_id, engagement_id, type, amount, status)
           VALUES (?, ?, ?, ?, 'deposit', 500, 'sent')`
        )
        .bind(id, orgId, entityId, engagementId)
        .run()
    }
  })

  // ============================================================
  // getPortalClient (src/lib/portal/session.ts)
  // Finding #8: entity lookup must scope by org_id.
  // ============================================================

  it('getPortalClient returns null when a valid user_id from org B is asked under session orgId=org-a', async () => {
    // Simulates a forged session where userId belongs to org B but orgId says
    // org A. The SELECT on users scopes by (id, role, org_id), so the user
    // lookup itself fails.
    const result = await getPortalClient(db, USER_B, ORG_A)
    expect(result).toBeNull()
  })

  it('getPortalClient returns null if users.entity_id points cross-org (stale or tampered)', async () => {
    // Introduce a stale/tampered link: org A user pointing at org B's entity.
    await db.prepare('UPDATE users SET entity_id = ? WHERE id = ?').bind(ENTITY_B, USER_A).run()

    // Even though the user row exists in org A, the entity lookup is scoped
    // by org_id and returns null, so the helper returns null overall.
    const result = await getPortalClient(db, USER_A, ORG_A)
    expect(result).toBeNull()
  })

  it('getPortalClient returns the client when session org matches user and entity', async () => {
    const result = await getPortalClient(db, USER_A, ORG_A)
    expect(result?.client.id).toBe(ENTITY_A)
    expect(result?.user.id).toBe(USER_A)
  })

  // ============================================================
  // Portal quote DAL — getQuoteForEntity + listQuotesForEntity
  // Finding #9 (quotes).
  // ============================================================

  it('getQuoteForEntity returns null for a cross-org quote even when entity_id is correct for the quote', async () => {
    // Caller from org A passes entity_id and quote_id from org B. Scope by
    // org_id refuses.
    const result = await getQuoteForEntity(db, ORG_A, ENTITY_B, QUOTE_B)
    expect(result).toBeNull()
  })

  it('getQuoteForEntity returns the row when org_id and entity_id match', async () => {
    const result = await getQuoteForEntity(db, ORG_A, ENTITY_A, QUOTE_A)
    expect(result?.id).toBe(QUOTE_A)
  })

  it('listQuotesForEntity returns empty when entity belongs to a different org', async () => {
    const result = await listQuotesForEntity(db, ORG_A, ENTITY_B)
    expect(result).toHaveLength(0)
  })

  it('listQuotesForEntity returns the owning-org quotes', async () => {
    const result = await listQuotesForEntity(db, ORG_A, ENTITY_A)
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe(QUOTE_A)
  })

  // ============================================================
  // Portal invoice DAL — getInvoiceForEntity + listInvoicesForEntity
  // Finding #9 (invoices).
  // ============================================================

  it('getInvoiceForEntity returns null for a cross-org invoice', async () => {
    const result = await getInvoiceForEntity(db, ORG_A, ENTITY_B, INVOICE_B)
    expect(result).toBeNull()
  })

  it('getInvoiceForEntity returns the row when org_id and entity_id match', async () => {
    const result = await getInvoiceForEntity(db, ORG_A, ENTITY_A, INVOICE_A)
    expect(result?.id).toBe(INVOICE_A)
  })

  it('listInvoicesForEntity returns empty when entity belongs to a different org', async () => {
    const result = await listInvoicesForEntity(db, ORG_A, ENTITY_B)
    expect(result).toHaveLength(0)
  })

  it('listInvoicesForEntity returns the owning-org invoices', async () => {
    const result = await listInvoicesForEntity(db, ORG_A, ENTITY_A)
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe(INVOICE_A)
  })
})
