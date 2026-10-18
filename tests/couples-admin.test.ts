/**
 * Admin actions on a couples instance (ADR 0090 decision 4): create, invite
 * each partner through a Clerk Organization (the Operator invitation pattern,
 * driven by SMD), record the Telegram bot and group, and read the onboarding
 * state. Clerk is mocked: no invitation leaves the test.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

const clerk = vi.hoisted(() => ({
  createOrganization: vi.fn(),
  createOrganizationInvitation: vi.fn(),
}))
vi.mock('@clerk/astro/server', () => ({
  clerkClient: () => ({ organizations: clerk }),
}))
const mail = vi.hoisted(() => ({ sendEmail: vi.fn() }))
vi.mock('../src/lib/email/resend', () => ({ sendEmail: mail.sendEmail }))

import { POST as createPost } from '../src/pages/api/admin/hosted-agent/couples/index'
import { POST as invitePost } from '../src/pages/api/admin/hosted-agent/couples/[id]/invite'
import { POST as telegramPost } from '../src/pages/api/admin/hosted-agent/couples/[id]/telegram'
import { POST as activatePost } from '../src/pages/api/admin/hosted-agent/[id]/activate'
import { listCouplesInstances, listPeople } from '../src/lib/db/couples-onboarding'
import { getCouplesOnboardingStatus } from '../src/lib/hosted-agent/couples/status'

const ORG_ID = '01JQFK0000SMDSERVICES000'
const ADMIN = {
  userId: 'admin-1',
  orgId: ORG_ID,
  role: 'admin',
  email: 'scott@smd.services',
  expiresAt: '2099-01-01',
}

let db: D1Database

function form(fields: Record<string, string>): FormData {
  const f = new FormData()
  for (const [k, v] of Object.entries(fields)) f.append(k, v)
  return f
}

function ctx(body: FormData, params: Record<string, string> = {}, session: unknown = ADMIN) {
  const request = new Request('http://test.local/x', { method: 'POST', body })
  return { request, params, locals: { session } } as unknown as Parameters<typeof createPost>[0]
}

const VALID = {
  couple_name: 'Alex and Sam',
  agent_name: 'Juniper',
  p1_name: 'Alex',
  p1_email: 'alex@example.com',
  p2_name: 'Sam',
  p2_email: 'sam@example.com',
}

async function created(): Promise<string> {
  const res = await createPost(ctx(form(VALID)))
  const loc = res.headers.get('Location') ?? ''
  const m = /couples\/([0-9a-f-]{36})\?st=created/.exec(loc)
  if (!m) throw new Error(`unexpected redirect ${loc}`)
  return m[1]
}

beforeEach(async () => {
  db = createTestD1()
  await runMigrations(db, {
    files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
  })
  Object.assign(testEnv, { DB: db, PORTAL_BASE_URL: 'https://portal.smd.services' })
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id, created_at)
       VALUES ('admin-1', ?, 'admin-test@smd.services', 'Admin', 'admin', 'clerk_admin', datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  clerk.createOrganization.mockReset().mockResolvedValue({ id: 'org_new' })
  clerk.createOrganizationInvitation.mockReset().mockResolvedValue({ id: 'orginv_1' })
  mail.sendEmail.mockReset().mockResolvedValue({ success: true })
})

describe('create', () => {
  it('creates an instance and sends nothing', async () => {
    await created()
    expect(await listCouplesInstances(db)).toHaveLength(1)
    expect(clerk.createOrganization).not.toHaveBeenCalled()
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
  })

  it('FALSIFIER: refuses a non-admin', async () => {
    const res = await createPost(ctx(form(VALID), {}, null))
    expect(res.status).toBe(401)
    expect(await listCouplesInstances(db)).toHaveLength(0)
  })

  it('FALSIFIER: refuses two partners with the same email', async () => {
    const res = await createPost(ctx(form({ ...VALID, p2_email: 'ALEX@example.com' })))
    expect(res.headers.get('Location')).toContain('st=invalid')
    expect(await listCouplesInstances(db)).toHaveLength(0)
  })
})

describe('invite', () => {
  it('creates the Clerk org once, binds it, and invites one partner as org:member', async () => {
    const id = await created()
    const [p1, p2] = await listPeople(db, id)
    await invitePost(ctx(form({ person_id: p1.id }), { id }))
    await invitePost(ctx(form({ person_id: p2.id }), { id }))
    expect(clerk.createOrganization).toHaveBeenCalledTimes(1)
    expect(clerk.createOrganization).toHaveBeenCalledWith({
      name: 'Alex and Sam',
      createdBy: 'clerk_admin',
    })
    expect(clerk.createOrganizationInvitation).toHaveBeenCalledTimes(2)
    expect(clerk.createOrganizationInvitation.mock.calls[0][0]).toEqual({
      organizationId: 'org_new',
      emailAddress: 'alex@example.com',
      role: 'org:member',
      inviterUserId: 'clerk_admin',
      redirectUrl: 'https://portal.smd.services/portal/products/hosted-agent/couples',
    })
    const people = await listPeople(db, id)
    expect(people.every((p) => p.invited_at !== null && p.clerk_invitation_id === 'orginv_1')).toBe(
      true
    )
  })

  it('FALSIFIER: a person id from another instance is refused', async () => {
    const id = await created()
    const otherRes = await createPost(
      ctx(form({ ...VALID, p1_email: 'x@example.com', p2_email: 'y@example.com' }))
    )
    const other = /couples\/([0-9a-f-]{36})/.exec(otherRes.headers.get('Location') ?? '')![1]
    const [foreign] = await listPeople(db, other)
    const res = await invitePost(ctx(form({ person_id: foreign.id }), { id }))
    expect(res.headers.get('Location')).toContain('st=invalid')
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
  })

  it('maps a duplicate invitation to a banner', async () => {
    const id = await created()
    const [p1] = await listPeople(db, id)
    clerk.createOrganizationInvitation.mockRejectedValueOnce(new Error('already invited'))
    const res = await invitePost(ctx(form({ person_id: p1.id }), { id }))
    expect(res.headers.get('Location')).toContain('st=already_invited')
  })
})

describe('telegram facts and onboarding state', () => {
  it('records the bot and group, and the state names what blocks the generator', async () => {
    const id = await created()
    let status = await getCouplesOnboardingStatus(db, id)
    expect(status?.blockers).toEqual(
      expect.arrayContaining([
        'No customer slug assigned.',
        'Alex (p1) has not consented.',
        'Sam (p2) has not consented.',
        'No joint settings saved.',
      ])
    )
    await telegramPost(ctx(form({ bot_username: '@juniper_couple_bot' }), { id }))
    await telegramPost(ctx(form({ shared_telegram_chat: '-1001234567890' }), { id }))
    status = await getCouplesOnboardingStatus(db, id)
    expect(status?.instance.telegram_bot_username).toBe('juniper_couple_bot')
    expect(status?.instance.shared_telegram_chat).toBe('-1001234567890')
  })

  it('FALSIFIER: a positive (user) id is not accepted as the group chat', async () => {
    const id = await created()
    const res = await telegramPost(ctx(form({ shared_telegram_chat: '12345' }), { id }))
    expect(res.headers.get('Location')).toContain('st=invalid')
  })
})

async function withSlug(id: string): Promise<void> {
  await db
    .prepare(`UPDATE hosted_agent_intake SET customer_slug = 'couple-test' WHERE id = ?`)
    .bind(id)
    .run()
}

describe('activation (go live)', () => {
  it('FALSIFIER: on a couples instance the go-live email goes to BOTH partners, from their rows', async () => {
    const id = await created()
    await withSlug(id)
    const res = await activatePost(
      ctx(
        form({
          channel_details: 'Email juniper@example.agentmail.to.',
          return_to: `/admin/hosted-agent/couples/${id}`,
        }),
        { id }
      )
    )
    expect(res.headers.get('Location')).toBe(`/admin/hosted-agent/couples/${id}?st=activated`)
    const to = mail.sendEmail.mock.calls.map((c) => (c[1] as { to: string }).to).sort()
    expect(to).toEqual(['alex@example.com', 'sam@example.com'])
    const html = (mail.sendEmail.mock.calls[0][1] as { html: string }).html
    expect(html).toContain('Email juniper@example.agentmail.to.')
    expect(html).toContain('https://portal.smd.services/portal/products/hosted-agent/couples')
  })

  it("one partner's failed email does not stop the other partner's", async () => {
    const id = await created()
    await withSlug(id)
    mail.sendEmail.mockRejectedValueOnce(new Error('bounce'))
    await activatePost(ctx(form({ channel_details: 'Email us.' }), { id }))
    expect(mail.sendEmail).toHaveBeenCalledTimes(2)
  })
})
