/**
 * POST /api/internal/agent/couples/:id/invite: an SMD agent sends a couples
 * partner's portal invitation through the website, authenticated by
 * SMD_AGENT_ADMIN_KEY, inviting as the SMD admin user. Shares the flow with
 * the admin-console button (src/lib/hosted-agent/couples/invite.ts).
 *
 * SMD sends its OWN welcome invitation email; Clerk's is suppressed by
 * creating an application invitation with `notify: false`. Clerk and the
 * mailer are both mocked: no invitation and no email leaves the test.
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
  revokeOrganizationInvitation: vi.fn(),
  createInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
  getUserList: vi.fn(),
}))
vi.mock('@clerk/astro/server', () => ({
  clerkClient: () => ({
    organizations: {
      createOrganization: clerk.createOrganization,
      createOrganizationInvitation: clerk.createOrganizationInvitation,
      revokeOrganizationInvitation: clerk.revokeOrganizationInvitation,
    },
    invitations: {
      createInvitation: clerk.createInvitation,
      revokeInvitation: clerk.revokeInvitation,
    },
    users: { getUserList: clerk.getUserList },
  }),
}))
const mail = vi.hoisted(() => ({ sendEmail: vi.fn() }))
vi.mock('../src/lib/email/resend', () => ({ sendEmail: mail.sendEmail }))

import { POST as createPost } from '../src/pages/api/admin/hosted-agent/couples/index'
import { POST as adminInvitePost } from '../src/pages/api/admin/hosted-agent/couples/[id]/invite'
import { POST as agentInvitePost } from '../src/pages/api/internal/agent/couples/[id]/invite'
import { getCouplesInstance, listPeople } from '../src/lib/db/couples-onboarding'
import { SMD_ADMIN_USER_ID } from '../src/lib/auth/agent-admin-key'
import { resolveCouplesAccess } from '../src/lib/portal/couples-access'
import { couplesWelcomeEmail } from '../src/lib/email/couples-templates'

const ORG_ID = '01JQFK0000SMDSERVICES000'
const KEY = 'test-agent-key-not-a-real-secret'
const ACCEPT = 'https://clerk.example/v1/tickets/accept?ticket=t1'
const LANDING = 'https://portal.smd.services/portal/products/hosted-agent/couples'
const ADMIN = {
  userId: 'admin-1',
  orgId: ORG_ID,
  role: 'admin',
  email: 'scott@smd.services',
  expiresAt: '2099-01-01',
}

let db: D1Database

type Ctx = Parameters<typeof createPost>[0]

function adminCtx(fields: Record<string, string>, params: Record<string, string> = {}): Ctx {
  const f = new FormData()
  for (const [k, v] of Object.entries(fields)) f.append(k, v)
  const request = new Request('http://test.local/x', { method: 'POST', body: f })
  return { request, params, locals: { session: ADMIN } } as unknown as Ctx
}

function agentCtx(
  id: string,
  body: unknown,
  auth: string | null = `Bearer ${KEY}`,
  raw = false
): Ctx {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (auth !== null) headers['Authorization'] = auth
  const request = new Request(`http://test.local/api/internal/agent/couples/${id}/invite`, {
    method: 'POST',
    headers,
    body: raw ? String(body) : JSON.stringify(body),
  })
  return { request, params: { id }, locals: { session: null } } as unknown as Ctx
}

async function created(p1Email = 'alex@example.com', p2Email = 'sam@example.com'): Promise<string> {
  const res = await createPost(
    adminCtx({
      couple_name: 'Alex and Sam',
      agent_name: 'Juniper',
      p1_name: 'Alex Rivera',
      p1_email: p1Email,
      p2_name: 'Sam',
      p2_email: p2Email,
    })
  )
  const m = /couples\/([0-9a-f-]{36})\?st=created/.exec(res.headers.get('Location') ?? '')
  if (!m) throw new Error('create failed')
  return m[1]
}

/** Astro locals for a fresh Clerk session (no Organization) with a verified email. */
function signedIn(clerkUserId: string, email: string): App.Locals {
  return {
    auth: () => ({ userId: clerkUserId, orgId: null, sessionId: `sess_${clerkUserId}` }),
    currentUser: async () => ({
      primaryEmailAddress: { emailAddress: email, verification: { status: 'verified' } },
      firstName: 'Alex',
      lastName: 'Rivera',
    }),
    session: null,
  } as unknown as App.Locals
}

