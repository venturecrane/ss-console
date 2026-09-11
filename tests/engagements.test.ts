import { describe, it, expect, beforeEach, vi } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import { createEngagement, updateEngagementStatus } from '../src/lib/db/engagements'
import { createQuote } from '../src/lib/db/quotes'

describe('engagements: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/engagements.ts'), 'utf-8')

  it('engagements.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/engagements.ts'))).toBe(true)
  })

  it('exports listEngagements function', () => {
    expect(source()).toContain('export async function listEngagements')
  })

  it('exports getEngagement function', () => {
    expect(source()).toContain('export async function getEngagement')
  })

  it('exports createEngagement function', () => {
    expect(source()).toContain('export async function createEngagement')
  })

  it('exports updateEngagement function', () => {
    expect(source()).toContain('export async function updateEngagement')
  })

  it('exports updateEngagementStatus function', () => {
    expect(source()).toContain('export async function updateEngagementStatus')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('scopes all queries to org_id', () => {
    const code = source()
    expect(code).toContain("'org_id = ?'")
    expect(code).toContain('org_id = ?')
  })

  it('supports optional entity_id filter in listEngagements', () => {
    const code = source()
    expect(code).toContain("'entity_id = ?'")
  })

  it('defines all valid engagement statuses', () => {
    const code = source()
    expect(code).toContain("'scheduled'")
    expect(code).toContain("'active'")
    expect(code).toContain("'handoff'")
    expect(code).toContain("'safety_net'")
    expect(code).toContain("'completed'")
    expect(code).toContain("'cancelled'")
  })

  it('exports ENGAGEMENT_STATUSES constant', () => {
    expect(source()).toContain('export const ENGAGEMENT_STATUSES')
  })

  it('exports VALID_TRANSITIONS for status state machine', () => {
    expect(source()).toContain('export const VALID_TRANSITIONS')
  })

  it('enforces valid status transitions', () => {
    const code = source()
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('Invalid status transition')
  })

  it('defines valid transitions: scheduled -> active | cancelled', () => {
    const code = source()
    expect(code).toContain("scheduled: ['active', 'cancelled']")
  })

  it('defines valid transitions: active -> handoff | cancelled', () => {
    const code = source()
    expect(code).toContain("active: ['handoff', 'cancelled']")
  })

  it('defines valid transitions: handoff -> safety_net | cancelled', () => {
    const code = source()
    expect(code).toContain("handoff: ['safety_net', 'cancelled']")
  })

  it('defines valid transitions: safety_net -> completed | cancelled', () => {
    const code = source()
    expect(code).toContain("safety_net: ['completed', 'cancelled']")
  })

  it('completed and cancelled are terminal states', () => {
    const code = source()
    expect(code).toContain('completed: []')
    expect(code).toContain('cancelled: []')
  })

  it('auto-sets safety_net_end when transitioning to handoff', () => {
    const code = source()
    expect(code).toContain("newStatus === 'handoff'")
    expect(code).toContain('safety_net_end')
    expect(code).toContain('handoff_date')
    // 14-day calculation
    expect(code).toContain('getDate() + 14')
  })

  it('auto-sets actual_end when transitioning to completed', () => {
    const code = source()
    expect(code).toContain("newStatus === 'completed'")
    expect(code).toContain('actual_end')
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
  const migrationsDir = resolve(process.cwd(), 'migrations')
  const ORG = 'org-handoff'
  let db: D1Database

  async function seed(): Promise<{ engagementId: string; entityId: string }> {
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, 'T', 't', datetime('now'), datetime('now'))`
      )
      .bind(ORG)
      .run()
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at) VALUES ('ent-1', ?, 'ent-1', 'ent-1', 'engaged', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(ORG)
      .run()
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at) VALUES ('mtg-1', ?, 'ent-1', NULL, 'scheduled', datetime('now'))`
      )
      .bind(ORG)
      .run()
    const quote = await createQuote(db, ORG, {
      entityId: 'ent-1',
      assessmentId: 'mtg-1',
      lineItems: [],
      rate: 175,
    })
    const engagement = await createEngagement(db, ORG, { entity_id: 'ent-1', quote_id: quote.id })
    return { engagementId: engagement.id, entityId: 'ent-1' }
  }

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  })

  it('on handoff, calls the injected cadence with db, org, engagement, entity, and the handoff instant', async () => {
    const { engagementId, entityId } = await seed()
    const scheduleHandoffCadence = vi.fn(async () => {})
    const deps = { scheduleHandoffCadence }

    await updateEngagementStatus(db, ORG, engagementId, 'active', deps)
    expect(scheduleHandoffCadence).not.toHaveBeenCalled()

    const before = Date.now()
    const updated = await updateEngagementStatus(db, ORG, engagementId, 'handoff', deps)
    expect(updated?.status).toBe('handoff')
    expect(scheduleHandoffCadence).toHaveBeenCalledTimes(1)
    const [calledDb, calledOrg, calledEngagement, calledEntity, calledIso] = scheduleHandoffCadence
      .mock.calls[0] as unknown as [unknown, string, string, string, string]
    expect(calledDb).toBe(db)
    expect(calledOrg).toBe(ORG)
    expect(calledEngagement).toBe(engagementId)
    expect(calledEntity).toBe(entityId)
    expect(Date.parse(calledIso)).toBeGreaterThanOrEqual(before - 1000)
    expect(calledIso).toBe(updated?.handoff_date)
  })

  it('on handoff, sets safety_net_end fourteen days after handoff_date and promotes the entity to delivered', async () => {
    const { engagementId, entityId } = await seed()
    const deps = { scheduleHandoffCadence: vi.fn(async () => {}) }
    await updateEngagementStatus(db, ORG, engagementId, 'active', deps)
    const updated = await updateEngagementStatus(db, ORG, engagementId, 'handoff', deps)
    const handoff = Date.parse(updated?.handoff_date ?? '')
    const safetyNetEnd = Date.parse(updated?.safety_net_end ?? '')
    expect(Math.round((safetyNetEnd - handoff) / 86_400_000)).toBe(14)
    const entity = await db
      .prepare('SELECT stage FROM entities WHERE id = ? AND org_id = ?')
      .bind(entityId, ORG)
      .first<{ stage: string }>()
    expect(entity?.stage).toBe('delivered')
  })

  it('the real scheduler is what the admin route injects', () => {
    const route = readFileSync(resolve('src/pages/api/admin/engagements/[id].ts'), 'utf-8')
    expect(route).toContain(
      "import { scheduleEngagementCadence } from '../../../../lib/follow-ups/scheduler'"
    )
    expect(route).toContain('{ scheduleHandoffCadence: scheduleEngagementCadence }')
  })
})

