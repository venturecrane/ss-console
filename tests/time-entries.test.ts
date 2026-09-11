/**
 * Behavioural tests for time entries: the data layer
 * (src/lib/db/time-entries.ts) and its two admin routes.
 *
 * Replaces the source-text mirror of 2026-09-11 (review 2026-09-10, Testing
 * 3). The invariant that matters, that engagements.actual_hours always equals
 * the sum of the engagement's entries, is asserted on the engagement row
 * after every write. The cross-org paths of the data layer are covered in
 * tests/admin/time-entries.cross-org.test.ts and are not repeated here.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createTimeEntry,
  deleteTimeEntry,
  getTimeEntry,
  recalculateActualHours,
  updateTimeEntry,
} from '../src/lib/db/time-entries'
import { POST as createRoute } from '../src/pages/api/admin/time-entries/index'
import { POST as entryRoute } from '../src/pages/api/admin/time-entries/[id]'
import {
  adminSession,
  bindEnv,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedEngagement,
  seedEntity,
  seedOrg,
} from './_stubs/behavioural'

const ORG = 'org-a'
const ENT = 'ent-a'
const ENG = 'eng-a'

async function actualHours(db: D1Database, engagementId: string): Promise<number> {
  const row = await db
    .prepare('SELECT actual_hours FROM engagements WHERE id = ?')
    .bind(engagementId)
    .first<{ actual_hours: number }>()
  return row?.actual_hours ?? -1
}

describe('time entries against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG)
    await seedEntity(db, { id: ENT, orgId: ORG, stage: 'engaged' })
    await seedEngagement(db, { id: ENG, orgId: ORG, entityId: ENT })
    bindEnv({ DB: db })
  })

  describe('data layer', () => {
    it('createTimeEntry stores the entry and the engagement total becomes the sum', async () => {
      const first = await createTimeEntry(db, ORG, ENG, {
        date: '2026-09-01',
        hours: 2.5,
        description: 'Shadowing',
        category: 'discovery',
      })
      expect(first).toMatchObject({
        engagement_id: ENG,
        date: '2026-09-01',
        hours: 2.5,
        description: 'Shadowing',
        category: 'discovery',
      })
      expect(await getTimeEntry(db, ORG, first.id)).toEqual(first)
      expect(await actualHours(db, ENG)).toBe(2.5)

      await createTimeEntry(db, ORG, ENG, { date: '2026-09-02', hours: 1.5 })
      expect(await actualHours(db, ENG)).toBe(4)
    })

    it('updateTimeEntry changes only the given fields and re-sums the engagement', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, {
        date: '2026-09-01',
        hours: 2,
        description: 'keep',
      })
      const updated = await updateTimeEntry(db, ORG, entry.id, { hours: 5, category: 'build' })
      expect(updated).toMatchObject({ hours: 5, description: 'keep', category: 'build' })
      expect(await actualHours(db, ENG)).toBe(5)
      expect(await updateTimeEntry(db, ORG, entry.id, {})).toEqual(updated)
    })

    it('deleteTimeEntry removes the row and the engagement falls back to zero when none remain', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, { date: '2026-09-01', hours: 3 })
      expect(await deleteTimeEntry(db, ORG, entry.id)).toBe(true)
      expect(await getTimeEntry(db, ORG, entry.id)).toBeNull()
      expect(await actualHours(db, ENG)).toBe(0)
      expect(await deleteTimeEntry(db, ORG, entry.id)).toBe(false)
    })

    it('recalculateActualHours on an engagement with no entries writes 0, not NULL', async () => {
      await db.prepare('UPDATE engagements SET actual_hours = 9 WHERE id = ?').bind(ENG).run()
      await recalculateActualHours(db, ORG, ENG)
      expect(await actualHours(db, ENG)).toBe(0)
    })
  })

  describe('POST /api/admin/time-entries', () => {
    const url = 'http://test.local/api/admin/time-entries'
    const call = (fields: Record<string, string>, session = adminSession(ORG)) =>
      createRoute(
        routeContext({ request: formRequest(url, fields), session }) as unknown as Parameters<
          typeof createRoute
        >[0]
      )

    it('answers 401 with no admin session and writes nothing', async () => {
      const res = await call(
        { engagement_id: ENG, client_id: ENT, date: '2026-09-01', hours: '2' },
        null as unknown as ReturnType<typeof adminSession>
      )
      expect(res.status).toBe(401)
      expect(await actualHours(db, ENG)).toBe(0)
    })

    it('rejects a missing or non-positive hours value as error=missing', async () => {
      for (const hours of ['', '0', '-1', 'abc']) {
        const res = await call({ engagement_id: ENG, client_id: ENT, date: '2026-09-01', hours })
        expect(res.status).toBe(302)
        expect(locationQuery(res).get('error')).toBe('missing')
      }
      expect(await actualHours(db, ENG)).toBe(0)
    })

    it('an engagement outside the org reads as not_found', async () => {
      const res = await call({
        engagement_id: 'eng-elsewhere',
        client_id: ENT,
        date: '2026-09-01',
        hours: '2',
      })
      expect(locationQuery(res).get('error')).toBe('not_found')
    })

    it('creates the entry, syncs the engagement, and returns to the time page with saved=1', async () => {
      const res = await call({
        engagement_id: ENG,
        client_id: ENT,
        date: '2026-09-01',
        hours: '2.25',
        description: '  Intake redesign  ',
        category: '',
      })
      expect(res.status).toBe(302)
      expect(locationOf(res)).toBe(`/admin/entities/${ENT}/engagements/${ENG}/time?saved=1`)
      const rows = await db
        .prepare('SELECT hours, description, category FROM time_entries WHERE engagement_id = ?')
        .bind(ENG)
        .all<{ hours: number; description: string | null; category: string | null }>()
      expect(rows.results).toEqual([
        { hours: 2.25, description: 'Intake redesign', category: null },
      ])
      expect(await actualHours(db, ENG)).toBe(2.25)
    })
  })

  describe('POST /api/admin/time-entries/[id]', () => {
    const call = (id: string, fields: Record<string, string>, session = adminSession(ORG)) =>
      entryRoute(
        routeContext({
          request: formRequest(`http://test.local/api/admin/time-entries/${id}`, fields),
          params: { id },
          session,
        }) as unknown as Parameters<typeof entryRoute>[0]
      )

    it('answers 401 with no admin session', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, { date: '2026-09-01', hours: 1 })
      const res = await call(
        entry.id,
        { hours: '4' },
        null as unknown as ReturnType<typeof adminSession>
      )
      expect(res.status).toBe(401)
      expect((await getTimeEntry(db, ORG, entry.id))?.hours).toBe(1)
    })

    it('an unknown entry reads as not_found', async () => {
      const res = await call('nope', { hours: '4' })
      expect(locationQuery(res).get('error')).toBe('not_found')
    })

    it('updates the entry from the posted fields and re-syncs the engagement', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, {
        date: '2026-09-01',
        hours: 1,
        description: 'old',
      })
      const res = await call(entry.id, {
        client_id: ENT,
        hours: '4',
        description: 'new',
        category: 'build',
      })
      expect(locationOf(res)).toBe(`/admin/entities/${ENT}/engagements/${ENG}/time?saved=1`)
      expect(await getTimeEntry(db, ORG, entry.id)).toMatchObject({
        hours: 4,
        description: 'new',
        category: 'build',
      })
      expect(await actualHours(db, ENG)).toBe(4)
    })

    it('_method=DELETE removes the entry, re-syncs, and reports deleted=1', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, { date: '2026-09-01', hours: 3 })
      const res = await call(entry.id, { _method: 'DELETE', client_id: ENT })
      expect(locationOf(res)).toBe(`/admin/entities/${ENT}/engagements/${ENG}/time?deleted=1`)
      expect(await getTimeEntry(db, ORG, entry.id)).toBeNull()
      expect(await actualHours(db, ENG)).toBe(0)
    })

    it('with no client_id posted, the redirect falls back to the engagement entity', async () => {
      const entry = await createTimeEntry(db, ORG, ENG, { date: '2026-09-01', hours: 3 })
      const res = await call(entry.id, { hours: '2' })
      expect(locationOf(res)).toBe(`/admin/entities/${ENT}/engagements/${ENG}/time?saved=1`)
    })
  })
})
