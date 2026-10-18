/**
 * POST /api/internal/agent/couples/:id/invite: an SMD agent sends a couples
 * partner's portal invitation through the website, authenticated by
 * SMD_AGENT_ADMIN_KEY, inviting as the SMD admin user. Shares the flow with
 * the admin-console button (src/lib/hosted-agent/couples/invite.ts). Clerk is
 * mocked: no invitation leaves the test.
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

import { POST as createPost } from '../src/pages/api/admin/hosted-agent/couples/index'
import { POST as adminInvitePost } from '../src/pages/api/admin/hosted-agent/couples/[id]/invite'
import { POST as agentInvitePost } from '../src/pages/api/internal/agent/couples/[id]/invite'
import { listPeople } from '../src/lib/db/couples-onboarding'
import { SMD_ADMIN_USER_ID } from '../src/lib/auth/agent-admin-key'

const ORG_ID = '01JQFK0000SMDSERVICES000'
const KEY = 'test-agent-key-not-a-real-secret'
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
      p1_name: 'Alex',
      p1_email: p1Email,
      p2_name: 'Sam',
      p2_email: p2Email,
    })
  )
  const m = /couples\/([0-9a-f-]{36})\?st=created/.exec(res.headers.get('Location') ?? '')
  if (!m) throw new Error('create failed')
  return m[1]
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
  clerk.createOrganization.mockReset().mockResolvedValue({ id: 'org_new' })
  clerk.createOrganizationInvitation.mockReset().mockResolvedValue({ id: 'orginv_1' })
})

describe('auth (fail closed)', () => {
  it('FALSIFIER: 401 when SMD_AGENT_ADMIN_KEY is unset, even with a bearer', async () => {
    const id = await created()
    Object.assign(testEnv, { SMD_AGENT_ADMIN_KEY: undefined })
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(401)
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
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
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
  })
})

describe('body and target validation', () => {
  it('400 on malformed JSON, a non-object, an unknown key, or both refs', async () => {
    const id = await created()
    expect((await agentInvitePost(agentCtx(id, '{not json', undefined, true))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, ['p1']))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, { person_key: 'p3' }))).status).toBe(400)
    expect((await agentInvitePost(agentCtx(id, {}))).status).toBe(400)
    const [p1] = await listPeople(db, id)
    expect(
      (await agentInvitePost(agentCtx(id, { person_key: 'p1', person_id: p1.id }))).status
    ).toBe(400)
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
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
    expect(clerk.createOrganizationInvitation).not.toHaveBeenCalled()
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

describe('happy path', () => {
  it('invites p1 by key as the SMD admin user and returns the invitation id', async () => {
    const id = await created()
    const res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ result: 'invited', invitation_id: 'orginv_1' })
    expect(clerk.createOrganization).toHaveBeenCalledWith({
      name: 'Alex and Sam',
      createdBy: 'clerk_smd_admin',
    })
    expect(clerk.createOrganizationInvitation).toHaveBeenCalledWith({
      organizationId: 'org_new',
      emailAddress: 'alex@example.com',
      role: 'org:member',
      inviterUserId: 'clerk_smd_admin',
      redirectUrl: 'https://portal.smd.services/portal/products/hosted-agent/couples',
    })
    const [p1, p2] = await listPeople(db, id)
    expect(p1.invited_at).not.toBeNull()
    expect(p1.clerk_invitation_id).toBe('orginv_1')
    expect(p2.invited_at).toBeNull()
  })

  it('invites by person_id and reuses the bound org', async () => {
    const id = await created()
    await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    const [, p2] = await listPeople(db, id)
    const res = await agentInvitePost(agentCtx(id, { person_id: p2.id }))
    expect(res.status).toBe(200)
    expect(clerk.createOrganization).toHaveBeenCalledTimes(1)
    expect(clerk.createOrganizationInvitation).toHaveBeenCalledTimes(2)
  })

  it('maps Clerk outcomes and never logs the invitee address', async () => {
    const id = await created()
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    clerk.createOrganizationInvitation.mockRejectedValueOnce(new Error('already invited'))
    let res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ result: 'already_invited' })

    clerk.createOrganizationInvitation.mockRejectedValueOnce(new Error('boom'))
    res = await agentInvitePost(agentCtx(id, { person_key: 'p1' }))
    expect(res.status).toBe(502)
    expect(await res.json()).toEqual({ result: 'invite_failed' })
    expect(JSON.stringify(spy.mock.calls)).not.toContain('alex@example.com')
    spy.mockRestore()
  })
})

describe('admin route shares the flow', () => {
  it('the admin button still invites as the signed-in admin and redirects with the banner', async () => {
    const id = await created()
    const [p1] = await listPeople(db, id)
    const res = await adminInvitePost(adminCtx({ person_id: p1.id }, { id }))
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe(`/admin/hosted-agent/couples/${id}?st=invited`)
    expect(clerk.createOrganizationInvitation.mock.calls[0][0].inviterUserId).toBe('clerk_admin')
  })
})
