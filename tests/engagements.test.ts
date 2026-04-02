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

describe('engagements: create form page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/engagements/new.astro'), 'utf-8')

  it('create page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/engagements/new.astro'))).toBe(true)
  })

  it('form posts to /api/admin/engagements', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/engagements"')
  })

  it('includes hidden client_id field', () => {
    expect(source()).toContain('name="client_id"')
  })

  it('includes quote_id dropdown', () => {
    const code = source()
    expect(code).toContain('name="quote_id"')
    expect(code).toContain('Select a quote')
  })

  it('loads accepted quotes for the client', () => {
    const code = source()
    expect(code).toContain("status = 'accepted'")
    expect(code).toContain('quotes')
  })

  it('includes schedule fields', () => {
    const code = source()
    expect(code).toContain('name="start_date"')
    expect(code).toContain('name="estimated_end"')
    expect(code).toContain('name="estimated_hours"')
  })

  it('includes scope_summary field', () => {
    expect(source()).toContain('name="scope_summary"')
  })

  it('includes notes field', () => {
    expect(source()).toContain('name="notes"')
  })

  it('includes default milestones section', () => {
    const code = source()
    expect(code).toContain('milestone_name')
    expect(code).toContain('milestone_description')
    expect(code).toContain('milestone_due_date')
    expect(code).toContain('milestone_payment_trigger')
  })

  it('has add/remove milestone functionality', () => {
    const code = source()
    expect(code).toContain('Add Milestone')
    expect(code).toContain('Remove')
    expect(code).toContain('add-milestone-btn')
  })

  it('loads client data for display', () => {
    expect(source()).toContain('getClient')
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin/clients')
    expect(code).toContain('client.business_name')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('engagements: detail/edit page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/engagements/[engId].astro'), 'utf-8')

  it('detail page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/engagements/[engId].astro'))).toBe(true)
  })

  it('loads engagement via getEngagement', () => {
    expect(source()).toContain('getEngagement')
  })

  it('loads client via getClient for breadcrumb', () => {
    expect(source()).toContain('getClient')
  })

  it('form posts to /api/admin/engagements/:id', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('/api/admin/engagements/${engagement.id}')
  })

  it('shows status progression indicator', () => {
    const code = source()
    expect(code).toContain('statusOrder')
    expect(code).toContain('getProgressionColor')
  })

  it('shows status transition buttons', () => {
    const code = source()
    expect(code).toContain('Status Transition')
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('transition_status')
    expect(code).toContain('new_status')
  })

  it('displays current status badge', () => {
    const code = source()
    expect(code).toContain('ENGAGEMENT_STATUSES')
    expect(code).toContain('statusColorMap')
  })

  it('displays scope summary', () => {
    const code = source()
    expect(code).toContain('scope_summary')
    expect(code).toContain('engagement.scope_summary')
  })

  it('displays dates and hours fields', () => {
    const code = source()
    expect(code).toContain('start_date')
    expect(code).toContain('estimated_end')
    expect(code).toContain('estimated_hours')
    expect(code).toContain('actual_hours')
  })

  it('displays milestones with status badges', () => {
    const code = source()
    expect(code).toContain('listMilestones')
    expect(code).toContain('MILESTONE_STATUSES')
    expect(code).toContain('milestoneStatusColor')
  })

  it('displays milestone status transition buttons', () => {
    const code = source()
    expect(code).toContain('MILESTONE_TRANSITIONS')
    expect(code).toContain('milestone_id')
  })

  it('displays payment trigger badges on milestones', () => {
    const code = source()
    expect(code).toContain('payment_trigger')
    expect(code).toContain('Payment Trigger')
  })

  it('displays engagement milestones and time tracking', () => {
    const code = source()
    expect(code).toContain('listMilestones')
    expect(code).toContain('listTimeEntries')
  })

  it('displays safety net end date when in handoff or safety_net status', () => {
    const code = source()
    expect(code).toContain('showSafetyNet')
    expect(code).toContain('safety_net_end')
    expect(code).toContain('Safety net ends')
  })

  it('shows success message after save', () => {
    const code = source()
    expect(code).toContain("get('saved')")
    expect(code).toContain('Engagement updated successfully')
  })

  it('has breadcrumb navigation back to client', () => {
    const code = source()
    expect(code).toContain('/admin/clients/${client.id}')
    expect(code).toContain('client.business_name')
  })

  it('includes add milestone form', () => {
    const code = source()
    expect(code).toContain('Add Milestone')
    expect(code).toContain('/milestones')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('engagements: client detail page integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/[id].astro'), 'utf-8')

  it('client detail page imports listEngagements', () => {
    expect(source()).toContain('listEngagements')
  })

  it('client detail page imports ENGAGEMENT_STATUSES', () => {
    expect(source()).toContain('ENGAGEMENT_STATUSES')
  })

  it('client detail page loads engagements for this client', () => {
    const code = source()
    expect(code).toContain('listEngagements')
    expect(code).toContain('engagements')
  })

  it('client detail page has engagements section', () => {
    const code = source()
    expect(code).toContain('Engagements')
    expect(code).toContain('New Engagement')
  })

  it('client detail page links to new engagement form', () => {
    const code = source()
    expect(code).toContain('/engagements/new')
  })

  it('client detail page links to engagement detail', () => {
    const code = source()
    expect(code).toContain('/engagements/${e.id}')
  })

  it('shows engagement status in the list', () => {
    const code = source()
    expect(code).toContain('engagementStatusColor')
    expect(code).toContain('e.status')
  })

  it('shows empty state when no engagements', () => {
    const code = source()
    expect(code).toContain('No engagements yet')
    expect(code).toContain('Create the first engagement')
  })
})
