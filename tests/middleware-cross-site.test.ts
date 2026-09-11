/**
 * The CSRF defence-in-depth check, driven through the real middleware.
 *
 * Same harness shape as tests/middleware-behavior.test.ts (Clerk middleware
 * mocked to a pass-through, real migrated D1, the test owns locals.auth()),
 * narrowed to the one branch that file does not cover: a state-changing
 * request on a signed-in surface that the browser itself labels cross-site.
 * The order matters and is asserted: an unauthenticated cross-site POST is an
 * auth failure, not a CSRF refusal, because the cookie never came along.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database, KVNamespace } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { ORG_ID } from '../src/lib/constants'

vi.mock('@clerk/astro/server', () => ({
  clerkMiddleware: () => (_ctx: unknown, next: () => unknown) => next(),
}))

import { onRequest } from '../src/middleware'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')
const ADMIN_CLERK_ID = 'user_admin_clerk'
const CLIENT_CLERK_ID = 'user_client_clerk'
const NEXT_MARKER = 'X-Next'

function memoryKv(): KVNamespace {
  const store = new Map<string, string>()
  return {
    get: async (key: string) => store.get(key) ?? null,
    put: async (key: string, value: string) => {
      store.set(key, value)
    },
    delete: async (key: string) => {
      store.delete(key)
    },
  } as unknown as KVNamespace
}

async function invoke(opts: {
  url: string
  method: string
  headers?: Record<string, string>
  userId: string | null
}): Promise<Response> {
  const request = new Request(opts.url, { method: opts.method, headers: opts.headers })
  const context = {
    request,
    url: new URL(opts.url),
    locals: { auth: () => ({ userId: opts.userId, orgId: null }), session: null as unknown },
    rewrite: (req: Request) =>
      new Response('rewrite', {
        status: 200,
        headers: { 'X-Rewrite-To': new URL(req.url).pathname },
      }),
    redirect: (location: string, status = 302) =>
      new Response(null, { status, headers: { Location: location } }),
  }
  const next = async () => new Response('next', { status: 200, headers: { [NEXT_MARKER]: '1' } })
  const handler = onRequest as (ctx: unknown, n: unknown) => Promise<Response>
  return handler(context, next)
}

describe('middleware: cross-site mutation guard', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, clerk_user_id) VALUES (?, ?, ?, ?, 'admin', ?)`
      )
      .bind('u-admin', ORG_ID, 'admin@smd.services', 'Admin', ADMIN_CLERK_ID)
      .run()
    Object.assign(testEnv, { DB: db, SESSIONS: memoryKv() })
    delete (testEnv as unknown as Record<string, unknown>).SENTRY_DSN
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  const ADMIN_POST = 'https://admin.smd.services/api/admin/clients/1/operator-price'
  const PORTAL_POST = 'https://portal.smd.services/api/portal/billing/manage'

  it('refuses an authenticated admin POST that the browser labels cross-site', async () => {
    const res = await invoke({
      url: ADMIN_POST,
      method: 'POST',
      headers: { 'sec-fetch-site': 'cross-site' },
      userId: ADMIN_CLERK_ID,
    })
    expect(res.status).toBe(403)
    expect(await res.json()).toMatchObject({
      error: 'cross_site_request',
      detail: 'sec-fetch-site',
    })
  })

  it('refuses an authenticated portal POST whose Origin is another site', async () => {
    const res = await invoke({
      url: PORTAL_POST,
      method: 'POST',
      headers: { origin: 'https://evil.example' },
      userId: CLIENT_CLERK_ID,
    })
    expect(res.status).toBe(403)
    expect(await res.json()).toMatchObject({
      error: 'cross_site_request',
      detail: 'origin-mismatch',
    })
  })

  it('lets a same-origin authenticated POST through to the route', async () => {
    const res = await invoke({
      url: ADMIN_POST,
      method: 'POST',
      headers: { 'sec-fetch-site': 'same-origin', origin: 'https://admin.smd.services' },
      userId: ADMIN_CLERK_ID,
    })
    expect(res.headers.get(NEXT_MARKER)).toBe('1')
  })

  it('lets a non-browser POST (no fetch metadata, no Origin) through to the route', async () => {
    const res = await invoke({ url: ADMIN_POST, method: 'POST', userId: ADMIN_CLERK_ID })
    expect(res.headers.get(NEXT_MARKER)).toBe('1')
  })

  it('does not touch a cross-site GET', async () => {
    const res = await invoke({
      url: 'https://admin.smd.services/api/admin/clients',
      method: 'GET',
      headers: { 'sec-fetch-site': 'cross-site' },
      userId: ADMIN_CLERK_ID,
    })
    expect(res.headers.get(NEXT_MARKER)).toBe('1')
  })

  it('reports an UNauthenticated cross-site admin POST as the auth failure it is (401), not CSRF', async () => {
    const res = await invoke({
      url: ADMIN_POST,
      method: 'POST',
      headers: { 'sec-fetch-site': 'cross-site' },
      userId: null,
    })
    expect(res.status).toBe(401)
  })

  it('never applies to a cross-site webhook POST (not a signed-in surface)', async () => {
    const res = await invoke({
      url: 'https://smd.services/api/webhooks/stripe',
      method: 'POST',
      headers: { 'sec-fetch-site': 'cross-site', origin: 'https://stripe.com' },
      userId: null,
    })
    expect(res.headers.get(NEXT_MARKER)).toBe('1')
  })
})
