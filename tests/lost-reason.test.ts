/**
 * Lost-reason structured capture tests.
 *
 * Covers issue #477:
 * - transitionStage(..., 'lost', ...) requires a structured lost reason
 * - the stage_change context entry carries lost_reason + lost_detail
 *   as JSON metadata, queryable via json_extract
 * - getLatestLostReasonsByEntity rolls up the freshest code per entity
 * - invalid codes are rejected
 *
 * Uses @venturecrane/crane-test-harness for in-memory D1 with real
 * SQL execution against the actual migration schema — mirrors
 * lifecycle-guards.test.ts.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import {
  createEntity,
  transitionStage,
  getLatestLostReasonsByEntity,
  type EntityStage,
} from '../src/lib/db/entities'
import { LOST_REASONS, isLostReasonCode } from '../src/lib/db/lost-reasons'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG_ID = 'org-lost-test'

describe('structured lost reason', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Lost Test Org', 'lost-test-org')
      .run()
  })

  describe('taxonomy module', () => {
    it('exposes the seven canonical codes', () => {
      const codes = LOST_REASONS.map((r) => r.value).sort()
      expect(codes).toEqual(
        [
          'declined-quote',
          'no-budget',
          'no-response',
          'not-a-fit',
          'other',
          'unreachable',
          'wrong-contact',
        ].sort()
      )
    })

    it('isLostReasonCode validates codes strictly', () => {
      expect(isLostReasonCode('not-a-fit')).toBe(true)
      expect(isLostReasonCode('other')).toBe(true)
      expect(isLostReasonCode('not_a_fit')).toBe(false)
      expect(isLostReasonCode('')).toBe(false)
      expect(isLostReasonCode(null)).toBe(false)
      expect(isLostReasonCode(42)).toBe(false)
    })
  })

  describe('transitionStage → lost', () => {
    it('throws when no lost reason is provided', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'No-Reason Biz',
        stage: 'signal' as EntityStage,
      })
      await expect(transitionStage(db, ORG_ID, entity.id, 'lost', 'Dismissed.')).rejects.toThrow(
        'Lost reason is required'
      )
    })

    it('throws when the provided code is not in the taxonomy', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Bad-Code Biz',
        stage: 'signal' as EntityStage,
      })
      await expect(
        transitionStage(db, ORG_ID, entity.id, 'lost', 'Dismissed.', {
          // Cast around the type to exercise runtime validation.
          lostReason: { code: 'ghost' as never },
        })
      ).rejects.toThrow('Invalid lost reason code')
    })

    it('persists lost_reason + lost_detail on the stage_change metadata', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Persist Biz',
        stage: 'signal' as EntityStage,
      })
      await transitionStage(db, ORG_ID, entity.id, 'lost', 'Dismissed from inbox.', {
        lostReason: { code: 'no-budget', detail: 'Said they could fund it next quarter.' },
      })

      const row = await db
        .prepare(
          `SELECT metadata FROM context
            WHERE entity_id = ? AND type = 'stage_change'
            ORDER BY created_at DESC LIMIT 1`
        )
        .bind(entity.id)
        .first<{ metadata: string }>()
      expect(row).not.toBeNull()
      const meta = JSON.parse(row!.metadata) as Record<string, unknown>
      expect(meta.to).toBe('lost')
      expect(meta.lost_reason).toBe('no-budget')
      expect(meta.lost_detail).toBe('Said they could fund it next quarter.')
    })

    it('trims whitespace detail and stores empty detail as absent', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Trim Biz',
        stage: 'signal' as EntityStage,
      })
      await transitionStage(db, ORG_ID, entity.id, 'lost', 'Dismissed.', {
        lostReason: { code: 'unreachable', detail: '   ' },
      })
      const row = await db
        .prepare(
          `SELECT metadata FROM context WHERE entity_id = ? AND type = 'stage_change' LIMIT 1`
        )
        .bind(entity.id)
        .first<{ metadata: string }>()
      const meta = JSON.parse(row!.metadata) as Record<string, unknown>
      expect(meta.lost_reason).toBe('unreachable')
      expect('lost_detail' in meta).toBe(false)
    })

    it('non-lost transitions ignore lostReason option and still succeed', async () => {
      // Create a signal entity and promote — must not require a lost reason.
      const entity = await createEntity(db, ORG_ID, {
        name: 'Promote Biz',
        stage: 'signal' as EntityStage,
      })
      const result = await transitionStage(
        db,
        ORG_ID,
        entity.id,
        'prospect',
        'Promoted from signal.'
      )
      expect(result).not.toBeNull()
      expect(result!.stage).toBe('prospect')
    })
  })

  describe('getLatestLostReasonsByEntity', () => {
    it('returns an empty map when no entity IDs are provided', async () => {
      const map = await getLatestLostReasonsByEntity(db, ORG_ID, [])
      expect(map.size).toBe(0)
    })

    it('rolls up the most recent lost reason per entity', async () => {
      const a = await createEntity(db, ORG_ID, {
        name: 'Entity A',
        stage: 'signal' as EntityStage,
      })
      const b = await createEntity(db, ORG_ID, {
        name: 'Entity B',
        stage: 'signal' as EntityStage,
      })

      await transitionStage(db, ORG_ID, a.id, 'lost', 'A', {
        lostReason: { code: 'no-response' },
      })
      // Re-engage A and then mark lost again with a different code.
      await transitionStage(db, ORG_ID, a.id, 'prospect', 'Re-engaged')
      await transitionStage(db, ORG_ID, a.id, 'lost', 'A2', {
        lostReason: { code: 'declined-quote', detail: 'Sent SOW; owner passed.' },
      })

      await transitionStage(db, ORG_ID, b.id, 'lost', 'B', {
        lostReason: { code: 'wrong-contact' },
      })

      const map = await getLatestLostReasonsByEntity(db, ORG_ID, [a.id, b.id])
      expect(map.size).toBe(2)
      expect(map.get(a.id)?.code).toBe('declined-quote')
      expect(map.get(a.id)?.detail).toBe('Sent SOW; owner passed.')
      expect(map.get(b.id)?.code).toBe('wrong-contact')
      expect(map.get(b.id)?.detail).toBeNull()
    })

    it('omits entities whose latest stage_change has no lost_reason (legacy rows)', async () => {
      const entity = await createEntity(db, ORG_ID, {
        name: 'Legacy Biz',
        stage: 'signal' as EntityStage,
      })
      // Simulate a legacy Lost row: insert stage_change metadata without
      // lost_reason. Bypasses transitionStage() which would now enforce.
      await db
        .prepare(
          `INSERT INTO context (id, entity_id, org_id, type, content, source, content_size, metadata, created_at)
            VALUES (?, ?, ?, 'stage_change', ?, 'system', ?, ?, datetime('now'))`
        )
        .bind(
          crypto.randomUUID(),
          entity.id,
          ORG_ID,
          'Stage: signal → lost. Legacy row.',
          30,
          JSON.stringify({ from: 'signal', to: 'lost', reason: 'Legacy row.' })
        )
        .run()
      // Also flip the entity's stage column so it shows on the Lost tab.
      await db.prepare(`UPDATE entities SET stage = 'lost' WHERE id = ?`).bind(entity.id).run()

      const map = await getLatestLostReasonsByEntity(db, ORG_ID, [entity.id])
      expect(map.has(entity.id)).toBe(false)
    })
  })
})
