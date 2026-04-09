import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

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
  const source = () => readFileSync(resolve('src/lib/db/engagements.ts'), 'utf-8')

  it('imports scheduleEngagementCadence from scheduler', () => {
    expect(source()).toContain(
      "import { scheduleEngagementCadence } from '../follow-ups/scheduler'"
    )
  })

  it('imports transitionStage from entities', () => {
    expect(source()).toContain("import { transitionStage } from './entities'")
  })

  it('calls scheduleEngagementCadence on handoff transition', () => {
    const code = source()
    expect(code).toContain('scheduleEngagementCadence(')
    expect(code).toContain('existing.entity_id')
  })

  it('passes correct args to scheduleEngagementCadence: db, orgId, engagementId, entityId, handoffDate', () => {
    const code = source()
    expect(code).toContain(
      'scheduleEngagementCadence(\n      db,\n      orgId,\n      engagementId,\n      existing.entity_id,\n      handoffDate.toISOString()\n    )'
    )
  })

  it('calls transitionStage to delivered on handoff', () => {
    const code = source()
    expect(code).toContain("transitionStage(db, orgId, existing.entity_id, 'delivered'")
  })

  it('safety_net_end is set to handoff_date + 14 days on handoff', () => {
    const code = source()
    expect(code).toContain("newStatus === 'handoff'")
    expect(code).toContain('safety_net_end')
    expect(code).toContain('getDate() + 14')
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
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
    expect(milestonesCode).toContain("session.role !== 'admin'")
  })
})
