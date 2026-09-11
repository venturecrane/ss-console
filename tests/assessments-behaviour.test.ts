/**
 * Behavioural tests for the assessment data layer (src/lib/db/assessments.ts).
 *
 * Nine production routes import this module and, until 2026-09-11, its only
 * test asserted on the module's SOURCE TEXT (tests/assessments.test.ts:
 * `expect(source()).toContain('org_id = ?')`), which turns red when an export
 * is renamed and stays green when the `org_id` predicate is dropped, a WHERE
 * is inverted, or a column is misspelled (2026-09-10 code review, Testing 3).
 *
 * These run the real SQL against a miniflare D1 with every numeric migration
 * applied. Each query carries an org-isolation case: a row that belongs to
 * another org must never come back, and a write scoped to the wrong org must
 * change nothing. That is the assertion the source-text test could not make.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  installWorkerdPolyfills,
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createAssessment,
  getAssessment,
  listAssessments,
  updateAssessment,
  updateAssessmentStatus,
  VALID_TRANSITIONS,
} from '../src/lib/db/assessments'

installWorkerdPolyfills()

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A1 = 'ent-a1'
const ENT_A2 = 'ent-a2'
const ENT_B1 = 'ent-b1'

async function seed(db: D1Database): Promise<void> {
  for (const org of [ORG_A, ORG_B]) {
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(org, `Org ${org}`, org)
      .run()
  }
  for (const [ent, org] of [
    [ENT_A1, ORG_A],
    [ENT_A2, ORG_A],
    [ENT_B1, ORG_B],
  ] as const) {
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(ent, org, `Entity ${ent}`, ent)
      .run()
  }
}

async function stamp(db: D1Database, id: string, createdAt: string): Promise<void> {
  await db.prepare('UPDATE assessments SET created_at = ? WHERE id = ?').bind(createdAt, id).run()
}

describe('assessments data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    await seed(db)
  })

  describe('createAssessment / getAssessment', () => {
    it('creates a scheduled assessment for the entity and reads it back', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {
        scheduled_at: '2026-10-01T15:00:00Z',
      })
      expect(created.org_id).toBe(ORG_A)
      expect(created.entity_id).toBe(ENT_A1)
      expect(created.status).toBe('scheduled')
      expect(created.scheduled_at).toBe('2026-10-01T15:00:00Z')
      expect(created.completed_at).toBeNull()
      expect(created.id).toMatch(/^[0-9a-f-]{36}$/)
      expect(await getAssessment(db, ORG_A, created.id)).toEqual(created)
    })

    it('a missing scheduled_at is stored as NULL, not the string "undefined"', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      expect(created.scheduled_at).toBeNull()
    })

    it('org isolation: another org cannot read the assessment by id', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      expect(await getAssessment(db, ORG_B, created.id)).toBeNull()
    })

    it('an unknown id reads as null', async () => {
      expect(await getAssessment(db, ORG_A, 'nope')).toBeNull()
    })
  })

  describe('listAssessments', () => {
    it("lists only the org's assessments, newest first", async () => {
      const older = await createAssessment(db, ORG_A, ENT_A1, {})
      const newer = await createAssessment(db, ORG_A, ENT_A2, {})
      const foreign = await createAssessment(db, ORG_B, ENT_B1, {})
      await stamp(db, older.id, '2026-01-01T00:00:00.000Z')
      await stamp(db, newer.id, '2026-02-01T00:00:00.000Z')

      const rows = await listAssessments(db, ORG_A)
      expect(rows.map((r) => r.id)).toEqual([newer.id, older.id])
      expect(rows.some((r) => r.id === foreign.id)).toBe(false)
    })

    it('filters by entity inside the org', async () => {
      const a1 = await createAssessment(db, ORG_A, ENT_A1, {})
      await createAssessment(db, ORG_A, ENT_A2, {})
      const rows = await listAssessments(db, ORG_A, ENT_A1)
      expect(rows.map((r) => r.id)).toEqual([a1.id])
    })

    it('org isolation: an entity filter cannot reach across orgs', async () => {
      await createAssessment(db, ORG_B, ENT_B1, {})
      expect(await listAssessments(db, ORG_A, ENT_B1)).toEqual([])
    })
  })

  describe('updateAssessment', () => {
    it('updates only the fields given and returns the fresh row', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {
        scheduled_at: '2026-10-01T15:00:00Z',
      })
      const updated = await updateAssessment(db, ORG_A, created.id, {
        duration_minutes: 45,
        live_notes: 'owner wants the intake queue fixed first',
        transcript_path: `org-a/${created.id}/transcript.txt`,
      })
      expect(updated).toMatchObject({
        id: created.id,
        duration_minutes: 45,
        live_notes: 'owner wants the intake queue fixed first',
        transcript_path: `org-a/${created.id}/transcript.txt`,
        scheduled_at: '2026-10-01T15:00:00Z',
        status: 'scheduled',
      })
    })

    it('an explicit null clears a field; an omitted field is left alone', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {
        scheduled_at: '2026-10-01T15:00:00Z',
      })
      await updateAssessment(db, ORG_A, created.id, { extraction: '{"problems":[]}' })
      const cleared = await updateAssessment(db, ORG_A, created.id, { scheduled_at: null })
      expect(cleared?.scheduled_at).toBeNull()
      expect(cleared?.extraction).toBe('{"problems":[]}')
    })

    it('no fields returns the existing row unchanged', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      expect(await updateAssessment(db, ORG_A, created.id, {})).toEqual(created)
    })

    it('org isolation: a write scoped to the wrong org changes nothing and returns null', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      const result = await updateAssessment(db, ORG_B, created.id, { live_notes: 'forged' })
      expect(result).toBeNull()
      expect((await getAssessment(db, ORG_A, created.id))?.live_notes).toBeNull()
    })
  })

  describe('updateAssessmentStatus', () => {
    it('scheduled to completed stamps completed_at once', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      const before = Date.now()
      const done = await updateAssessmentStatus(db, ORG_A, created.id, 'completed')
      expect(done?.status).toBe('completed')
      expect(Date.parse(done!.completed_at!)).toBeGreaterThanOrEqual(before - 1000)
      const converted = await updateAssessmentStatus(db, ORG_A, created.id, 'converted')
      expect(converted?.completed_at).toBe(done?.completed_at)
    })

    it('an already-set completed_at is not overwritten on completion', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      await updateAssessment(db, ORG_A, created.id, { completed_at: '2026-09-01T10:00:00Z' })
      const done = await updateAssessmentStatus(db, ORG_A, created.id, 'completed')
      expect(done?.completed_at).toBe('2026-09-01T10:00:00Z')
    })

    it('every transition in VALID_TRANSITIONS is accepted by the database CHECK', async () => {
      for (const [from, targets] of Object.entries(VALID_TRANSITIONS)) {
        for (const to of targets) {
          const created = await createAssessment(db, ORG_A, ENT_A1, {})
          await db
            .prepare('UPDATE assessments SET status = ? WHERE id = ?')
            .bind(from, created.id)
            .run()
          const moved = await updateAssessmentStatus(db, ORG_A, created.id, to)
          expect(moved?.status, `${from} -> ${to}`).toBe(to)
        }
      }
    })

    it('an invalid transition throws and leaves the row alone', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      await expect(updateAssessmentStatus(db, ORG_A, created.id, 'converted')).rejects.toThrow(
        /Invalid status transition: scheduled -> converted/
      )
      expect((await getAssessment(db, ORG_A, created.id))?.status).toBe('scheduled')
    })

    it('terminal states refuse every move', async () => {
      for (const terminal of ['disqualified', 'converted', 'cancelled'] as const) {
        const created = await createAssessment(db, ORG_A, ENT_A1, {})
        await db
          .prepare('UPDATE assessments SET status = ? WHERE id = ?')
          .bind(terminal, created.id)
          .run()
        await expect(updateAssessmentStatus(db, ORG_A, created.id, 'completed')).rejects.toThrow(
          /none \(terminal state\)/
        )
      }
    })

    it('org isolation: a status change scoped to the wrong org changes nothing and returns null', async () => {
      const created = await createAssessment(db, ORG_A, ENT_A1, {})
      expect(await updateAssessmentStatus(db, ORG_B, created.id, 'cancelled')).toBeNull()
      expect((await getAssessment(db, ORG_A, created.id))?.status).toBe('scheduled')
    })
  })
})
