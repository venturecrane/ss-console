/**
 * Behavioural tests for the milestone data layer (src/lib/db/milestones.ts).
 *
 * Replaces the source-text mirror of 2026-09-11 (review 2026-09-10, Testing
 * 3): the ordering, the defaults, the state machine and the org predicate are
 * asserted on rows in a migrated D1, not on the phrasing of the module. The
 * route over this layer, including its cross-org paths, is covered in
 * tests/admin/milestones.cross-org.test.ts.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createMilestone,
  deleteMilestone,
  getMilestone,
  listMilestones,
  updateMilestone,
  updateMilestoneStatus,
  VALID_TRANSITIONS,
  type MilestoneStatus,
} from '../src/lib/db/milestones'
import { migratedDb, seedEngagement, seedEntity, seedOrg } from './_stubs/behavioural'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENG_A = 'eng-a'
const ENG_A2 = 'eng-a2'
const ENG_B = 'eng-b'

describe('milestones data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: 'ent-a', orgId: ORG_A, stage: 'engaged' })
    await seedEntity(db, { id: 'ent-b', orgId: ORG_B, stage: 'engaged' })
    await seedEngagement(db, { id: ENG_A, orgId: ORG_A, entityId: 'ent-a' })
    await seedEngagement(db, { id: ENG_A2, orgId: ORG_A, entityId: 'ent-a' })
    await seedEngagement(db, { id: ENG_B, orgId: ORG_B, entityId: 'ent-b' })
  })

  describe('createMilestone / getMilestone', () => {
    it('is born pending with the given fields and reads back by id', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, {
        name: 'Discovery',
        description: 'Shadow the intake desk',
        due_date: '2026-10-15',
        payment_trigger: true,
        sort_order: 2,
      })
      expect(created).toMatchObject({
        org_id: ORG_A,
        engagement_id: ENG_A,
        name: 'Discovery',
        description: 'Shadow the intake desk',
        due_date: '2026-10-15',
        status: 'pending',
        payment_trigger: 1,
        sort_order: 2,
        completed_at: null,
      })
      expect(await getMilestone(db, ORG_A, created.id)).toEqual(created)
    })

    it('defaults: no description, no due date, no payment trigger, sort_order 0', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'Bare' })
      expect(created.description).toBeNull()
      expect(created.due_date).toBeNull()
      expect(created.payment_trigger).toBe(0)
      expect(created.sort_order).toBe(0)
    })

    it('org isolation: the row carries org_id from birth, so another org reads null', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'Private' })
      expect(await getMilestone(db, ORG_B, created.id)).toBeNull()
    })
  })

  describe('listMilestones', () => {
    it('returns the engagement milestones in sort_order, ascending', async () => {
      await createMilestone(db, ORG_A, ENG_A, { name: 'Third', sort_order: 3 })
      await createMilestone(db, ORG_A, ENG_A, { name: 'First', sort_order: 1 })
      await createMilestone(db, ORG_A, ENG_A, { name: 'Second', sort_order: 2 })
      await createMilestone(db, ORG_A, ENG_A2, { name: 'Sibling engagement', sort_order: 0 })

      const rows = await listMilestones(db, ORG_A, ENG_A)
      expect(rows.map((r) => r.name)).toEqual(['First', 'Second', 'Third'])
    })

    it('org isolation: the right engagement under the wrong org lists nothing', async () => {
      await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await listMilestones(db, ORG_B, ENG_A)).toEqual([])
    })
  })

  describe('updateMilestone', () => {
    it('changes only the fields given, and a boolean payment_trigger lands as 0/1', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, {
        name: 'M',
        description: 'keep me',
        payment_trigger: false,
      })
      const updated = await updateMilestone(db, ORG_A, created.id, {
        name: 'Renamed',
        payment_trigger: true,
        due_date: '2026-11-01',
      })
      expect(updated).toMatchObject({
        name: 'Renamed',
        description: 'keep me',
        payment_trigger: 1,
        due_date: '2026-11-01',
      })
    })

    it('no fields returns the existing row unchanged', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await updateMilestone(db, ORG_A, created.id, {})).toEqual(created)
    })

    it('org isolation: a write from the wrong org returns null and changes nothing', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await updateMilestone(db, ORG_B, created.id, { name: 'Forged' })).toBeNull()
      expect((await getMilestone(db, ORG_A, created.id))?.name).toBe('M')
    })
  })

  describe('updateMilestoneStatus', () => {
    it('the state machine is pending -> in_progress | skipped, in_progress -> completed | skipped, then terminal', () => {
      expect(VALID_TRANSITIONS).toEqual({
        pending: ['in_progress', 'skipped'],
        in_progress: ['completed', 'skipped'],
        completed: [],
        skipped: [],
      })
    })

    it('pending -> in_progress -> completed stamps completed_at once', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(created.completed_at).toBeNull()
      const started = await updateMilestoneStatus(db, ORG_A, created.id, 'in_progress')
      expect(started?.status).toBe('in_progress')
      expect(started?.completed_at).toBeNull()

      const before = Date.now()
      const done = await updateMilestoneStatus(db, ORG_A, created.id, 'completed')
      expect(done?.status).toBe('completed')
      expect(Date.parse(done!.completed_at!)).toBeGreaterThanOrEqual(before - 1000)
    })

    it('skipping from pending leaves completed_at empty', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      const skipped = await updateMilestoneStatus(db, ORG_A, created.id, 'skipped')
      expect(skipped?.status).toBe('skipped')
      expect(skipped?.completed_at).toBeNull()
    })

    it('refuses a transition the table does not allow, naming both states, and leaves the row alone', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      await expect(updateMilestoneStatus(db, ORG_A, created.id, 'completed')).rejects.toThrow(
        'Invalid status transition: pending -> completed'
      )
      expect((await getMilestone(db, ORG_A, created.id))?.status).toBe('pending')
    })

    it('completed and skipped are terminal: every transition out of them is refused', async () => {
      for (const terminal of ['completed', 'skipped'] as const) {
        const created = await createMilestone(db, ORG_A, ENG_A, { name: terminal })
        await db
          .prepare('UPDATE milestones SET status = ? WHERE id = ?')
          .bind(terminal, created.id)
          .run()
        for (const target of Object.keys(VALID_TRANSITIONS) as MilestoneStatus[]) {
          await expect(updateMilestoneStatus(db, ORG_A, created.id, target)).rejects.toThrow(
            'none (terminal state)'
          )
        }
      }
    })

    it('org isolation: the wrong org gets null and the status does not move', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await updateMilestoneStatus(db, ORG_B, created.id, 'in_progress')).toBeNull()
      expect((await getMilestone(db, ORG_A, created.id))?.status).toBe('pending')
    })
  })

  describe('deleteMilestone', () => {
    it('deletes the row and reports whether one was found', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await deleteMilestone(db, ORG_A, created.id)).toBe(true)
      expect(await getMilestone(db, ORG_A, created.id)).toBeNull()
      expect(await deleteMilestone(db, ORG_A, created.id)).toBe(false)
    })

    it('org isolation: the wrong org cannot delete, and the row survives', async () => {
      const created = await createMilestone(db, ORG_A, ENG_A, { name: 'M' })
      expect(await deleteMilestone(db, ORG_B, created.id)).toBe(false)
      expect(await getMilestone(db, ORG_A, created.id)).not.toBeNull()
    })
  })
})
