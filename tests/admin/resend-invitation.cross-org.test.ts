/**
 * Cross-org regression test for POST /api/admin/resend-invitation.
 *
 * The bug this defends against (issue #172, fix #176): the original
 * resend-invitation handler looked up users by `id` only, without scoping
 * to the caller's `org_id`. An admin in one organization could resend an
 * invitation for any client user in any organization by guessing IDs,
 * which leaks the email address (and triggers an unauthorized email send)
 * across tenant boundaries.
 *
 * The fix at src/pages/api/admin/resend-invitation.ts:57-61 added
 * `AND org_id = ?` to the SELECT and (defense-in-depth) to the UPDATE.
 * This test exercises the full handler in-process via the
 * @venturecrane/crane-test-harness primitives so the SQL predicate is
 * verified end-to-end on every CI run.
 *
 * If anyone removes the org_id scoping from either the SELECT or the
 * UPDATE in the future, the cross-org case in this test fails immediately.
 *
 * Architecture note: SS uses Astro APIRoute handlers, not raw
 * `worker.fetch(req, env)`. The harness's `invoke()` helper assumes the
 * latter, so this test bypasses `invoke()` and constructs the Astro
 * context (`{ request, locals }`) by hand. The harness's `createTestD1`
 * and `runMigrations` primitives still apply unchanged — they're
 * framework-agnostic.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { POST } from '../../src/pages/api/admin/resend-invitation'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
// Route handlers import `env` from `cloudflare:workers` (adapter v13 pattern).
// The vitest alias in vitest.config.ts resolves that specifier to a mutable
// stub object — tests populate it per-case via Object.assign(testEnv, {...}).
import { env as testEnv } from 'cloudflare:workers'

const migrationsDir = resolve(process.cwd(), 'migrations')

interface TestEnv {
  DB: D1Database
  APP_BASE_URL: string
  RESEND_API_KEY?: string
}

function buildContext(opts: {
  session: { userId: string; orgId: string; role: string; email: string; expiresAt: string } | null
  body: unknown
}) {
  const request = new Request('http://test.local/api/admin/resend-invitation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts.body),
  })
  return {
    request,
    locals: {
      session: opts.session,
    },
  }
}

describe('POST /api/admin/resend-invitation — cross-org regression', () => {
  let db: D1Database
  let env: TestEnv

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Seed two organizations.
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-a', 'Org A', 'org-a')
      .run()
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-b', 'Org B', 'org-b')
      .run()

    // Seed one client user in each organization.
    await db
      .prepare('INSERT INTO users (id, org_id, email, name, role) VALUES (?, ?, ?, ?, ?)')
      .bind('user-in-a', 'org-a', 'client-a@example.com', 'Client A', 'client')
      .run()
    await db
      .prepare('INSERT INTO users (id, org_id, email, name, role) VALUES (?, ?, ?, ?, ?)')
      .bind('user-in-b', 'org-b', 'client-b@example.com', 'Client B', 'client')
      .run()

    // Seed one admin user in org A so we have a session to attach.
    await db
      .prepare('INSERT INTO users (id, org_id, email, name, role) VALUES (?, ?, ?, ?, ?)')
      .bind('admin-in-a', 'org-a', 'admin-a@example.com', 'Admin A', 'admin')
      .run()

    env = {
      DB: db,
      APP_BASE_URL: 'http://test.local',
      // RESEND_API_KEY left undefined → sendEmail returns dev-mode success
      // without making any network calls. See src/lib/email/resend.ts:46-52.
    }
    Object.assign(testEnv, env)
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
  })

  /**
   * Helper to call the handler with an admin session in the named org.
   */
  async function callAsAdminFromOrg(orgId: string, body: unknown): Promise<Response> {
    const context = buildContext({
      session: {
        userId: 'admin-in-a',
        orgId,
        role: 'admin',
        email: 'admin-a@example.com',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
      body,
    })
    // The handler signature is APIRoute = (context) => Promise<Response>.
    // We pass our hand-built context. The cast is necessary because the
    // real Astro `Locals` type is augmented at runtime via the cloudflare
    // adapter; for tests, only the fields the handler reads matter.
    return await POST(context as unknown as Parameters<typeof POST>[0])
  }

  // ==========================================================================
  // The regression test (#172/#176)
  // ==========================================================================

  it('returns 404 when org A admin requests resend for an org B user', async () => {
    const response = await callAsAdminFromOrg('org-a', { userId: 'user-in-b' })
    expect(response.status).toBe(404)

    const body = (await response.json()) as { error: string }
    expect(body.error).toBe('Client user not found')

    // No magic_link should have been created — the request should have
    // failed at the SELECT predicate before reaching createMagicLink.
    const magicLinks = await db
      .prepare('SELECT COUNT(*) as c FROM magic_links')
      .first<{ c: number }>()
    expect(magicLinks?.c).toBe(0)
  })

  it('returns 404 when org A admin tries to update an org B user email via resend', async () => {
    // Even when the admin sends a new email (the OQ-010 bounce-recovery path),
    // the handler must NOT update users in other organizations. The
    // defense-in-depth UPDATE at lines 78-86 also has `AND org_id = ?`.
    const response = await callAsAdminFromOrg('org-a', {
      userId: 'user-in-b',
      email: 'attacker-controlled@evil.example.com',
    })
    expect(response.status).toBe(404)

    // Org B's user email must be unchanged.
    const orgBUser = await db
      .prepare('SELECT email FROM users WHERE id = ?')
      .bind('user-in-b')
      .first<{ email: string }>()
    expect(orgBUser?.email).toBe('client-b@example.com')
  })

  // ==========================================================================
  // Same-org positive control — proves the org-scoped query path works
  // ==========================================================================

  it('returns 200 when org A admin requests resend for an org A user', async () => {
    const response = await callAsAdminFromOrg('org-a', { userId: 'user-in-a' })
    expect(response.status).toBe(200)

    const body = (await response.json()) as {
      success: boolean
      sentTo: string
      emailId: string
    }
    expect(body.success).toBe(true)
    expect(body.sentTo).toBe('client-a@example.com')

    // createMagicLink should have written exactly one row.
    const magicLinks = await db
      .prepare('SELECT email FROM magic_links WHERE email = ?')
      .bind('client-a@example.com')
      .all<{ email: string }>()
    expect(magicLinks.results).toHaveLength(1)
  })

  // ==========================================================================
  // Auth + validation guards (also part of the regression surface)
  // ==========================================================================

  it('returns 401 when no session is attached', async () => {
    const context = buildContext({
      session: null,
      body: { userId: 'user-in-a' },
    })
    const response = await POST(context as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(401)
  })

  it('returns 401 when session role is not admin', async () => {
    const context = buildContext({
      session: {
        userId: 'user-in-a',
        orgId: 'org-a',
        role: 'client', // not admin
        email: 'client-a@example.com',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
      body: { userId: 'user-in-a' },
    })
    const response = await POST(context as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(401)
  })

  it('returns 400 when userId is missing from the body', async () => {
    const response = await callAsAdminFromOrg('org-a', { somethingElse: 'no userId' })
    expect(response.status).toBe(400)
  })
})