describe('engagements: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/engagements/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/engagements/index.ts'))).toBe(true)
  })

  it('update endpoint exists at src/pages/api/admin/engagements/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/engagements/[id].ts'))).toBe(true)
  })

  it('milestones endpoint exists at src/pages/api/admin/engagements/[id]/milestones.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/engagements/[id]/milestones.ts'))).toBe(true)
  })

  it('create endpoint validates required fields', () => {
    const code = readFileSync(resolve('src/pages/api/admin/engagements/index.ts'), 'utf-8')
    expect(code).toContain('client_id')
    expect(code).toContain('quote_id')
    expect(code).toContain('createEngagement')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/engagements/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('create endpoint supports default milestones', () => {
    const code = readFileSync(resolve('src/pages/api/admin/engagements/index.ts'), 'utf-8')
    expect(code).toContain('milestone_name')
    expect(code).toContain('createMilestone')
  })

  it('update endpoint handles status transitions', () => {
    const code = readFileSync(resolve('src/pages/api/admin/engagements/[id].ts'), 'utf-8')
    expect(code).toContain('transition_status')
    expect(code).toContain('updateEngagementStatus')
  })

  it('update endpoint handles field updates', () => {
    const code = readFileSync(resolve('src/pages/api/admin/engagements/[id].ts'), 'utf-8')
    expect(code).toContain('updateEngagement')
    expect(code).toContain('scope_summary')
    expect(code).toContain('estimated_hours')
  })

  it('milestones endpoint supports create', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/engagements/[id]/milestones.ts'),
      'utf-8'
    )
    expect(code).toContain('createMilestone')
    expect(code).toContain('name')
  })

  it('milestones endpoint supports _method=DELETE', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/engagements/[id]/milestones.ts'),
      'utf-8'
    )
    expect(code).toContain('_method')
    expect(code).toContain('DELETE')
    expect(code).toContain('deleteMilestone')
  })

  it('milestones endpoint supports status transitions', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/engagements/[id]/milestones.ts'),
      'utf-8'
    )
    expect(code).toContain('transition_status')
    expect(code).toContain('updateMilestoneStatus')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/engagements/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/engagements/[id].ts'), 'utf-8')
    const milestonesCode = readFileSync(
      resolve('src/pages/api/admin/engagements/[id]/milestones.ts'),
      'utf-8'
    )
    expect(createCode).toContain('requireAdminSession')
    expect(updateCode).toContain('requireAdminSession')
    expect(milestonesCode).toContain('requireAdminSession')
  })
})
