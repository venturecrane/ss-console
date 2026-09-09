/**
 * Tests for the change-request inbox — the view-model
 * (src/lib/admin/change-request-inbox.ts) and the action endpoint
 * (POST /api/admin/operator/requests/[action]). Design §4.4.
 *
 * The endpoint tests run against a real D1: seed a client-filed request via the
 * frozen createChangeRequest, then action it through the handler and assert the
 * store moved (resolver stamps the SMD actor — the inline audit, foundations §6).
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { POST } from '../src/pages/api/admin/operator/requests/[action]'
import { env as testEnv } from 'cloudflare:workers'
import {
  createChangeRequest,
  listOpenChangeRequests,
  type ChangeRequestRow,
} from '../src/lib/portal/operator/change-request'
import {
  actionToStatus,
  changeRequestDomainLabel,
  requestStatusBadge,
} from '../src/lib/admin/change-request-inbox'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const ORG_ID = 'org-1'
const ENTITY_ID = 'ent-cr'

async function requestsForCustomer(customerSlug: string): Promise<ChangeRequestRow[]> {
  const { results } = await testEnv.DB.prepare(
    `SELECT * FROM operator_change_requests
      WHERE customer_slug = ? ORDER BY created_at DESC LIMIT 50`
  )
    .bind(customerSlug)
    .all<ChangeRequestRow>()
  return results ?? []
}

interface MinimalSession {
  userId: string
  orgId: string
  role: string
  email: string
  expiresAt: string
}

function adminSession(): MinimalSession {
  return {
    userId: 'usr-captain',
    orgId: ORG_ID,
    role: 'admin',
    email: 'captain@example.com',
    expiresAt: '2099-12-31T00:00:00Z',
  }
}

function buildCtx(opts: {
  session: MinimalSession | null
  action: string
  body: unknown
}): Parameters<typeof POST>[0] {
  const request = new Request(`http://test.local/api/admin/operator/requests/${opts.action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body),
  })
  return {
    request,
    params: { action: opts.action },
    locals: { session: opts.session },
  } as unknown as Parameters<typeof POST>[0]
}

describe('change-request-inbox view-model', () => {
  it('actionToStatus maps the three admin actions and rejects others', () => {
    expect(actionToStatus('acknowledge')).toBe('acknowledged')
    expect(actionToStatus('resolve')).toBe('resolved')
    expect(actionToStatus('decline')).toBe('declined')
    expect(actionToStatus('delete')).toBeNull()
    expect(actionToStatus('')).toBeNull()
  })

  it('changeRequestDomainLabel renders a friendly domain label', () => {
    expect(changeRequestDomainLabel('people_access')).toBe('People & access')
    expect(changeRequestDomainLabel('connectors')).toBe('Connectors & credentials')
  })

  it('requestStatusBadge is total over the status union', () => {
    for (const s of ['open', 'acknowledged', 'resolved', 'declined'] as const) {
      const badge = requestStatusBadge(s)
      expect(badge.label.length).toBeGreaterThan(0)
      expect(badge.classes).toContain('rounded-[var(--ss-radius-badge)]')
    }
  })
})

describe('POST /api/admin/operator/requests/[action]', () => {
  beforeAll(() => {
    expect(discoverNumericMigrations(migrationsDir).length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'Test Org', 'test-org', datetime('now'), datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    await db
      .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, 'Acme', 'acme')`)
      .bind(ENTITY_ID, ORG_ID)
      .run()
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
  })

  async function seedRequest(): Promise<number> {
    const res = await createChangeRequest(testEnv.DB, {
      entity_id: ENTITY_ID,
      customer_slug: 'acme',
      domain: 'people_access',
      requested_by_user_id: 'usr-client',
      requested_by_email: 'owner@acme.test',
      summary: 'Please add Jordan as a staff user.',
    })
    if (!res.ok) throw new Error(`seed failed: ${res.error}`)
    return res.id
  }

  it('rejects a non-admin session', async () => {
    const res = await POST(buildCtx({ session: null, action: 'resolve', body: { id: 1 } }))
    expect(res.status).toBe(401)
  })

  it('rejects an unknown action', async () => {
    const res = await POST(
      buildCtx({ session: adminSession(), action: 'destroy', body: { id: 1 } })
    )
    expect(res.status).toBe(404)
  })

  it('rejects a missing / invalid id', async () => {
    const res = await POST(
      buildCtx({ session: adminSession(), action: 'resolve', body: { id: 'x' } })
    )
    expect(res.status).toBe(400)
  })

  it('404s when the request id does not exist', async () => {
    const res = await POST(
      buildCtx({ session: adminSession(), action: 'resolve', body: { id: 9999 } })
    )
    expect(res.status).toBe(404)
  })

  it('resolves a request, stamping the SMD actor and dropping it from the inbox', async () => {
    const id = await seedRequest()
    expect(await listOpenChangeRequests(testEnv.DB)).toHaveLength(1)

    const res = await POST(
      buildCtx({
        session: adminSession(),
        action: 'resolve',
        body: { id, resolution_note: 'Added via config path.' },
      })
    )
    expect(res.status).toBe(200)

    // No longer open; the row carries the resolver identity + note.
    expect(await listOpenChangeRequests(testEnv.DB)).toHaveLength(0)
    const [row] = await requestsForCustomer('acme')
    expect(row.status).toBe('resolved')
    expect(row.resolved_by_email).toBe('captain@example.com')
    expect(row.resolution_note).toBe('Added via config path.')
    expect(row.resolved_at).not.toBeNull()
  })

  it('acknowledge records receipt but keeps the request in the inbox', async () => {
    const id = await seedRequest()
    const res = await POST(
      buildCtx({ session: adminSession(), action: 'acknowledge', body: { id } })
    )
    expect(res.status).toBe(200)
    const open = await listOpenChangeRequests(testEnv.DB)
    expect(open).toHaveLength(1)
    expect(open[0].status).toBe('acknowledged')
  })

  it('declines a request with a note, dropping it from the inbox', async () => {
    const id = await seedRequest()
    const res = await POST(
      buildCtx({
        session: adminSession(),
        action: 'decline',
        body: { id, resolution_note: 'Out of current scope.' },
      })
    )
    expect(res.status).toBe(200)
    expect(await listOpenChangeRequests(testEnv.DB)).toHaveLength(0)
    const [row] = await requestsForCustomer('acme')
    expect(row.status).toBe('declined')
    expect(row.resolution_note).toBe('Out of current scope.')
  })
})
