/**
 * Behavioural tests for the parking lot (Decision Stack #11): the data layer
 * (src/lib/db/parking-lot.ts), the admin route over it, and the badge tone.
 *
 * Replaces the source-text mirror of 2026-09-11 (review 2026-09-10, Testing
 * 3). parking_lot has no org_id column, so every org-isolation case here
 * proves the JOIN through engagements does the scoping: a row under another
 * org's engagement must read as null or empty, never as forbidden.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createParkingLotItem,
  deleteParkingLotItem,
  dispositionParkingLotItem,
  DISPOSITIONS,
  getParkingLotItem,
  listParkingLot,
} from '../src/lib/db/parking-lot'
import { statusBadgeClass } from '../src/lib/ui/status-badge'
import { POST } from '../src/pages/api/admin/engagements/[id]/parking-lot'
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

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A = 'ent-a'
const ENT_B = 'ent-b'
const ENG_A = 'eng-a'
const ENG_A2 = 'eng-a2'
const ENG_B = 'eng-b'

async function stampCreated(db: D1Database, id: string, createdAt: string) {
  await db.prepare('UPDATE parking_lot SET created_at = ? WHERE id = ?').bind(createdAt, id).run()
}

async function contextRows(db: D1Database) {
  const rows = await db
    .prepare(
      `SELECT type, content, source, source_ref, metadata, engagement_id FROM context ORDER BY created_at ASC`
    )
    .all<{
      type: string
      content: string
      source: string
      source_ref: string | null
      metadata: string | null
      engagement_id: string | null
    }>()
  return rows.results
}

describe('parking lot against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: ENT_A, orgId: ORG_A, stage: 'engaged' })
    await seedEntity(db, { id: ENT_B, orgId: ORG_B, stage: 'engaged' })
    await seedEngagement(db, { id: ENG_A, orgId: ORG_A, entityId: ENT_A })
    await seedEngagement(db, { id: ENG_A2, orgId: ORG_A, entityId: ENT_A })
    await seedEngagement(db, { id: ENG_B, orgId: ORG_B, entityId: ENT_B })
    bindEnv({ DB: db })
  })

  describe('data layer', () => {
    it('DISPOSITIONS is the closed set the endpoint validates against', () => {
      expect(DISPOSITIONS).toEqual(['fold_in', 'follow_on', 'dropped'])
    })

    it('createParkingLotItem stores the request undispositioned and reads it back', async () => {
      const item = await createParkingLotItem(db, ORG_A, ENG_A, {
        description: 'Add a second intake form',
        requested_by: 'Dana',
      })
      expect(item).toMatchObject({
        engagement_id: ENG_A,
        description: 'Add a second intake form',
        requested_by: 'Dana',
        disposition: null,
        disposition_note: null,
        reviewed_at: null,
      })
      expect(await getParkingLotItem(db, ORG_A, item.id)).toEqual(item)
    })

    it('listParkingLot returns the engagement items oldest first, and only that engagement', async () => {
      const newer = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'newer' })
      const older = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'older' })
      await createParkingLotItem(db, ORG_A, ENG_A2, { description: 'sibling' })
      await stampCreated(db, older.id, '2026-01-01T00:00:00.000Z')
      await stampCreated(db, newer.id, '2026-02-01T00:00:00.000Z')

      const rows = await listParkingLot(db, ORG_A, ENG_A)
      expect(rows.map((r) => r.description)).toEqual(['older', 'newer'])
    })

    it('org isolation through the engagement JOIN: another org sees null and an empty list', async () => {
      const item = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'private' })
      expect(await getParkingLotItem(db, ORG_B, item.id)).toBeNull()
      expect(await listParkingLot(db, ORG_B, ENG_A)).toEqual([])
    })

    it('dispositionParkingLotItem sets the disposition and note and stamps reviewed_at; a second call replaces both', async () => {
      const item = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'x' })
      const before = Date.now()
      const first = await dispositionParkingLotItem(db, ORG_A, item.id, 'follow_on', 'Quote it')
      expect(first).toMatchObject({ disposition: 'follow_on', disposition_note: 'Quote it' })
      expect(Date.parse(first!.reviewed_at!)).toBeGreaterThanOrEqual(before - 1000)

      const second = await dispositionParkingLotItem(db, ORG_A, item.id, 'dropped', 'Out of scope')
      expect(second).toMatchObject({ disposition: 'dropped', disposition_note: 'Out of scope' })
      expect(Date.parse(second!.reviewed_at!)).toBeGreaterThanOrEqual(
        Date.parse(first!.reviewed_at!)
      )
    })

    it('org isolation: dispositioning from the wrong org returns null and changes nothing', async () => {
      const item = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'x' })
      expect(await dispositionParkingLotItem(db, ORG_B, item.id, 'fold_in', 'n')).toBeNull()
      expect((await getParkingLotItem(db, ORG_A, item.id))?.disposition).toBeNull()
    })

    it('deleteParkingLotItem: ok while undispositioned, dispositioned once reviewed, not_found across orgs', async () => {
      const open = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'open' })
      const reviewed = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'reviewed' })
      await dispositionParkingLotItem(db, ORG_A, reviewed.id, 'fold_in', 'in scope after all')

      expect(await deleteParkingLotItem(db, ORG_B, open.id)).toBe('not_found')
      expect(await getParkingLotItem(db, ORG_A, open.id)).not.toBeNull()

      expect(await deleteParkingLotItem(db, ORG_A, reviewed.id)).toBe('dispositioned')
      expect(await getParkingLotItem(db, ORG_A, reviewed.id)).not.toBeNull()

      expect(await deleteParkingLotItem(db, ORG_A, open.id)).toBe('ok')
      expect(await getParkingLotItem(db, ORG_A, open.id)).toBeNull()
      expect(await deleteParkingLotItem(db, ORG_A, open.id)).toBe('not_found')
    })
  })

  describe('POST /api/admin/engagements/[id]/parking-lot', () => {
    const call = (
      engagementId: string,
      fields: Record<string, string>,
      session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
    ) =>
      POST(
        routeContext({
          request: formRequest(
            `http://test.local/api/admin/engagements/${engagementId}/parking-lot`,
            fields
          ),
          params: { id: engagementId },
          session,
        }) as unknown as Parameters<typeof POST>[0]
      )

    it('answers 401 with no admin session and writes nothing', async () => {
      const res = await call(ENG_A, { description: 'x' }, null)
      expect(res.status).toBe(401)
      expect(await listParkingLot(db, ORG_A, ENG_A)).toEqual([])
    })

    it('an engagement the org does not own reads as not_found', async () => {
      const res = await call(ENG_B, { description: 'x' })
      expect(locationOf(res)).toBe('/admin/entities?error=not_found')
    })

    it('create: a blank description is error=missing; a real one lands the item and an audit entry', async () => {
      expect(locationQuery(await call(ENG_A, { description: '   ' })).get('error')).toBe('missing')

      const res = await call(ENG_A, { description: '  Second form  ', requested_by: ' Dana ' })
      expect(locationOf(res)).toBe(`/admin/engagements/${ENG_A}?parking_lot_added=1`)
      const [item] = await listParkingLot(db, ORG_A, ENG_A)
      expect(item).toMatchObject({ description: 'Second form', requested_by: 'Dana' })

      const [entry] = await contextRows(db)
      expect(entry).toMatchObject({
        type: 'parking_lot',
        content: 'Second form',
        source: 'admin',
        source_ref: `parking_lot:${item.id}:created`,
        engagement_id: ENG_A,
      })
      expect(JSON.parse(entry.metadata ?? '{}')).toEqual({ requested_by: 'Dana', item_id: item.id })
    })

    it('disposition: the value must be in DISPOSITIONS and the note must be present (Decision #11)', async () => {
      const item = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'x' })
      const base = { action: 'disposition', item_id: item.id }

      expect(
        locationQuery(
          await call(ENG_A, { ...base, disposition: 'maybe', disposition_note: 'n' })
        ).get('error')
      ).toBe('invalid_disposition')
      expect(
        locationQuery(
          await call(ENG_A, { ...base, disposition: 'fold_in', disposition_note: '  ' })
        ).get('error')
      ).toBe('missing_note')
      expect((await getParkingLotItem(db, ORG_A, item.id))?.disposition).toBeNull()

      const res = await call(ENG_A, { ...base, disposition: 'fold_in', disposition_note: ' Fits ' })
      expect(locationOf(res)).toBe(`/admin/engagements/${ENG_A}?parking_lot_dispositioned=1`)
      expect(await getParkingLotItem(db, ORG_A, item.id)).toMatchObject({
        disposition: 'fold_in',
        disposition_note: 'Fits',
      })
      const entries = await contextRows(db)
      expect(entries.at(-1)).toMatchObject({
        type: 'parking_lot',
        content: 'Dispositioned as fold_in: Fits',
        source_ref: `parking_lot:${item.id}:dispositioned`,
        engagement_id: ENG_A,
      })
    })

    it('an item on a sibling engagement of the same org is not_found from this engagement URL', async () => {
      const sibling = await createParkingLotItem(db, ORG_A, ENG_A2, { description: 'sibling' })
      const res = await call(ENG_A, {
        action: 'disposition',
        item_id: sibling.id,
        disposition: 'dropped',
        disposition_note: 'n',
      })
      expect(locationQuery(res).get('error')).toBe('not_found')
      expect((await getParkingLotItem(db, ORG_A, sibling.id))?.disposition).toBeNull()
    })

    it('delete: an undispositioned item goes with an audit entry; a dispositioned one is refused by name', async () => {
      const open = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'open' })
      const reviewed = await createParkingLotItem(db, ORG_A, ENG_A, { description: 'reviewed' })
      await dispositionParkingLotItem(db, ORG_A, reviewed.id, 'dropped', 'no')

      const refused = await call(ENG_A, { _method: 'DELETE', item_id: reviewed.id })
      expect(locationQuery(refused).get('error')).toBe('cannot_delete_dispositioned')
      expect(await getParkingLotItem(db, ORG_A, reviewed.id)).not.toBeNull()

      const res = await call(ENG_A, { _method: 'DELETE', item_id: open.id })
      expect(locationOf(res)).toBe(`/admin/engagements/${ENG_A}?parking_lot_deleted=1`)
      expect(await getParkingLotItem(db, ORG_A, open.id)).toBeNull()
      expect((await contextRows(db)).at(-1)).toMatchObject({
        content: 'Deleted parking lot item: open',
        source_ref: `parking_lot:${open.id}:deleted`,
      })
    })
  })

  describe('status badge tone', () => {
    it('each disposition has its own tone, distinct from the unknown-status fallback', () => {
      const fallback = statusBadgeClass('no-such-status')
      const tones = DISPOSITIONS.map((d) => statusBadgeClass(d))
      expect(new Set(tones).size).toBe(DISPOSITIONS.length)
      expect(tones.filter((t) => t === fallback)).toHaveLength(1)
      expect(statusBadgeClass('dropped')).toBe(fallback)
    })
  })
})

// The engagement detail page mounts EngagementParkingLotPanel with the rows
// the loader reads. Astro pages have no handler to invoke, so this stays a
// composition drift guard, deliberately small.
describe('parking lot: engagement detail composition (drift guard)', () => {
  it('the detail page mounts the panel and the panel posts to the route above', async () => {
    const { readFileSync } = await import('fs')
    const { resolve } = await import('path')
    const page = readFileSync(resolve('src/pages/admin/engagements/[id].astro'), 'utf-8')
    const panel = readFileSync(
      resolve('src/components/admin/EngagementParkingLotPanel.astro'),
      'utf-8'
    )
    expect(page).toContain('<EngagementParkingLotPanel')
    expect(panel).toContain('/parking-lot')
  })
})