beforeEach(async () => {
  db = createTestD1()
  await runMigrations(db, {
    files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
  })
  Object.assign(testEnv, {
    DB: db,
    PORTAL_BASE_URL: 'https://portal.smd.services',
    SMD_AGENT_ADMIN_KEY: KEY,
    RESEND_API_KEY: 'test-resend-key-not-real',
  })
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id, created_at)
       VALUES ('admin-1', ?, 'admin-test@smd.services', 'Admin', 'admin', 'clerk_admin', datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  // The seeded SMD admin user (migration 0005) is the agent route's inviter.
  await db
    .prepare(`UPDATE users SET clerk_user_id = 'clerk_smd_admin' WHERE id = ?`)
    .bind(SMD_ADMIN_USER_ID)
    .run()
  clerk.createOrganization.mockReset()
  clerk.createOrganizationInvitation.mockReset()
  clerk.revokeOrganizationInvitation.mockReset().mockResolvedValue({})
  clerk.createInvitation.mockReset().mockResolvedValue({ id: 'inv_1', url: ACCEPT })
  clerk.revokeInvitation.mockReset().mockResolvedValue({})
  clerk.getUserList.mockReset().mockResolvedValue({ data: [], totalCount: 0 })
  mail.sendEmail.mockReset().mockResolvedValue({ success: true, id: 'email_1' })
})

describe('auth (fail closed)', () => {
  it('FALSIFIER: 401 when SMD_AGENT_ADMIN_KEY is unset, even with a bearer', async () => {
    const id = await created()
    Object.assign(testEnv, { SMD_AGENT_ADMIN_KEY: undefined })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(401)
    expect(clerk.createInvitation).not.toHaveBeenCalled()
    expect(mail.sendEmail).not.toHaveBeenCalled()
  })

  it('FALSIFIER: 401 when SMD_AGENT_ADMIN_KEY is empty and the bearer is empty', async () => {
    const id = await created()
    Object.assign(testEnv, { SMD_AGENT_ADMIN_KEY: '' })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }, 'Bearer '))
    expect(res.status).toBe(401)
  })

  it('FALSIFIER: 401 on a wrong key, a missing header, or a non-Bearer scheme', async () => {
    const id = await created()
    for (const auth of [`Bearer ${KEY}x`, null, KEY, `Basic ${KEY}`]) {
      const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }, auth))
      expect(res.status).toBe(401)
    }
    expect(clerk.createInvitation).not.toHaveBeenCalled()
  })
})

describe('body and target validation', () => {
  it('400 on malformed JSON, a non-object, an unknown key, both refs, or a non-boolean resend', async () => {
    const id = await created()
    expect((await agentInvitePost(agentCtx(id, '{not json', undefined, true))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, ['p1']))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, { person_key: 'p3' }))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, {}))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, { person_key: 'p1', resend: 'yes' }))).status).toBe(
      400
    )
    const [p1] = await listPeople(db, id)
    expect(
      (await agentInvitePost(agentCtx(id, { person_key: 'p1', person_id: p1.id }))).status
    ).toBe(400)
    expect(clerk.createInvitation).not.toHaveBeenCalled()
  })

  it('404 invalid on an unknown instance', async () => {
    const res = await agentInvitePost(
      agentCtx('00000000-0000-0000-0000-000000000000', { person_key: 'p1' })
    )
    expect(res.status).toBe(404)
    expect(await res.json()).toEqual({ result: 'invalid' })
  })

  it('FALSIFIER: a person_id from another instance is refused', async () => {
    const id = await created()
    const other = await created('x@example.com', 'y@example.com')
    const [foreign] = await listPeople(db, other)
    const res = await agentInvitePost(agentCtx(id, { person_id: foreign.id }))
    expect(res.status).toBe(404)
    expect(await res.json()).toEqual({ result: 'invalid' })
    expect(clerk.createInvitation).not.toHaveBeenCalled()
    expect(mail.sendEmail).not.toHaveBeenCalled()
  })

  it('409 when the SMD admin user has no Clerk user id', async () => {
    const id = await created()
    await db
      .prepare(`UPDATE users SET clerk_user_id = NULL WHERE id = ?`)
      .bind(SMD_ADMIN_USER_ID)
      .run()
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(409)
    expect(await res.json()).toEqual({ result: 'no_clerk_user' })
  })
})

