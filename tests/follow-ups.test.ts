/**
 * Behavioural tests for follow-ups: the data layer (src/lib/db/follow-ups.ts),
 * the cadence scheduler (src/lib/follow-ups/scheduler.ts), the email templates
 * (src/lib/email/follow-up-templates.ts), and the admin action route.
 *
 * Until 2026-09-11 this file matched source text (review 2026-09-10, Testing
 * 3). The cadences are now asserted as rows with the expected dates, the
 * templates as rendered output, and the route as redirects and row changes,
 * with the email transport as the only fake. The proposal cadence's trigger
 * from the send-for-signature route is covered in tests/signwell.test.ts.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  bulkCreateFollowUps,
  completeFollowUp,
  createFollowUp,
  FOLLOW_UP_TYPES,
  getFollowUp,
  listFollowUpEntityNames,
  listFollowUps,
  skipFollowUp,
} from '../src/lib/db/follow-ups'
import {
  scheduleEngagementCadence,
  scheduleProposalCadence,
  scheduleProspectCadence,
} from '../src/lib/follow-ups/scheduler'
import {
  feedback30DayEmail,
  getFollowUpTemplate,
  proposalDay2Email,
  proposalDay5Email,
  proposalDay7Email,
  referralAskEmail,
  reviewRequestEmail,
  safetyNetCheckinEmail,
} from '../src/lib/email/follow-up-templates'
import { createContact } from '../src/lib/db/contacts'
import {
  adminSession,
  bindEnv,
  daysFrom,
  formRequest,
  locationOf,
  locationQuery,
  migratedDb,
  routeContext,
  seedEngagement,
  seedEntity,
  seedOrg,
  seedQuote,
} from './_stubs/behavioural'

const sendEmail = vi.fn()
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: (...args: unknown[]) => sendEmail(...args),
}))

// Import AFTER the mock so the route binds the mocked transport.
import { POST } from '../src/pages/api/admin/follow-ups/[id]'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A = 'ent-a'
const ENT_B = 'ent-b'
const PAST = '2026-01-01T00:00:00.000Z'
const FUTURE = '2036-01-01T00:00:00.000Z'

describe('follow-ups data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: ENT_A, orgId: ORG_A, name: 'Alpha Plumbing' })
    await seedEntity(db, { id: ENT_B, orgId: ORG_B, name: 'Beta Legal' })
  })

  it('FOLLOW_UP_TYPES carries the thirteen typed moments, each with a label', () => {
    expect(FOLLOW_UP_TYPES.map((t) => t.value)).toEqual([
      'initial_outreach',
      'outreach_followup_d3',
      'outreach_followup_d7',
      're_engage_30d',
      're_engage_90d',
      'proposal_day2',
      'proposal_day5',
      'proposal_day7',
      'review_request',
      'referral_ask',
      'safety_net_checkin',
      'feedback_30day',
      'custom',
    ])
    for (const t of FOLLOW_UP_TYPES) expect(t.label.length).toBeGreaterThan(0)
  })

  it('createFollowUp is born scheduled and reads back; another org reads null', async () => {
    const created = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: FUTURE,
    })
    expect(created).toMatchObject({
      org_id: ORG_A,
      entity_id: ENT_A,
      type: 'custom',
      status: 'scheduled',
      scheduled_for: FUTURE,
      completed_at: null,
      engagement_id: null,
      quote_id: null,
    })
    expect(await getFollowUp(db, ORG_A, created.id)).toEqual(created)
    expect(await getFollowUp(db, ORG_B, created.id)).toBeNull()
  })

  it('listFollowUps orders by scheduled_for and filters by status, type, upcoming, and overdue', async () => {
    const overdue = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'proposal_day2',
      scheduled_for: PAST,
    })
    const upcoming = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'proposal_day5',
      scheduled_for: FUTURE,
    })
    const done = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'proposal_day7',
      scheduled_for: '2026-01-02T00:00:00.000Z',
    })
    await completeFollowUp(db, ORG_A, done.id)
    await createFollowUp(db, ORG_B, { entity_id: ENT_B, type: 'custom', scheduled_for: PAST })

    const ids = (rows: { id: string }[]) => rows.map((r) => r.id)
    expect(ids(await listFollowUps(db, ORG_A))).toEqual([overdue.id, done.id, upcoming.id])
    expect(ids(await listFollowUps(db, ORG_A, { status: 'completed' }))).toEqual([done.id])
    expect(ids(await listFollowUps(db, ORG_A, { type: 'proposal_day5' }))).toEqual([upcoming.id])
    expect(ids(await listFollowUps(db, ORG_A, { upcoming: true }))).toEqual([upcoming.id])
    expect(ids(await listFollowUps(db, ORG_A, { overdue: true }))).toEqual([overdue.id])
  })

  it('completeFollowUp stamps completed_at; skipFollowUp only flips the status', async () => {
    const a = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    const b = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    const before = Date.now()
    const completed = await completeFollowUp(db, ORG_A, a.id)
    expect(completed?.status).toBe('completed')
    expect(Date.parse(completed!.completed_at!)).toBeGreaterThanOrEqual(before - 1000)
    const skipped = await skipFollowUp(db, ORG_A, b.id)
    expect(skipped).toMatchObject({ status: 'skipped', completed_at: null })
  })

  it('org isolation: completing or skipping from the wrong org returns null and changes nothing', async () => {
    const a = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect(await completeFollowUp(db, ORG_B, a.id)).toBeNull()
    expect(await skipFollowUp(db, ORG_B, a.id)).toBeNull()
    expect((await getFollowUp(db, ORG_A, a.id))?.status).toBe('scheduled')
  })

  it('bulkCreateFollowUps creates every row given and returns them in order', async () => {
    const rows = await bulkCreateFollowUps(db, ORG_A, [
      { entity_id: ENT_A, type: 'proposal_day2', scheduled_for: PAST },
      { entity_id: ENT_A, type: 'proposal_day5', scheduled_for: FUTURE },
    ])
    expect(rows.map((r) => r.type)).toEqual(['proposal_day2', 'proposal_day5'])
    expect(await listFollowUps(db, ORG_A)).toHaveLength(2)
  })

  it('listFollowUpEntityNames maps only the org entities asked for; empty input is an empty map', async () => {
    expect(await listFollowUpEntityNames(db, ORG_A, [])).toEqual({})
    expect(await listFollowUpEntityNames(db, ORG_A, [ENT_A, ENT_B, ENT_A])).toEqual({
      [ENT_A]: 'Alpha Plumbing',
    })
  })
})

describe('follow-ups scheduler against real D1', () => {
  let db: D1Database
  const ENG = 'eng-a'
  const QUOTE = 'quote-a'

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedEntity(db, { id: ENT_A, orgId: ORG_A, stage: 'engaged' })
    await seedQuote(db, { id: QUOTE, orgId: ORG_A, entityId: ENT_A, status: 'sent' })
    await seedEngagement(db, { id: ENG, orgId: ORG_A, entityId: ENT_A })
  })

  it('prospect cadence: initial outreach now, then day 3 and day 7', async () => {
    const promotedAt = '2026-09-01T15:00:00.000Z'
    await scheduleProspectCadence(db, ORG_A, ENT_A, promotedAt)
    const rows = await listFollowUps(db, ORG_A)
    expect(rows.map((r) => [r.type, r.scheduled_for, r.entity_id])).toEqual([
      ['initial_outreach', promotedAt, ENT_A],
      ['outreach_followup_d3', daysFrom(promotedAt, 3), ENT_A],
      ['outreach_followup_d7', daysFrom(promotedAt, 7), ENT_A],
    ])
  })

  it('proposal cadence (Decision #19): day 2, day 5, day 7 after the send, linked to the quote', async () => {
    const sentAt = '2026-09-01T15:00:00.000Z'
    await scheduleProposalCadence(db, ORG_A, QUOTE, ENT_A, sentAt)
    const rows = await listFollowUps(db, ORG_A)
    expect(rows.map((r) => [r.type, r.scheduled_for, r.quote_id])).toEqual([
      ['proposal_day2', daysFrom(sentAt, 2), QUOTE],
      ['proposal_day5', daysFrom(sentAt, 5), QUOTE],
      ['proposal_day7', daysFrom(sentAt, 7), QUOTE],
    ])
  })

  it('engagement cadence (Decisions #23, #26, #29): at handoff, +2, +7, +30, linked to the engagement', async () => {
    const handoff = '2026-09-01T15:00:00.000Z'
    await scheduleEngagementCadence(db, ORG_A, ENG, ENT_A, handoff)
    const rows = await listFollowUps(db, ORG_A)
    expect(rows.map((r) => [r.type, r.scheduled_for, r.engagement_id])).toEqual([
      ['referral_ask', handoff, ENG],
      ['review_request', daysFrom(handoff, 2), ENG],
      ['safety_net_checkin', daysFrom(handoff, 7), ENG],
      ['feedback_30day', daysFrom(handoff, 30), ENG],
    ])
  })
})

describe('follow-up email templates', () => {
  const data = {
    clientName: 'Dana',
    businessName: 'Alpha Plumbing',
    portalUrl: 'https://portal.smd.services/portal',
  }
  const templates = {
    proposal_day2: proposalDay2Email,
    proposal_day5: proposalDay5Email,
    proposal_day7: proposalDay7Email,
    review_request: reviewRequestEmail,
    referral_ask: referralAskEmail,
    safety_net_checkin: safetyNetCheckinEmail,
    feedback_30day: feedback30DayEmail,
  }
  const visibleText = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

  it('every template renders a subject and a self-contained email addressed to the client by business', () => {
    for (const [type, render] of Object.entries(templates)) {
      const { subject, html } = render(data)
      expect(subject.length, type).toBeGreaterThan(0)
      expect(html, type).toContain('<!DOCTYPE html>')
      expect(visibleText(html), type).toContain('Hi Dana')
      expect(visibleText(html), type).toContain('Alpha Plumbing')
    }
  })

  it('the emails that ask the client to act in the portal carry the portal link; the two that ask for a reply do not', () => {
    const withLink = [
      'proposal_day2',
      'proposal_day5',
      'proposal_day7',
      'review_request',
      'safety_net_checkin',
    ]
    for (const [type, render] of Object.entries(templates)) {
      const linked = render(data).html.includes(`href="${data.portalUrl}"`)
      expect(linked, type).toBe(withLink.includes(type))
    }
  })

  it('speaks as "we", never as an individual (Decision #20)', () => {
    for (const [type, render] of Object.entries(templates)) {
      const text = visibleText(render(data).html)
      expect(text, type).toMatch(/\b[Ww]e\b/)
      expect(text, type).not.toMatch(/\bI\b/)
    }
  })

  it('getFollowUpTemplate maps the seven emailed moments and nothing else', () => {
    for (const [type, render] of Object.entries(templates)) {
      expect(getFollowUpTemplate(type)).toBe(render)
    }
    expect(getFollowUpTemplate('initial_outreach')).toBeNull()
    expect(getFollowUpTemplate('custom')).toBeNull()
    expect(getFollowUpTemplate('')).toBeNull()
  })
})

describe('POST /api/admin/follow-ups/[id]', () => {
  let db: D1Database

  const call = (
    id: string,
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG_A)
  ) =>
    POST(
      routeContext({
        request: formRequest(`http://test.local/api/admin/follow-ups/${id}`, fields),
        params: { id },
        session,
      }) as unknown as Parameters<typeof POST>[0]
    )

  beforeEach(async () => {
    sendEmail.mockReset()
    sendEmail.mockResolvedValue({ success: true, id: 'email-1' })
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: ENT_A, orgId: ORG_A, name: 'Alpha Plumbing' })
    await seedEntity(db, { id: ENT_B, orgId: ORG_B, name: 'Beta Legal' })
    bindEnv({ DB: db, RESEND_API_KEY: 're_test', APP_BASE_URL: 'https://smd.services' })
  })

  it('answers 401 with no admin session', async () => {
    const f = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect((await call(f.id, { action: 'complete' }, null)).status).toBe(401)
    expect((await getFollowUp(db, ORG_A, f.id))?.status).toBe('scheduled')
  })

  it('a follow-up outside the org is not_found; an unknown action is invalid_action', async () => {
    const foreign = await createFollowUp(db, ORG_B, {
      entity_id: ENT_B,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect(locationQuery(await call(foreign.id, { action: 'complete' })).get('error')).toBe(
      'not_found'
    )
    const mine = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect(locationQuery(await call(mine.id, { action: 'archive' })).get('error')).toBe(
      'invalid_action'
    )
    expect((await getFollowUp(db, ORG_A, mine.id))?.status).toBe('scheduled')
  })

  it('complete and skip move the row and return to the dashboard with saved=1', async () => {
    const a = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    const b = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect(locationOf(await call(a.id, { action: 'complete' }))).toBe('/admin/follow-ups?saved=1')
    expect(locationOf(await call(b.id, { action: 'skip' }))).toBe('/admin/follow-ups?saved=1')
    expect((await getFollowUp(db, ORG_A, a.id))?.status).toBe('completed')
    expect((await getFollowUp(db, ORG_A, b.id))?.status).toBe('skipped')
  })

  it('send_email needs a contact with an address and a template for the type', async () => {
    const noContact = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'proposal_day2',
      scheduled_for: PAST,
    })
    expect(locationQuery(await call(noContact.id, { action: 'send_email' })).get('error')).toBe(
      'no_contact_email'
    )

    await createContact(db, ORG_A, ENT_A, { name: 'Dana', email: 'dana@example.com' })
    const noTemplate = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'custom',
      scheduled_for: PAST,
    })
    expect(locationQuery(await call(noTemplate.id, { action: 'send_email' })).get('error')).toBe(
      'no_template'
    )
    expect(sendEmail).not.toHaveBeenCalled()
  })

  it('send_email renders the type template for the contact, sends it, and completes the follow-up', async () => {
    await createContact(db, ORG_A, ENT_A, { name: 'Dana', email: 'dana@example.com' })
    const f = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'safety_net_checkin',
      scheduled_for: PAST,
    })
    const res = await call(f.id, { action: 'send_email' })
    expect(locationOf(res)).toBe('/admin/follow-ups?saved=1')

    expect(sendEmail).toHaveBeenCalledTimes(1)
    const [apiKey, payload] = sendEmail.mock.calls[0] as [
      string,
      { to: string; subject: string; html: string },
    ]
    expect(apiKey).toBe('re_test')
    const expected = safetyNetCheckinEmail({
      clientName: 'Dana',
      businessName: 'Alpha Plumbing',
      portalUrl: 'https://smd.services',
    })
    expect(payload.to).toBe('dana@example.com')
    expect(payload.subject).toBe(expected.subject)
    expect(payload.html).toContain('Alpha Plumbing')
    expect((await getFollowUp(db, ORG_A, f.id))?.status).toBe('completed')
  })

  it('a transport failure leaves the follow-up scheduled and reports email_failed', async () => {
    sendEmail.mockResolvedValue({ success: false, error: 'resend down' })
    await createContact(db, ORG_A, ENT_A, { name: 'Dana', email: 'dana@example.com' })
    const f = await createFollowUp(db, ORG_A, {
      entity_id: ENT_A,
      type: 'proposal_day2',
      scheduled_for: PAST,
    })
    expect(locationQuery(await call(f.id, { action: 'send_email' })).get('error')).toBe(
      'email_failed'
    )
    expect((await getFollowUp(db, ORG_A, f.id))?.status).toBe('scheduled')
  })
})

// Astro pages have no handler to invoke. Two things about them are policy
// rather than composition and stay as drift guards: follow-ups remain one
// click away from the Settings hub (ADR 0046 demoted them from the top nav),
// and the admin surface is never indexed.
describe('follow-ups: reachability and privacy (drift guards)', () => {
  it('the Settings hub links to /admin/follow-ups', () => {
    const settings = readFileSync(resolve('src/pages/admin/settings/index.astro'), 'utf-8')
    expect(settings).toContain('/admin/follow-ups')
  })

  it('the admin layout is noindex', () => {
    expect(readFileSync(resolve('src/layouts/AdminLayout.astro'), 'utf-8')).toContain('noindex')
  })
})
