/**
 * Behavioural tests for engagements: the data layer
 * (src/lib/db/engagements.ts) and the two admin routes over it.
 *
 * Until 2026-09-11 most of this file matched the module's SOURCE TEXT (review
 * 2026-09-10, Testing 3). Everything below runs the real SQL against a
 * migrated D1 and the real route handlers against a fake Astro context. The
 * handoff wiring suite predates the rewrite and is kept as it was: it is the
 * contract between the data layer and the follow-up policy it is handed.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createEngagement,
  ENGAGEMENT_STATUSES,
  getActiveEngagementForEntities,
  getEngagement,
  listEngagements,
  updateEngagement,
  updateEngagementStatus,
  VALID_TRANSITIONS,
  type EngagementStatus,
} from '../src/lib/db/engagements'
import { listMilestones } from '../src/lib/db/milestones'
import { POST as createRoute } from '../src/pages/api/admin/engagements/index'
import { POST as updateRoute } from '../src/pages/api/admin/engagements/[id]'
import {
  adminSession,
  bindEnv,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedEntity,
  seedOrg,
  seedQuote,
} from './_stubs/behavioural'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A1 = 'ent-a1'
const ENT_A2 = 'ent-a2'
const ENT_B1 = 'ent-b1'
const QUOTE_A1 = 'quote-a1'
const QUOTE_A2 = 'quote-a2'
const QUOTE_B1 = 'quote-b1'

const noCadence = { scheduleHandoffCadence: vi.fn(async () => {}) }

async function stampCreated(db: D1Database, id: string, createdAt: string) {
  await db.prepare('UPDATE engagements SET created_at = ? WHERE id = ?').bind(createdAt, id).run()
}

async function seedAll(db: D1Database) {
  await seedOrg(db, ORG_A)
  await seedOrg(db, ORG_B)
  await seedEntity(db, { id: ENT_A1, orgId: ORG_A, stage: 'engaged' })
  await seedEntity(db, { id: ENT_A2, orgId: ORG_A, stage: 'engaged' })
  await seedEntity(db, { id: ENT_B1, orgId: ORG_B, stage: 'engaged' })
  await seedQuote(db, { id: QUOTE_A1, orgId: ORG_A, entityId: ENT_A1 })
  await seedQuote(db, { id: QUOTE_A2, orgId: ORG_A, entityId: ENT_A2 })
  await seedQuote(db, { id: QUOTE_B1, orgId: ORG_B, entityId: ENT_B1 })
}

describe('engagements data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
  })

  describe('createEngagement / getEngagement', () => {
    it('is born scheduled, carries the given fields, and reads back by id', async () => {
      const created = await createEngagement(db, ORG_A, {
        entity_id: ENT_A1,
        quote_id: QUOTE_A1,
        scope_summary: 'Intake redesign',
        start_date: '2026-10-01',
        estimated_end: '2026-11-15',
        estimated_hours: 40,
      })
      expect(created).toMatchObject({
        org_id: ORG_A,
        entity_id: ENT_A1,
        quote_id: QUOTE_A1,
        status: 'scheduled',
        scope_summary: 'Intake redesign',
        start_date: '2026-10-01',
        estimated_end: '2026-11-15',
        estimated_hours: 40,
        actual_hours: 0,
        handoff_date: null,
        actual_end: null,
      })
      expect(await getEngagement(db, ORG_A, created.id)).toEqual(created)
    })

    it('creates the consulting service spine row in the same batch (ADR 0046)', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      expect(created.service_id).toMatch(/^svc_/)
      const service = await db
        .prepare(
          'SELECT org_id, entity_id, quote_id, type, cadence, status FROM services WHERE id = ?'
        )
        .bind(created.service_id)
        .first()
      expect(service).toEqual({
        org_id: ORG_A,
        entity_id: ENT_A1,
        quote_id: QUOTE_A1,
        type: 'consulting',
        cadence: 'one_time',
        status: 'active',
      })
    })

    it('an entity with no signal on file is created unattributed (originating_signal_id NULL)', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      expect(created.originating_signal_id).toBeNull()
    })

    it('org isolation: another org cannot read the engagement by id', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      expect(await getEngagement(db, ORG_B, created.id)).toBeNull()
    })
  })

  describe('listEngagements', () => {
    it("lists only the org's engagements, newest first, with an optional entity filter", async () => {
      const older = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      const newer = await createEngagement(db, ORG_A, { entity_id: ENT_A2, quote_id: QUOTE_A2 })
      const foreign = await createEngagement(db, ORG_B, { entity_id: ENT_B1, quote_id: QUOTE_B1 })
      await stampCreated(db, older.id, '2026-01-01T00:00:00.000Z')
      await stampCreated(db, newer.id, '2026-02-01T00:00:00.000Z')

      expect((await listEngagements(db, ORG_A)).map((e) => e.id)).toEqual([newer.id, older.id])
      expect((await listEngagements(db, ORG_A, ENT_A1)).map((e) => e.id)).toEqual([older.id])
      expect((await listEngagements(db, ORG_A)).some((e) => e.id === foreign.id)).toBe(false)
      expect(await listEngagements(db, ORG_A, ENT_B1)).toEqual([])
    })
  })

  describe('updateEngagement', () => {
    it('changes only the fields given; an explicit null clears, an omitted field stays', async () => {
      const created = await createEngagement(db, ORG_A, {
        entity_id: ENT_A1,
        quote_id: QUOTE_A1,
        scope_summary: 'keep',
        estimated_hours: 40,
      })
      const updated = await updateEngagement(db, ORG_A, created.id, {
        estimated_hours: null,
        consultant_name: 'Sam',
        next_touchpoint_label: 'Kickoff walk-through',
      })
      expect(updated).toMatchObject({
        scope_summary: 'keep',
        estimated_hours: null,
        consultant_name: 'Sam',
        next_touchpoint_label: 'Kickoff walk-through',
      })
      expect(await updateEngagement(db, ORG_A, created.id, {})).toEqual(updated)
    })

    it('org isolation: a write from the wrong org returns null and changes nothing', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      expect(await updateEngagement(db, ORG_B, created.id, { scope_summary: 'forged' })).toBeNull()
      expect((await getEngagement(db, ORG_A, created.id))?.scope_summary).toBeNull()
    })
  })

  describe('updateEngagementStatus', () => {
    it('the vocabulary and the state machine are the six statuses and their allowed moves', () => {
      expect(ENGAGEMENT_STATUSES.map((s) => s.value)).toEqual([
        'scheduled',
        'active',
        'handoff',
        'safety_net',
        'completed',
        'cancelled',
      ])
      expect(VALID_TRANSITIONS).toEqual({
        scheduled: ['active', 'cancelled'],
        active: ['handoff', 'cancelled'],
        handoff: ['safety_net', 'cancelled'],
        safety_net: ['completed', 'cancelled'],
        completed: [],
        cancelled: [],
      })
    })

    it('refuses a move the table does not allow, naming both states, and leaves the row alone', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      await expect(
        updateEngagementStatus(db, ORG_A, created.id, 'handoff', noCadence)
      ).rejects.toThrow('Invalid status transition: scheduled -> handoff')
      expect((await getEngagement(db, ORG_A, created.id))?.status).toBe('scheduled')
    })

    it('completed and cancelled are terminal', async () => {
      for (const terminal of ['completed', 'cancelled'] as const) {
        const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
        await db
          .prepare('UPDATE engagements SET status = ? WHERE id = ?')
          .bind(terminal, created.id)
          .run()
        for (const target of Object.keys(VALID_TRANSITIONS) as EngagementStatus[]) {
          await expect(
            updateEngagementStatus(db, ORG_A, created.id, target, noCadence)
          ).rejects.toThrow('none (terminal state)')
        }
      }
    })

    it('reaching completed stamps actual_end once and keeps an authored one', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      await db
        .prepare("UPDATE engagements SET status = 'safety_net' WHERE id = ?")
        .bind(created.id)
        .run()
      const before = Date.now()
      const done = await updateEngagementStatus(db, ORG_A, created.id, 'completed', noCadence)
      expect(done?.status).toBe('completed')
      expect(Date.parse(done!.actual_end!)).toBeGreaterThanOrEqual(before - 1000)

      const authored = await createEngagement(db, ORG_A, { entity_id: ENT_A2, quote_id: QUOTE_A2 })
      await db
        .prepare("UPDATE engagements SET status = 'safety_net', actual_end = ? WHERE id = ?")
        .bind('2026-08-01T00:00:00.000Z', authored.id)
        .run()
      const kept = await updateEngagementStatus(db, ORG_A, authored.id, 'completed', noCadence)
      expect(kept?.actual_end).toBe('2026-08-01T00:00:00.000Z')
    })

    it('org isolation: the wrong org gets null and the status does not move', async () => {
      const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      expect(await updateEngagementStatus(db, ORG_B, created.id, 'active', noCadence)).toBeNull()
      expect((await getEngagement(db, ORG_A, created.id))?.status).toBe('scheduled')
    })
  })

  describe('getActiveEngagementForEntities', () => {
    it('returns the most recent non-terminal engagement per entity, org-scoped; empty input is an empty map', async () => {
      expect((await getActiveEngagementForEntities(db, ORG_A, [])).size).toBe(0)

      const older = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      const newer = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
      const cancelled = await createEngagement(db, ORG_A, { entity_id: ENT_A2, quote_id: QUOTE_A2 })
      await createEngagement(db, ORG_B, { entity_id: ENT_B1, quote_id: QUOTE_B1 })
      await stampCreated(db, older.id, '2026-01-01T00:00:00.000Z')
      await stampCreated(db, newer.id, '2026-02-01T00:00:00.000Z')
      await db
        .prepare("UPDATE engagements SET status = 'cancelled' WHERE id = ?")
        .bind(cancelled.id)
        .run()

      const map = await getActiveEngagementForEntities(db, ORG_A, [ENT_A1, ENT_A2, ENT_B1])
      expect(map.get(ENT_A1)?.id).toBe(newer.id)
      expect(map.has(ENT_A2)).toBe(false)
      expect(map.has(ENT_B1)).toBe(false)
    })
  })
})

describe('engagements: handoff wiring', () => {
  // Behavioural, against a real D1, because the thing under test is a
  // contract between layers: the data layer sets the handoff dates and
  // promotes the entity, and the follow-up cadence is INJECTED by the caller
  // that owns that policy (src/lib/follow-ups/scheduler.ts). Until
  // 2026-09-10 the data layer imported the scheduler directly, which the
  // 2026-09-10 code review flagged as one of four upward edges out of
  // src/lib/db; tests/db-layer-boundary.test.ts now keeps the edge out, and
  // this suite keeps the behaviour in.
  let db: D1Database

  async function seed(): Promise<{ engagementId: string; entityId: string }> {
    await seedAll(db)
    const engagement = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
    return { engagementId: engagement.id, entityId: ENT_A1 }
  }

  beforeEach(async () => {
    db = await migratedDb()
  })

  it('on handoff, calls the injected cadence with db, org, engagement, entity, and the handoff instant', async () => {
    const { engagementId, entityId } = await seed()
    const scheduleHandoffCadence = vi.fn(async () => {})
    const deps = { scheduleHandoffCadence }

    await updateEngagementStatus(db, ORG_A, engagementId, 'active', deps)
    expect(scheduleHandoffCadence).not.toHaveBeenCalled()

    const before = Date.now()
    const updated = await updateEngagementStatus(db, ORG_A, engagementId, 'handoff', deps)
    expect(updated?.status).toBe('handoff')
    expect(scheduleHandoffCadence).toHaveBeenCalledTimes(1)
    const [calledDb, calledOrg, calledEngagement, calledEntity, calledIso] = scheduleHandoffCadence
      .mock.calls[0] as unknown as [unknown, string, string, string, string]
    expect(calledDb).toBe(db)
    expect(calledOrg).toBe(ORG_A)
    expect(calledEngagement).toBe(engagementId)
    expect(calledEntity).toBe(entityId)
    expect(Date.parse(calledIso)).toBeGreaterThanOrEqual(before - 1000)
    expect(calledIso).toBe(updated?.handoff_date)
  })

  it('on handoff, sets safety_net_end fourteen days after handoff_date and promotes the entity to delivered', async () => {
    const { engagementId, entityId } = await seed()
    const deps = { scheduleHandoffCadence: vi.fn(async () => {}) }
    await updateEngagementStatus(db, ORG_A, engagementId, 'active', deps)
    const updated = await updateEngagementStatus(db, ORG_A, engagementId, 'handoff', deps)
    const handoff = Date.parse(updated?.handoff_date ?? '')
    const safetyNetEnd = Date.parse(updated?.safety_net_end ?? '')
    expect(Math.round((safetyNetEnd - handoff) / 86_400_000)).toBe(14)
    const entity = await db
      .prepare('SELECT stage FROM entities WHERE id = ? AND org_id = ?')
      .bind(entityId, ORG_A)
      .first<{ stage: string }>()
    expect(entity?.stage).toBe('delivered')
  })
})

describe('POST /api/admin/engagements and /api/admin/engagements/[id]', () => {
  let db: D1Database

  const create = (
    fields: Record<string, string | string[]>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    createRoute(
      routeContext({
        request: formRequest('http://test.local/api/admin/engagements', fields),
        session,
      }) as unknown as Parameters<typeof createRoute>[0]
    )

  const update = (
    id: string,
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    updateRoute(
      routeContext({
        request: formRequest(`http://test.local/api/admin/engagements/${id}`, fields),
        params: { id },
        session,
      }) as unknown as Parameters<typeof updateRoute>[0]
    )

  beforeEach(async () => {
    db = await migratedDb()
    await seedAll(db)
    bindEnv({ DB: db })
  })

  it('create: 401 with no session; error=missing without client and quote; nothing written either way', async () => {
    expect((await create({ client_id: ENT_A1, quote_id: QUOTE_A1 }, null)).status).toBe(401)
    expect(locationQuery(await create({ client_id: ENT_A1 })).get('error')).toBe('missing')
    expect(await listEngagements(db, ORG_A)).toEqual([])
  })

  it('create: writes the engagement from the form plus its repeatable milestones, then lands on its detail page', async () => {
    const res = await create({
      client_id: ENT_A1,
      quote_id: QUOTE_A1,
      start_date: '2026-10-01',
      estimated_hours: '40',
      scope_summary: ' Intake redesign ',
      milestone_name: ['Discovery', '', 'Handoff'],
      milestone_description: ['Shadow the desk', '', ''],
      milestone_due_date: ['2026-10-10', '', '2026-11-15'],
      milestone_payment_trigger: ['', '', 'on'],
    })
    expect(res.status).toBe(302)
    const [engagement] = await listEngagements(db, ORG_A)
    expect(locationOf(res)).toBe(`/admin/engagements/${engagement.id}`)
    expect(engagement).toMatchObject({
      entity_id: ENT_A1,
      quote_id: QUOTE_A1,
      start_date: '2026-10-01',
      estimated_hours: 40,
      scope_summary: 'Intake redesign',
      status: 'scheduled',
    })
    const milestones = await listMilestones(db, ORG_A, engagement.id)
    expect(
      milestones.map((m) => [m.name, m.description, m.due_date, m.payment_trigger, m.sort_order])
    ).toEqual([
      ['Discovery', 'Shadow the desk', '2026-10-10', 0, 0],
      ['Handoff', null, '2026-11-15', 1, 2],
    ])
  })

  it('update: an engagement outside the org is not_found', async () => {
    const foreign = await createEngagement(db, ORG_B, { entity_id: ENT_B1, quote_id: QUOTE_B1 })
    expect(locationOf(await update(foreign.id, { scope_summary: 'x' }))).toBe(
      '/admin/entities?error=not_found'
    )
    expect((await getEngagement(db, ORG_B, foreign.id))?.scope_summary).toBeNull()
  })

  it('update: field edits land and answer saved=1; a whitespace scope clears it', async () => {
    const created = await createEngagement(db, ORG_A, {
      entity_id: ENT_A1,
      quote_id: QUOTE_A1,
      scope_summary: 'old',
    })
    const res = await update(created.id, {
      scope_summary: '   ',
      start_date: '2026-10-02',
      estimated_hours: '12.5',
      actual_hours: '',
    })
    expect(locationOf(res)).toBe(`/admin/engagements/${created.id}?saved=1`)
    expect(await getEngagement(db, ORG_A, created.id)).toMatchObject({
      scope_summary: null,
      start_date: '2026-10-02',
      estimated_hours: 12.5,
      actual_hours: null,
    })
  })

  it('update: a disallowed transition is reported as invalid_transition and nothing moves', async () => {
    const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
    const res = await update(created.id, { action: 'transition_status', new_status: 'completed' })
    expect(locationQuery(res).get('error')).toBe('invalid_transition')
    expect((await getEngagement(db, ORG_A, created.id))?.status).toBe('scheduled')
  })

  it('update: moving to handoff through the route schedules the real four-touch cadence and promotes the entity', async () => {
    const created = await createEngagement(db, ORG_A, { entity_id: ENT_A1, quote_id: QUOTE_A1 })
    await update(created.id, { action: 'transition_status', new_status: 'active' })
    const res = await update(created.id, { action: 'transition_status', new_status: 'handoff' })
    expect(locationOf(res)).toBe(`/admin/engagements/${created.id}?saved=1`)

    const followUps = await db
      .prepare(
        'SELECT type FROM follow_ups WHERE org_id = ? AND engagement_id = ? ORDER BY scheduled_for ASC'
      )
      .bind(ORG_A, created.id)
      .all<{ type: string }>()
    expect(followUps.results.map((r) => r.type)).toEqual([
      'referral_ask',
      'review_request',
      'safety_net_checkin',
      'feedback_30day',
    ])
    const entity = await db
      .prepare('SELECT stage FROM entities WHERE id = ?')
      .bind(ENT_A1)
      .first<{ stage: string }>()
    expect(entity?.stage).toBe('delivered')
  })
})