describe('SMD sends its own invitation; Clerk sends nothing', () => {
  it('creates a Clerk application invitation with notify:false and emails its url', async () => {
    const id = await created()
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ result: 'invited', invitation_id: 'inv_1' })
    expect(clerk.createInvitation).toHaveBeenCalledWith({
      emailAddress: 'alex@example.com',
      redirectUrl: LANDING,
      notify: false,
    })
    // The org invitation (Clerk's own email, no way to suppress it) is never used.
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
    expect(clerk.createOrganization).not.toHaveBeenCalled()

    expect(mail.sendEmail).toHaveBeenCalledTimes(1)
    const [apiKey, payload] = mail.sendEmail.mock.calls[0]
    expect(apiKey).toBe('test-resend-key-not-real')
    expect(payload.to).toBe('alex@example.com')
    expect(payload.reply_to).toBe('team@smd.services')
    expect(payload.subject).toBe('Juniper: your invitation to set up your couples assistant')
    expect(payload.html).toContain(ACCEPT.replace(/&/g, '&amp;'))
    expect(payload.text).toContain(ACCEPT)
    // Partner names are the authored first names; the other partner is named.
    expect(payload.text).toMatch(/^Hi Alex,/)
    expect(payload.text).toContain('You and Sam each confirm the shared settings')
    expect(payload.text).toContain('create your account with this email address')

    const [p1, p2] = await listPeople(db, id)
    expect(p1.invited_at).not.toBeNull()
    expect(p1.clerk_invitation_id).toBe('inv_1')
    expect(p2.invited_at).toBeNull()
  })

  it('a second send without resend sends nothing and reports already_invited', async () => {
    const id = await created()
    await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(await res.json()).toEqual({ result: 'already_invited' })
    expect(clerk.createInvitation).toHaveBeenCalledTimes(1)
    expect(mail.sendEmail).toHaveBeenCalledTimes(1)
  })

  it('resend revokes a pending ORG invitation from the old flow, then sends the new email', async () => {
    const id = await created()
    const inst = await getCouplesInstance(db, id)
    await db
      .prepare('UPDATE entities SET clerk_org_id = ? WHERE id = ?')
      .bind('org_couple', inst!.entity_id)
      .run()
    const [p1] = await listPeople(db, id)
    await db
      .prepare(
        `UPDATE hosted_agent_people SET invited_at = datetime('now'), clerk_invitation_id = 'orginv_old' WHERE id = ?`
      )
      .bind(p1.id)
      .run()
    clerk.createInvitation.mockResolvedValueOnce({ id: 'inv_2', url: ACCEPT })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1', resend: true }))
    expect(await res.json()).toEqual({ result: 'resent', invitation_id: 'inv_2' })
    expect(clerk.revokeOrganizationInvitation).toHaveBeenCalledWith({
      organizationId: 'org_couple',
      invitationId: 'orginv_old',
      requestingUserId: 'clerk_smd_admin',
    })
    expect(clerk.createInvitation).toHaveBeenCalledWith({
      emailAddress: 'alex@example.com',
      redirectUrl: LANDING,
      notify: false,
      ignoreExisting: true,
    })
    expect(mail.sendEmail).toHaveBeenCalledTimes(1)
    expect((await listPeople(db, id))[0].clerk_invitation_id).toBe('inv_2')
  })

  it('resend revokes a previous APPLICATION invitation', async () => {
    const id = await created()
    await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    await agentInvitePost(agentCtx(id, { person_key: 'p1', resend: true }))
    expect(clerk.revokeInvitation).toHaveBeenCalledWith('inv_1')
    expect(mail.sendEmail).toHaveBeenCalledTimes(2)
  })

  it('a partner who already has a Clerk account gets no invitation and a sign-in link', async () => {
    const id = await created()
    clerk.getUserList.mockResolvedValueOnce({
      data: [{ emailAddresses: [{ emailAddress: 'Alex@Example.com' }] }],
      totalCount: 1,
    })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(await res.json()).toEqual({ result: 'invited' })
    expect(clerk.createInvitation).not.toHaveBeenCalled()
    const payload = mail.sendEmail.mock.calls[0][1]
    expect(payload.text).toContain(LANDING)
    expect(payload.text).toContain('sign in with this email address')
    const [p1] = await listPeople(db, id)
    expect(p1.invited_at).not.toBeNull()
    expect(p1.clerk_invitation_id).toBeNull()
  })

  it('FALSIFIER: a partial Clerk email match is not an existing account', async () => {
    const id = await created()
    clerk.getUserList.mockResolvedValueOnce({
      data: [{ emailAddresses: [{ emailAddress: 'malex@example.com' }] }],
      totalCount: 1,
    })
    await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(clerk.createInvitation).toHaveBeenCalledTimes(1)
  })

  it('FALSIFIER: an address bound to another customer is refused, nothing sent', async () => {
    const id = await created()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, created_at)
         VALUES ('u-other', ?, 'alex@example.com', 'Alex', 'client', 'some-other-entity', datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(409)
    expect(await res.json()).toEqual({ result: 'account_conflict' })
    expect(clerk.createInvitation).not.toHaveBeenCalled()
    expect(mail.sendEmail).not.toHaveBeenCalled()
  })

  it('a failed email revokes the new invitation and leaves the partner un-invited', async () => {
    const id = await created()
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mail.sendEmail.mockResolvedValueOnce({ success: false, error: 'boom' })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(502)
    expect(await res.json()).toEqual({ result: 'email_failed' })
    expect(clerk.revokeInvitation).toHaveBeenCalledWith('inv_1')
    expect((await listPeople(db, id))[0].invited_at).toBeNull()
    expect(JSON.stringify(spy.mock.calls)).not.toContain('alex@example.com')
    spy.mockRestore()
  })

  it('maps Clerk failures and never logs the invitee address', async () => {
    const id = await created()
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    clerk.createInvitation.mockRejectedValueOnce(new Error('duplicate'))
    let res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(await res.json()).toEqual({ result: 'already_invited' })
    clerk.createInvitation.mockRejectedValueOnce(new Error('boom'))
    res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(502)
    expect(await res.json()).toEqual({ result: 'invite_failed' })
    expect(mail.sendEmail).not.toHaveBeenCalled()
    expect(JSON.stringify(spy.mock.calls)).not.toContain('alex@example.com')
    spy.mockRestore()
  })
})

