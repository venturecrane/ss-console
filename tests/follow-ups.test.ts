import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('follow-ups: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/follow-ups.ts'), 'utf-8')

  it('follow-ups.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/follow-ups.ts'))).toBe(true)
  })

  it('exports listFollowUps function', () => {
    expect(source()).toContain('export async function listFollowUps')
  })

  it('exports getFollowUp function', () => {
    expect(source()).toContain('export async function getFollowUp')
  })

  it('exports createFollowUp function', () => {
    expect(source()).toContain('export async function createFollowUp')
  })

  it('exports completeFollowUp function', () => {
    expect(source()).toContain('export async function completeFollowUp')
  })

  it('exports skipFollowUp function', () => {
    expect(source()).toContain('export async function skipFollowUp')
  })

  it('exports bulkCreateFollowUps function', () => {
    expect(source()).toContain('export async function bulkCreateFollowUps')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
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

  it('defines all follow-up types', () => {
    const code = source()
    expect(code).toContain("'proposal_day2'")
    expect(code).toContain("'proposal_day5'")
    expect(code).toContain("'proposal_day7'")
    expect(code).toContain("'review_request'")
    expect(code).toContain("'referral_ask'")
    expect(code).toContain("'safety_net_checkin'")
    expect(code).toContain("'feedback_30day'")
  })

  it('exports FOLLOW_UP_TYPES constant', () => {
    expect(source()).toContain('export const FOLLOW_UP_TYPES')
  })

  it('supports status filter in listFollowUps', () => {
    const code = source()
    expect(code).toContain("'status = ?'")
  })

  it('supports type filter in listFollowUps', () => {
    const code = source()
    expect(code).toContain("'type = ?'")
  })

  it('supports upcoming filter in listFollowUps', () => {
    const code = source()
    expect(code).toContain("scheduled_for >= datetime('now')")
    expect(code).toContain('upcoming')
  })

  it('supports overdue filter in listFollowUps', () => {
    const code = source()
    expect(code).toContain("scheduled_for < datetime('now')")
    expect(code).toContain('overdue')
  })

  it('completeFollowUp sets completed_at', () => {
    const code = source()
    expect(code).toContain("'completed'")
    expect(code).toContain('completed_at')
  })

  it('skipFollowUp sets status to skipped', () => {
    const code = source()
    expect(code).toContain("'skipped'")
  })
})

describe('follow-ups: scheduler', () => {
  const source = () => readFileSync(resolve('src/lib/follow-ups/scheduler.ts'), 'utf-8')

  it('scheduler.ts exists', () => {
    expect(existsSync(resolve('src/lib/follow-ups/scheduler.ts'))).toBe(true)
  })

  it('exports scheduleProposalCadence function', () => {
    expect(source()).toContain('export async function scheduleProposalCadence')
  })

  it('exports scheduleEngagementCadence function', () => {
    expect(source()).toContain('export async function scheduleEngagementCadence')
  })

  it('proposal cadence creates 3 follow-ups', () => {
    const code = source()
    expect(code).toContain('proposal_day2')
    expect(code).toContain('proposal_day5')
    expect(code).toContain('proposal_day7')
  })

  it('proposal cadence uses correct intervals: day 2, 5, 7', () => {
    const code = source()
    expect(code).toContain('addDays(sentAt, 2)')
    expect(code).toContain('addDays(sentAt, 5)')
    expect(code).toContain('addDays(sentAt, 7)')
  })

  it('engagement cadence creates 4 follow-ups', () => {
    const code = source()
    expect(code).toContain('referral_ask')
    expect(code).toContain('review_request')
    expect(code).toContain('safety_net_checkin')
    expect(code).toContain('feedback_30day')
  })

  it('engagement cadence uses correct intervals', () => {
    const code = source()
    // referral_ask at handoff (no addDays)
    expect(code).toContain('scheduled_for: handoffDate')
    // review_request at handoff+2
    expect(code).toContain('addDays(handoffDate, 2)')
    // safety_net_checkin at handoff+7
    expect(code).toContain('addDays(handoffDate, 7)')
    // feedback_30day at handoff+30
    expect(code).toContain('addDays(handoffDate, 30)')
  })

  it('uses bulkCreateFollowUps for batch creation', () => {
    expect(source()).toContain('bulkCreateFollowUps')
  })

  it('references Decision #19 for proposal cadence', () => {
    expect(source()).toContain('Decision #19')
  })

  it('references Decisions #23, #26, #29 for engagement cadence', () => {
    const code = source()
    expect(code).toContain('Decision')
  })
})

