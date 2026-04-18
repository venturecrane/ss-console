/**
 * Cross-org regression test for POST /api/admin/engagements/[id]/milestones.
 *
 * The vulnerability this defends against (issue #399, found in code review
 * 2026-04-16): getMilestone(), updateMilestone(), and deleteMilestone() in
 * src/lib/db/milestones.ts fetched by primary key only, with no org_id
 * predicate. The endpoint validated the engagement was org-scoped, then called
 * getMilestone(env.DB, milestoneId) by ID alone. The only cross-tenant guard
 * was a post-fetch `milestone.engagement_id !== engagementId` check —
 * defense-in-depth that failed once already in this codebase (#172).
 *
 * The fix (migration 0022 + DAL update):
 *   - Added org_id to milestones (backfilled from parent engagement).
 *   - getMilestone(), listMilestones(), updateMilestone(), deleteMilestone()
 *     all accept orgId and add `AND org_id = ?` to every query.
 *   - All callers pass session.orgId.
 *
 * This test seeds two orgs, seeds one milestone in each, then attempts every
 * cross-org mutation path (DELETE, toggle_payment_trigger, transition_status)
 * via an admin session from org A targeting a milestone that belongs to org B.
 * Each must return a redirect to ?error=not_found (status 302), not a 200 or
 * a successful mutation.
 *
 * Architecture note: SS uses Astro APIRoute handlers, not raw worker.fetch().
 * The harness's invoke() assumes the latter, so this test constructs the Astro
 * context ({ request, locals }) by hand — the same pattern used in
 * tests/admin/resend-invitation.cross-org.test.ts.
 *
 * Schema notes (as of migration 0022):
 *   - engagements uses entity_id (client_id was dropped in migration 0010)
 *   - entities table requires slug; stage defaults to 'signal'
 *   - SQLite does not enforce FK constraints without PRAGMA foreign_keys=ON,
 *     so we seed only the rows the handler actually reads (engagements + milestones)
 *     and stub out the FK targets (entities, quotes) as bare ID inserts.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { POST } from '../../src/pages/api/admin/engagements/[id]/milestones'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
// Route handlers import `env` from `cloudflare:workers` (adapter v13 pattern).
// The vitest alias in vitest.config.ts resolves that specifier to a mutable
// stub object — tests populate it per-case via Object.assign(testEnv, {...}).
import { env as testEnv } from 'cloudflare:workers'

const migrationsDir = resolve(process.cwd(), 'migrations')

interface TestEnv {
  DB: D1Database
  STRIPE_API_KEY?: string
}

function buildContext(opts: {
  session: { userId: string; orgId: string; role: string; email: string; expiresAt: string } | null
  engagementId: string
  body: Record<string, string>
}) {
  const formData = new FormData()
  for (const [k, v] of Object.entries(opts.body)) {
    formData.append(k, v)
  }

  const request = new Request(
    `http://test.local/api/admin/engagements/${opts.engagementId}/milestones`,
    {
      method: 'POST',
      body: formData,
    }
  )

  return {
    request,
    params: { id: opts.engagementId },
    locals: {
      session: opts.session,
    },
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  }
}

describe('POST /api/admin/engagements/[id]/milestones — cross-org regression (#399)', () => {
  let db: D1Database
  let env: TestEnv

  // IDs that stay stable across tests in a beforeEach block
  const ORG_A = 'org-a'
  const ORG_B = 'org-b'
  const ENGAGEMENT_A = 'engagement-a'
  const ENGAGEMENT_B = 'engagement-b'
  const MILESTONE_A = 'milestone-a'
  const MILESTONE_B = 'milestone-b'
  const ADMIN_A = 'admin-in-a'

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Seed two organizations.
    for (const [id, name, slug] of [
      [ORG_A, 'Org A', 'org-a'],
      [ORG_B, 'Org B', 'org-b'],
    ]) {
      await db
        .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
        .bind(id, name, slug)
        .run()
    }

    // Seed entities (required FK target for engagements and quotes).
    for (const [orgId, suffix] of [
      [ORG_A, 'a'],
      [ORG_B, 'b'],
    ]) {
      await db
        .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)`)
        .bind(`entity-${suffix}`, orgId, `Entity ${suffix.toUpperCase()}`, `entity-${suffix}`)
        .run()
    }

    // Seed assessments (required FK target for quotes).
    for (const [orgId, suffix] of [
      [ORG_A, 'a'],
      [ORG_B, 'b'],
    ]) {
      await db
        .prepare(
          `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
        )
        .bind(`assessment-${suffix}`, orgId, `entity-${suffix}`)
        .run()
    }

    // Seed quotes (required FK target for engagements).
    for (const [orgId, suffix] of [
      [ORG_A, 'a'],
      [ORG_B, 'b'],
    ]) {
      await db
        .prepare(
          `INSERT INTO quotes (id, org_id, entity_id, assessment_id, line_items, total_hours, rate, total_price, status)
           VALUES (?, ?, ?, ?, '[]', 10, 175, 1750, 'accepted')`
        )
        .bind(`quote-${suffix}`, orgId, `entity-${suffix}`, `assessment-${suffix}`)
        .run()
    }

    // Seed engagements.
    for (const [id, orgId, suffix] of [
      [ENGAGEMENT_A, ORG_A, 'a'],
      [ENGAGEMENT_B, ORG_B, 'b'],
    ]) {
      await db
        .prepare(
          `INSERT INTO engagements (id, org_id, entity_id, quote_id, status)
           VALUES (?, ?, ?, ?, 'active')`
        )
        .bind(id, orgId, `entity-${suffix}`, `quote-${suffix}`)
        .run()
    }

    // Seed one milestone in each engagement.
    // org_id is set directly because migration 0022 added the column.
    for (const [id, orgId, engagementId] of [
      [MILESTONE_A, ORG_A, ENGAGEMENT_A],
      [MILESTONE_B, ORG_B, ENGAGEMENT_B],
    ]) {
      await db
        .prepare(
          `INSERT INTO milestones (id, engagement_id, org_id, name, status, payment_trigger, sort_order)
           VALUES (?, ?, ?, 'Test Milestone', 'pending', 0, 0)`
        )
        .bind(id, engagementId, orgId)
        .run()
    }

    // Seed admin user in org A.
    await db
      .prepare('INSERT INTO users (id, org_id, email, name, role) VALUES (?, ?, ?, ?, ?)')
      .bind(ADMIN_A, ORG_A, 'admin-a@example.com', 'Admin A', 'admin')
      .run()

    env = { DB: db }
    Object.assign(testEnv, env)
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
  })

  /**
   * Call the handler as org A's admin, targeting the given engagementId.
   */
  async function callAsOrgAAdmin(
    engagementId: string,
    body: Record<string, string>
  ): Promise<Response> {
    const ctx = buildContext({
      session: {
        userId: ADMIN_A,
        orgId: ORG_A,
        role: 'admin',
        email: 'admin-a@example.com',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
      engagementId,
      body,
    })
    return await POST(ctx as unknown as Parameters<typeof POST>[0])
  }

  // ============================================================
  // Cross-org regression tests (#399)
  // ============================================================

  it('returns 302 not_found when org A admin attempts DELETE of an org B milestone', async () => {
    // Org A admin targets the correct engagement ID (org A's), but provides
    // the milestone ID from org B. The DAL must not find it.
    const response = await callAsOrgAAdmin(ENGAGEMENT_A, {
      _method: 'DELETE',
      milestone_id: MILESTONE_B,
    })

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toContain('error=not_found')

    // Verify org B's milestone was NOT deleted.
    const row = await db
      .prepare('SELECT id FROM milestones WHERE id = ?')
      .bind(MILESTONE_B)
      .first<{ id: string }>()
    expect(row).not.toBeNull()
  })

  it('returns 302 not_found when org A admin attempts toggle_payment_trigger on an org B milestone', async () => {
    const response = await callAsOrgAAdmin(ENGAGEMENT_A, {
      action: 'toggle_payment_trigger',
      milestone_id: MILESTONE_B,
    })

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toContain('error=not_found')

    // Verify org B's milestone payment_trigger was NOT changed.
    const row = await db
      .prepare('SELECT payment_trigger FROM milestones WHERE id = ?')
      .bind(MILESTONE_B)
      .first<{ payment_trigger: number }>()
    expect(row?.payment_trigger).toBe(0)
  })

  it('returns 302 not_found when org A admin attempts transition_status on an org B milestone', async () => {
    const response = await callAsOrgAAdmin(ENGAGEMENT_A, {
      action: 'transition_status',
      milestone_id: MILESTONE_B,
      new_status: 'in_progress',
    })

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toContain('error=not_found')

    // Verify org B's milestone status was NOT changed.
    const row = await db
      .prepare('SELECT status FROM milestones WHERE id = ?')
      .bind(MILESTONE_B)
      .first<{ status: string }>()
    expect(row?.status).toBe('pending')
  })

  // ============================================================
  // Same-org positive control — proves the scoped path works
  // ============================================================

  it('returns 302 milestone_deleted when org A admin deletes their own milestone', async () => {
    const response = await callAsOrgAAdmin(ENGAGEMENT_A, {
      _method: 'DELETE',
      milestone_id: MILESTONE_A,
    })

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toContain('milestone_deleted=1')

    // Verify org A's milestone was deleted.
    const row = await db
      .prepare('SELECT id FROM milestones WHERE id = ?')
      .bind(MILESTONE_A)
      .first<{ id: string }>()
    expect(row).toBeNull()
  })

  it('returns 302 saved=1 when org A admin toggles payment_trigger on their own milestone', async () => {
    const response = await callAsOrgAAdmin(ENGAGEMENT_A, {
      action: 'toggle_payment_trigger',
      milestone_id: MILESTONE_A,
    })

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toContain('saved=1')

    // Verify the toggle took effect.
    const row = await db
      .prepare('SELECT payment_trigger FROM milestones WHERE id = ?')
      .bind(MILESTONE_A)
      .first<{ payment_trigger: number }>()
    expect(row?.payment_trigger).toBe(1)
  })

  // ============================================================
  // Auth guards
  // ============================================================

  it('returns 401 when no session is attached', async () => {
    const ctx = buildContext({
      session: null,
      engagementId: ENGAGEMENT_A,
      body: { _method: 'DELETE', milestone_id: MILESTONE_A },
    })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(401)
  })
})