describe('reachability: the invited partner lands on the couples surface', () => {
  it('first sign-in with no Clerk Organization resolves the entity and claims the partner', async () => {
    const id = await created()
    await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    const access = await resolveCouplesAccess(db, signedIn('clerk_alex', 'ALEX@example.com'))
    expect(access.kind).toBe('allowed')
    if (access.kind === 'allowed') expect(access.person.person_key).toBe('p1')
  })
})

describe('the couples landing never depends on the active Clerk Organization', () => {
  // An old-flow invitee (Clerk org invitation, no pre-bound users row) whose
  // ticket sign-up left no active org: the session reaches no entity.
  it('binds an entity-less user to the couple by verified email, then claims the partner', async () => {
    const id = await created()
    const access = await resolveCouplesAccess(db, signedIn('clerk_alex', 'Alex@Example.com'))
    expect(access.kind).toBe('allowed')
    if (access.kind === 'allowed') {
      expect(access.person.person_key).toBe('p1')
      expect(access.instance.id).toBe(id)
    }
    const row = await db
      .prepare(`SELECT entity_id FROM users WHERE clerk_user_id = 'clerk_alex'`)
      .first<{ entity_id: string }>()
    expect(row?.entity_id).toBe((await getCouplesInstance(db, id))!.entity_id)
  })

  it('FALSIFIER: an email SMD did not author reaches nothing', async () => {
    await created()
    const access = await resolveCouplesAccess(db, signedIn('clerk_x', 'stranger@example.com'))
    expect(access).toEqual({ kind: 'redirect', to: '/auth/sign-in?status=no_subscription' })
  })

  it('FALSIFIER: a partner row claimed by someone else is not reachable by email', async () => {
    const id = await created()
    await resolveCouplesAccess(db, signedIn('clerk_alex', 'alex@example.com'))
    // A second Clerk identity with the same address (a new account) gets nothing.
    await db
      .prepare(`UPDATE users SET email = 'old-alex@example.com' WHERE clerk_user_id = 'clerk_alex'`)
      .run()
    const access = await resolveCouplesAccess(db, signedIn('clerk_alex2', 'alex@example.com'))
    expect(access.kind).toBe('redirect')
    expect((await listPeople(db, id))[0].user_id).not.toBeNull()
  })

  it('FALSIFIER: one address authored on two couples binds nothing', async () => {
    await created('alex@example.com', 'sam@example.com')
    await created('alex@example.com', 'pat@example.com')
    const access = await resolveCouplesAccess(db, signedIn('clerk_alex', 'alex@example.com'))
    expect(access.kind).toBe('redirect')
    const row = await db
      .prepare(`SELECT entity_id FROM users WHERE clerk_user_id = 'clerk_alex'`)
      .first<{ entity_id: string | null }>()
    expect(row?.entity_id).toBeNull()
  })

  it('FALSIFIER: a user already bound to another entity is never re-pointed', async () => {
    await created()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, clerk_user_id, entity_id, created_at)
         VALUES ('u-a', ?, 'alex@example.com', 'Alex', 'client', 'clerk_alex', 'elsewhere', datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    await resolveCouplesAccess(db, signedIn('clerk_alex', 'alex@example.com'))
    const row = await db
      .prepare(`SELECT entity_id FROM users WHERE id = 'u-a'`)
      .first<{ entity_id: string }>()
    expect(row?.entity_id).toBe('elsewhere')
  })
})

