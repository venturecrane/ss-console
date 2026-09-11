/**
 * Unit tests for the admin session shim (code review 2026-07-02 §4.3).
 *
 * resolveAdminSessionFromClerk is the admin gate's Clerk→legacy bridge: the
 * middleware calls it on every admin path to turn a Clerk user_id into the
 * SessionData shape 73 call sites read. It had no dedicated test. These cover
 * the security-relevant branches: non-admin rejection, missing local user,
 * session synthesis, the KV cache path, and the corrupt-cache fall-through
 * (issue #834 — a bad cache entry must never 500 an authenticated request).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
// KVNamespace is referenced via the ambient global (from the versioned
// @cloudflare/workers-types in tsconfig) so it is the SAME nominal type the
// shim's signature uses — importing the non-versioned index causes a ts(2345)
// identity mismatch when passing the fake KV straight into the function.
import { resolveAdminSessionFromClerk } from '../src/lib/auth/admin-session-shim'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_ID = 'org-1'
const ADMIN_CLERK = 'user_admin_clerk'
const CLIENT_CLERK = 'user_client_clerk'
const cacheKey = (clerkId: string) => `admin-session:${clerkId}`

function createMemoryKv(): { kv: KVNamespace; store: Map<string, string> } {
  const store = new Map<string, string>()
  const kv = {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    put: vi.fn(async (key: string, value: string) => {
      store.set(key, value)
    }),
    delete: vi.fn(async (key: string) => {
      store.delete(key)
    }),
    list: vi.fn(),
    getWithMetadata: vi.fn(),
  } as unknown as KVNamespace
  return { kv, store }
}

describe('resolveAdminSessionFromClerk', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Org', 'org')
      .run()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, clerk_user_id)
         VALUES (?, ?, ?, ?, 'admin', ?)`
      )
      .bind('u-admin', ORG_ID, 'admin@smd.services', 'Admin', ADMIN_CLERK)
      .run()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, clerk_user_id)
         VALUES (?, ?, ?, ?, 'client', ?)`
      )
      .bind('u-client', ORG_ID, 'client@example.com', 'Client', CLIENT_CLERK)
      .run()
  })

  it('synthesizes SessionData for an admin Clerk user', async () => {
    const { kv } = createMemoryKv()
    const session = await resolveAdminSessionFromClerk(ADMIN_CLERK, db, kv)
    expect(session).not.toBeNull()
    expect(session).toMatchObject({
      userId: 'u-admin',
      orgId: ORG_ID,
      role: 'admin',
      email: 'admin@smd.services',
    })
    // expiresAt is a forward-dated ISO string (Clerk owns real expiry).
    expect(new Date(session!.expiresAt).getTime()).toBeGreaterThan(Date.now())
  })

  it('returns null when the Clerk user has no local users row', async () => {
    const { kv } = createMemoryKv()
    expect(await resolveAdminSessionFromClerk('user_nobody', db, kv)).toBeNull()
  })

  it('returns null when the local user exists but is not an admin', async () => {
    const { kv } = createMemoryKv()
    // The client user is real, but the SELECT gates on role = 'admin'.
    expect(await resolveAdminSessionFromClerk(CLIENT_CLERK, db, kv)).toBeNull()
  })

  it('caches the resolved session and serves the second call from KV', async () => {
    const { kv, store } = createMemoryKv()
    const first = await resolveAdminSessionFromClerk(ADMIN_CLERK, db, kv)
    expect(first).not.toBeNull()
    expect(store.has(cacheKey(ADMIN_CLERK))).toBe(true)

    // Remove the DB row; a cache hit must still resolve (proves KV was used).
    await db.prepare('DELETE FROM users WHERE clerk_user_id = ?').bind(ADMIN_CLERK).run()
    const second = await resolveAdminSessionFromClerk(ADMIN_CLERK, db, kv)
    expect(second).toMatchObject({ userId: 'u-admin', role: 'admin' })
  })

  it('falls through to D1 and repopulates when the cache entry is corrupt (#834)', async () => {
    const { kv, store } = createMemoryKv()
    store.set(cacheKey(ADMIN_CLERK), '{ not-valid-json')
    const session = await resolveAdminSessionFromClerk(ADMIN_CLERK, db, kv)
    expect(session).toMatchObject({ userId: 'u-admin', role: 'admin' })
    // Corrupt entry was replaced with a valid one.
    const cached = store.get(cacheKey(ADMIN_CLERK))
    expect(cached).toBeTruthy()
    expect(JSON.parse(cached!).role).toBe('admin')
  })

  it('treats a non-admin cache entry as corrupt and falls through to D1', async () => {
    const { kv, store } = createMemoryKv()
    // A cache value whose role is not 'admin' must never be trusted.
    store.set(
      cacheKey(ADMIN_CLERK),
      JSON.stringify({
        userId: 'attacker',
        orgId: ORG_ID,
        email: 'x@x',
        role: 'client',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      })
    )
    const session = await resolveAdminSessionFromClerk(ADMIN_CLERK, db, kv)
    expect(session).toMatchObject({ userId: 'u-admin', role: 'admin' })
  })
})
