/**
 * GET /api/auth/google/connect resolves the admin identity itself.
 *
 * The middleware runs the Clerk-to-admin shim only on `/admin` and
 * `/api/admin` paths and exempts `/api/auth/*` from the admin rewrite, so
 * `locals.session` is always null on this route. Until 2026-09-10 the route
 * gated on it and sent every admin who clicked Connect on the Google settings
 * page back to sign-in (code review 2026-09-10, docs wave).
 *
 * Both directions are pinned against a real D1 with every migration applied:
 * no identity, or a Clerk identity that is not an admin, is refused; an admin
 * identity is sent to Google's consent screen with a state nonce that exists
 * in oauth_states and names that admin.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database, KVNamespace } from '@cloudflare/workers-types'
import { resolve } from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import { GET } from '../src/pages/api/auth/google/connect'

installWorkerdPolyfills()

const ORG = 'org-gc'
const ADMIN_CLERK_ID = 'user_admin_gc'
const CLIENT_CLERK_ID = 'user_client_gc'

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

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, 'GC', 'gc', datetime('now'), datetime('now'))`
    )
    .bind(ORG)
    .run()
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id) VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind('u-admin', ORG, 'admin@example.test', 'Admin', 'admin', ADMIN_CLERK_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id) VALUES (?, ?, ?, ?, ?, ?)`
    )
    .bind('u-client', ORG, 'client@example.test', 'Client', 'client', CLIENT_CLERK_ID)
    .run()
}

function invoke(clerkUserId: string | null): Promise<Response> {
  const ctx = {
    // What the middleware leaves on this path: never a session, only Clerk.
    locals: { session: null, auth: () => ({ userId: clerkUserId }) },
    redirect: (location: string, status = 302) =>
      new Response(null, { status, headers: { Location: location } }),
  }
  return Promise.resolve(GET(ctx as unknown as Parameters<typeof GET>[0]))
}

describe('GET /api/auth/google/connect', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, {
      DB: db,
      SESSIONS: memoryKv(),
      GOOGLE_CLIENT_ID: 'gc-client-id',
      ADMIN_BASE_URL: 'https://admin.example.test',
    })
  })

  it('refuses a request with no identity', async () => {
    const res = await invoke(null)
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toBe('/auth/sign-in?error=unauthorized')
    const rows = await db.prepare('SELECT COUNT(*) AS n FROM oauth_states').first<{ n: number }>()
    expect(rows?.n).toBe(0)
  })

  it('refuses a Clerk identity that is not an admin', async () => {
    const res = await invoke(CLIENT_CLERK_ID)
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toBe('/auth/sign-in?error=unauthorized')
  })

  it('sends an admin to Google with a state nonce that names them', async () => {
    const res = await invoke(ADMIN_CLERK_ID)
    expect(res.status).toBe(302)
    const location = new URL(res.headers.get('Location') ?? '')
    expect(location.origin + location.pathname).toBe('https://accounts.google.com/o/oauth2/v2/auth')
    expect(location.searchParams.get('client_id')).toBe('gc-client-id')
    expect(location.searchParams.get('redirect_uri')).toBe(
      'https://admin.example.test/api/auth/google/callback'
    )
    const state = location.searchParams.get('state')
    expect(state).toBeTruthy()
    const row = await db
      .prepare('SELECT org_id, provider, initiated_by FROM oauth_states WHERE state = ?')
      .bind(state)
      .first<{ org_id: string; provider: string; initiated_by: string }>()
    expect(row).toEqual({ org_id: ORG, provider: 'google_calendar', initiated_by: 'u-admin' })
  })
})