describe('admin route shares the flow', () => {
  it('the admin button invites and redirects with the banner', async () => {
    const id = await created()
    const [p1] = await listPeople(db, id)
    const res = await adminInvitePost(adminCtx({ person_id: p1.id }, { id }))
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe(`/admin/hosted-agent/couples/${id}?st=invited`)
    expect(mail.sendEmail).toHaveBeenCalledTimes(1)
  })
})

describe('the welcome email copy', () => {
  const base = {
    agentName: 'Juniper',
    partnerName: 'Alex Rivera',
    otherPartnerName: 'Sam Lee',
    acceptUrl: 'https://clerk.example/a?x=1&y=2',
    existingAccount: false,
  }

  it('carries no em dash, dollar amount, or banned placeholder copy', () => {
    const e = couplesWelcomeEmail(base)
    for (const part of [e.subject, e.html, e.text]) {
      expect(part).not.toMatch(/—/)
      expect(part).not.toMatch(/\$\d/)
      expect(part.toLowerCase()).not.toContain('coming soon')
    }
  })

  it('escapes authored names in the HTML', () => {
    const e = couplesWelcomeEmail({ ...base, partnerName: '<b>Al</b>', agentName: 'J&J' })
    expect(e.html).not.toContain('<b>Al</b>')
    expect(e.html).toContain('J&amp;J is a private assistant')
    expect(e.html).toContain('x=1&amp;y=2')
  })

  it('without an authored agent name, the subject and opening stay generic', () => {
    const e = couplesWelcomeEmail({ ...base, agentName: null, otherPartnerName: null })
    expect(e.subject).toBe('Your invitation to set up your couples assistant')
    expect(e.text).toContain('You are invited to a private assistant the two of you share.')
    expect(e.text).toContain('shared with your partner.')
  })
})
