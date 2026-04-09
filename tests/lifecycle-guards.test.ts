/**
 * Lifecycle invariant guard tests.
 *
 * Tests the pre-condition checks added to transitionStage() and
 * updateQuoteStatus() to enforce business rules at the DAL layer.
 *
 * Uses @venturecrane/crane-test-harness for in-memory D1 with real
 * SQL execution against the actual migration schema.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity, transitionStage, type EntityStage } from '../src/lib/db/entities'
import { createQuote, updateQuoteStatus } from '../src/lib/db/quotes'
import { readFileSync } from 'fs'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_ID = 'org-test'

describe('lifecycle invariant guards', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Seed organization
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Test Org', 'test-org')
      .run()
  })

  // =========================================================================
  // transitionStage: proposing -> engaged
  // =========================================================================

  describe('proposing -> engaged', () => {
    let entityId: string

    beforeEach(async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Guard Test Biz',
        stage: 'proposing' as EntityStage,
      })
      entityId = entity.id
    })

    it('throws without an accepted quote', async () => {
      await expect(
        transitionStage(db, ORG_ID, entityId, 'engaged', 'Starting engagement')
      ).rejects.toThrow('no accepted quote found')
    })

    it('succeeds with an accepted quote', async () => {
      // Seed an assessment (required FK for quotes)
      await db
        .prepare(
          `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
        )
        .bind('assessment-1', ORG_ID, entityId)
        .run()

      // Seed an accepted quote for this entity
      await db
        .prepare(
          `INSERT INTO quotes (id, org_id, entity_id, assessment_id, version, line_items, total_hours, rate, total_price, deposit_pct, deposit_amount, status, signwell_doc_id, signed_sow_path, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, '[]', 10, 150, 1500, 0.5, 750, 'accepted', 'sw-123', '/sow/signed.pdf', datetime('now'), datetime('now'))`
        )
        .bind('quote-1', ORG_ID, entityId, 'assessment-1')
        .run()

      const result = await transitionStage(db, ORG_ID, entityId, 'engaged', 'Starting engagement')
      expect(result).not.toBeNull()
      expect(result!.stage).toBe('engaged')
    })
  })

  // =========================================================================
  // transitionStage: delivered -> ongoing
  // =========================================================================

  describe('delivered -> ongoing', () => {
    let entityId: string

    beforeEach(async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Delivered Biz',
        stage: 'delivered' as EntityStage,
      })
      entityId = entity.id
    })

    it('throws without a paid completion invoice', async () => {
      await expect(
        transitionStage(db, ORG_ID, entityId, 'ongoing', 'Moving to retainer')
      ).rejects.toThrow('completion invoice has not been paid')
    })

    it('succeeds with a paid completion invoice', async () => {
      // Seed a paid completion invoice
      await db
        .prepare(
          `INSERT INTO invoices (id, org_id, entity_id, type, amount, status, created_at, updated_at)
           VALUES (?, ?, ?, 'completion', 1500, 'paid', datetime('now'), datetime('now'))`
        )
        .bind('inv-1', ORG_ID, entityId)
        .run()

      const result = await transitionStage(db, ORG_ID, entityId, 'ongoing', 'Moving to retainer')
      expect(result).not.toBeNull()
      expect(result!.stage).toBe('ongoing')
    })

    it('succeeds with force override and logs reason to context', async () => {
      const result = await transitionStage(db, ORG_ID, entityId, 'ongoing', 'Moving to retainer', {
        force: 'Client paid via wire transfer outside system',
      })
      expect(result).not.toBeNull()
      expect(result!.stage).toBe('ongoing')

      // Verify the override was logged to context
      const contextEntries = await db
        .prepare(
          `SELECT * FROM context WHERE entity_id = ? AND type = 'stage_change' AND content LIKE '%Force override%'`
        )
        .bind(entityId)
        .all()
      expect(contextEntries.results.length).toBeGreaterThanOrEqual(1)
      const overrideEntry = contextEntries.results[0] as Record<string, unknown>
      expect(overrideEntry.content).toContain('wire transfer outside system')
    })
  })

  // =========================================================================
  // transitionStage: signal -> assessing (blocked by VALID_TRANSITIONS)
  // =========================================================================

  describe('signal -> assessing', () => {
    it('throws because VALID_TRANSITIONS does not allow direct signal -> assessing', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Signal Biz',
        stage: 'signal' as EntityStage,
      })

      await expect(
        transitionStage(db, ORG_ID, entity.id, 'assessing', 'Skip prospect')
      ).rejects.toThrow('Invalid stage transition')
    })
  })

  // =========================================================================
  // updateQuoteStatus: acceptance guards
  // =========================================================================

  describe('quote acceptance guards', () => {
    let quoteId: string

    beforeEach(async () => {
      // Create an entity and a quote in 'sent' status
      const entity = await createEntity(db, ORG_ID, {
        name: 'Quote Guard Biz',
        stage: 'proposing' as EntityStage,
      })

      // Seed an assessment (assessments table: id, org_id, entity_id, status)
      await db
        .prepare(
          `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
        )
        .bind('assess-1', ORG_ID, entity.id)
        .run()

      const quote = await createQuote(db, ORG_ID, {
        entityId: entity.id,
        assessmentId: 'assess-1',
        lineItems: [{ problem: 'Test', description: 'Test item', estimated_hours: 10 }],
        rate: 150,
      })
      quoteId = quote.id

      // Transition to sent
      await updateQuoteStatus(db, ORG_ID, quoteId, 'sent')
    })

    it('throws when signwell_doc_id is null', async () => {
      // Quote has been sent but never went through SignWell
      await expect(updateQuoteStatus(db, ORG_ID, quoteId, 'accepted')).rejects.toThrow(
        'signwell_doc_id is null'
      )
    })

    it('throws when signed_sow_path is null even with signwell_doc_id', async () => {
      // Set signwell_doc_id but leave signed_sow_path null
      await db
        .prepare(`UPDATE quotes SET signwell_doc_id = ? WHERE id = ?`)
        .bind('sw-doc-123', quoteId)
        .run()

      await expect(updateQuoteStatus(db, ORG_ID, quoteId, 'accepted')).rejects.toThrow(
        'signed_sow_path is null'
      )
    })

    it('succeeds when both signwell_doc_id and signed_sow_path are set', async () => {
      // Set both fields as the SignWell webhook would
      await db
        .prepare(`UPDATE quotes SET signwell_doc_id = ?, signed_sow_path = ? WHERE id = ?`)
        .bind('sw-doc-123', '/orgs/test-org/quotes/sow-signed.pdf', quoteId)
        .run()

      const result = await updateQuoteStatus(db, ORG_ID, quoteId, 'accepted')
      expect(result).not.toBeNull()
      expect(result!.status).toBe('accepted')
      expect(result!.accepted_at).not.toBeNull()
    })
  })

  // =========================================================================
  // context.ts: append-only invariant
  // =========================================================================

  describe('context.ts append-only invariant', () => {
    const source = () => readFileSync(resolve('src/lib/db/context.ts'), 'utf-8')

    it('does not export any UPDATE operations', () => {
      const code = source()
      const exportedFunctions = code.match(/export\s+async\s+function\s+\w+/g) ?? []
      const functionNames = exportedFunctions.map((m) =>
        m.replace(/export\s+async\s+function\s+/, '')
      )

      // All exported functions should be append (INSERT) or read (SELECT) only
      for (const name of functionNames) {
        expect(name).not.toMatch(/update|delete|remove|drop/i)
      }
    })

    it('documents the append-only invariant', () => {
      const code = source()
      expect(code).toContain('INVARIANT: APPEND-ONLY')
      expect(code).toContain('NO update or delete operations')
    })
  })
})
