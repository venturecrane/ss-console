/**
 * The admin milestone route, against a real migrated D1, when the quote's line
 * items cannot be read row for row.
 *
 * Before 2026-09-25 completing a payment milestone cast the stored line items
 * and multiplied `estimated_hours` by the rate; a row without numeric hours put
 * NaN into the invoice amount, after the milestone had already been marked
 * completed (review 2026-09-25, Code Quality 2). The route now prices the
 * invoice first: an unreadable quote is refused before anything is written, the
 * admin is sent back with `error=line_items_unreadable`, and Sentry hears it.
 *
 * What would make this false: an invoice row, a completed milestone, or a
 * redirect that reads as an ordinary invalid transition.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

vi.mock('../../src/lib/observability/sentry', () => ({
  captureError: vi.fn(),
  captureWarning: vi.fn(),
}))

import { captureError } from '../../src/lib/observability/sentry'
import { POST } from '../../src/pages/api/admin/engagements/[id]/milestones'

const ORG = 'org-a'
const ENGAGEMENT = 'engagement-a'
const FIRST = 'milestone-1'
const SECOND = 'milestone-2'

async function seed(db: D1Database, lineItems: string): Promise<void> {
  await db
    .prepare("INSERT INTO organizations (id, name, slug) VALUES (?, 'Org A', 'org-a')")
    .bind(ORG)
    .run()
  await db
    .prepare(
      "INSERT INTO entities (id, org_id, name, slug) VALUES ('entity-a', ?, 'Entity A', 'entity-a')"
    )
    .bind(ORG)
    .run()
  await db
    .prepare(
      "INSERT INTO assessments (id, org_id, entity_id, status) VALUES ('assessment-a', ?, 'entity-a', 'completed')"
    )
    .bind(ORG)
    .run()
  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, line_items, total_hours, rate, total_price, status)
       VALUES ('quote-a', ?, 'entity-a', 'assessment-a', ?, 20, 175, 3500, 'accepted')`
    )
    .bind(ORG, lineItems)
    .run()
  await db
    .prepare(
      `INSERT INTO engagements (id, org_id, entity_id, quote_id, status) VALUES (?, ?, 'entity-a', 'quote-a', 'active')`
    )
    .bind(ENGAGEMENT, ORG)
    .run()
  for (const [id, order] of [
    [FIRST, 0],
    [SECOND, 1],
  ] as const) {
    await db
      .prepare(
        `INSERT INTO milestones (id, engagement_id, org_id, name, status, payment_trigger, sort_order)
         VALUES (?, ?, ?, ?, 'in_progress', 1, ?)`
      )
      .bind(id, ENGAGEMENT, ORG, `Phase ${order + 1}`, order)
      .run()
  }
}

async function complete(milestoneId: string): Promise<Response> {
  const form = new FormData()
  form.append('action', 'transition_status')
  form.append('milestone_id', milestoneId)
  form.append('new_status', 'completed')
  const ctx = {
    request: new Request(`http://test.local/api/admin/engagements/${ENGAGEMENT}/milestones`, {
      method: 'POST',
      body: form,
    }),
    params: { id: ENGAGEMENT },
    locals: {
      session: {
        userId: 'admin-a',
        orgId: ORG,
        role: 'admin',
        email: 'admin-a@example.com',
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
      },
    },
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  }
  return await POST(ctx as unknown as Parameters<typeof POST>[0])
}

describe('completing a payment milestone whose quote line items cannot be read', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    Object.assign(testEnv, { DB: db })
    vi.mocked(captureError).mockClear()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    vi.restoreAllMocks()
  })

  for (const [label, lineItems] of [
    [
      'a row without estimated_hours',
      '[{"problem":"P1","description":"d"},{"problem":"P2","description":"d","estimated_hours":10}]',
    ],
    [
      'a row with non-numeric estimated_hours',
      '[{"problem":"P1","description":"d","estimated_hours":"ten"},{"problem":"P2","description":"d","estimated_hours":10}]',
    ],
  ]) {
    it(`${label}: refused before any write, with its own error, and captured`, async () => {
      await seed(db, lineItems)
      const res = await complete(FIRST)

      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toContain('error=line_items_unreadable')
      const milestone = await db
        .prepare('SELECT status, completed_at FROM milestones WHERE id = ?')
        .bind(FIRST)
        .first<{ status: string; completed_at: string | null }>()
      expect(milestone).toEqual({ status: 'in_progress', completed_at: null })
      const invoices = await db.prepare('SELECT COUNT(*) AS n FROM invoices').first<{ n: number }>()
      expect(invoices?.n).toBe(0)
      expect(captureError).toHaveBeenCalledWith(
        expect.any(Error),
        'api/admin/engagements/milestones'
      )
    })
  }

  it('a readable quote still invoices the pro-rata amount (the control)', async () => {
    await seed(
      db,
      '[{"problem":"P1","description":"d","estimated_hours":10},{"problem":"P2","description":"d","estimated_hours":10}]'
    )
    const res = await complete(FIRST)

    expect(res.headers.get('Location')).toContain('saved=1')
    const invoice = await db
      .prepare('SELECT type, amount, status FROM invoices')
      .first<{ type: string; amount: number; status: string }>()
    expect(invoice).toEqual({ type: 'milestone', amount: 1750, status: 'draft' })
    expect(captureError).not.toHaveBeenCalled()
  })
})