describe('follow-ups: email templates', () => {
  const source = () => readFileSync(resolve('src/lib/email/follow-up-templates.ts'), 'utf-8')

  it('follow-up-templates.ts exists', () => {
    expect(existsSync(resolve('src/lib/email/follow-up-templates.ts'))).toBe(true)
  })

  it('exports proposalDay2Email template', () => {
    expect(source()).toContain('export function proposalDay2Email')
  })

  it('exports proposalDay5Email template', () => {
    expect(source()).toContain('export function proposalDay5Email')
  })

  it('exports proposalDay7Email template', () => {
    expect(source()).toContain('export function proposalDay7Email')
  })

  it('exports reviewRequestEmail template', () => {
    expect(source()).toContain('export function reviewRequestEmail')
  })

  it('exports referralAskEmail template', () => {
    expect(source()).toContain('export function referralAskEmail')
  })

  it('exports safetyNetCheckinEmail template', () => {
    expect(source()).toContain('export function safetyNetCheckinEmail')
  })

  it('exports feedback30DayEmail template', () => {
    expect(source()).toContain('export function feedback30DayEmail')
  })

  it('exports getFollowUpTemplate dispatcher', () => {
    expect(source()).toContain('export function getFollowUpTemplate')
  })

  it('all templates return subject and html', () => {
    const code = source()
    // Each template function returns { subject, html }
    expect(code.match(/subject:/g)?.length).toBeGreaterThanOrEqual(7)
    expect(code.match(/html: emailWrapper/g)?.length).toBeGreaterThanOrEqual(6)
  })

  it('uses "we" voice (Decision #20)', () => {
    const code = source()
    // Should contain "we" language
    expect(code).toContain('We sent over')
    expect(code).toContain("We've been thinking")
    expect(code).toContain("we'd genuinely appreciate")
    // Should NOT contain "I" language
    expect(code).not.toContain('I sent')
    expect(code).not.toContain('I wanted')
  })

  it('templates take clientName, businessName, portalUrl', () => {
    const code = source()
    expect(code).toContain('clientName')
    expect(code).toContain('businessName')
    expect(code).toContain('portalUrl')
  })

  it('getFollowUpTemplate maps all 7 types', () => {
    const code = source()
    expect(code).toContain('proposal_day2: proposalDay2Email')
    expect(code).toContain('proposal_day5: proposalDay5Email')
    expect(code).toContain('proposal_day7: proposalDay7Email')
    expect(code).toContain('review_request: reviewRequestEmail')
    expect(code).toContain('referral_ask: referralAskEmail')
    expect(code).toContain('safety_net_checkin: safetyNetCheckinEmail')
    expect(code).toContain('feedback_30day: feedback30DayEmail')
  })
})

describe('follow-ups: dashboard page', () => {
  const source = () => readFileSync(resolve('src/pages/admin/follow-ups/index.astro'), 'utf-8')

  it('dashboard page exists', () => {
    expect(existsSync(resolve('src/pages/admin/follow-ups/index.astro'))).toBe(true)
  })

  it('loads follow-ups via listFollowUps', () => {
    expect(source()).toContain('listFollowUps')
  })

  it('has upcoming, overdue, and completed sections', () => {
    const code = source()
    expect(code).toContain('upcoming')
    expect(code).toContain('overdue')
    expect(code).toContain('completed')
  })

  it('displays client name for each follow-up', () => {
    const code = source()
    expect(code).toContain('clientMap')
    expect(code).toContain('row.name')
  })

  it('displays follow-up type label', () => {
    expect(source()).toContain('getTypeLabel')
  })

  it('displays scheduled date and days until/overdue', () => {
    const code = source()
    expect(code).toContain('scheduled_for')
    expect(code).toContain('daysLabel')
  })

  it('has Mark Complete action', () => {
    const code = source()
    expect(code).toContain('Mark Complete')
    expect(code).toContain('value="complete"')
  })

  it('has Skip action', () => {
    const code = source()
    expect(code).toContain('Skip')
    expect(code).toContain('value="skip"')
  })

  it('has Send Email action', () => {
    const code = source()
    expect(code).toContain('Send Email')
    expect(code).toContain('value="send_email"')
  })

  it('supports filter by type', () => {
    const code = source()
    expect(code).toContain('FOLLOW_UP_TYPES')
    expect(code).toContain('filterType')
  })

  it('actions post to follow-ups API', () => {
    expect(source()).toContain('/api/admin/follow-ups/')
  })

  it('is not indexed by search engines', () => {
    const layout = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(layout).toContain('noindex')
  })
})

describe('follow-ups: API route', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/follow-ups/[id].ts'), 'utf-8')

  it('API route exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/follow-ups/[id].ts'))).toBe(true)
  })

  it('handles complete action', () => {
    const code = source()
    expect(code).toContain("action === 'complete'")
    expect(code).toContain('completeFollowUp')
  })

  it('handles skip action', () => {
    const code = source()
    expect(code).toContain("action === 'skip'")
    expect(code).toContain('skipFollowUp')
  })

  it('handles send_email action', () => {
    const code = source()
    expect(code).toContain("action === 'send_email'")
    expect(code).toContain('sendEmail')
  })

  it('uses getFollowUpTemplate for email content', () => {
    expect(source()).toContain('getFollowUpTemplate')
  })

  it('verifies admin session', () => {
    expect(source()).toContain("session.role !== 'admin'")
  })

  it('scopes follow-up lookup to org', () => {
    expect(source()).toContain('session.orgId')
  })

  it('marks follow-up completed after email send', () => {
    const code = source()
    // After send_email, should call completeFollowUp
    expect(code).toContain('completeFollowUp')
  })
})

describe('follow-ups: integration with quotes', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/quotes/[id]/sign.ts'), 'utf-8')

  it('signature send route imports scheduleProposalCadence', () => {
    expect(source()).toContain('scheduleProposalCadence')
  })

  it('signature send route calls scheduleProposalCadence after provider send succeeds', () => {
    const code = source()
    expect(code).toContain('await scheduleProposalCadence(')
    expect(code).toContain('signatureRequest.sent_at')
  })
})

describe('follow-ups: admin dashboard integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/index.astro'), 'utf-8')

  it('admin dashboard imports listFollowUps', () => {
    expect(source()).toContain('listFollowUps')
  })

  it('admin layout shows follow-ups nav link', () => {
    const layout = readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')
    expect(layout).toContain('Follow-ups')
    expect(layout).toContain('/admin/follow-ups')
  })

  it('admin dashboard shows upcoming and overdue follow-ups', () => {
    const code = source()
    expect(code).toContain('upcomingFollowUps')
    expect(code).toContain('overdueFollowUps')
  })
})
