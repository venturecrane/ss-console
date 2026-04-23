/**
 * Integration test for POST /api/admin/entities/[id]/send-booking-link (#467).
 *
 * Verifies the acceptance criteria end-to-end:
 *   - Button behavior matches the label: the endpoint creates a scheduled
 *     assessment row and signs a booking URL. It does NOT perform a bare
 *     stage transition.
 *   - Signed URL has a TTL (14 days default).
 *   - Meeting row is created in status `scheduled` at click time, with
 *     `scheduled_at` null (prospect hasn't picked a slot yet).
 *   - Stage transitions to `assessing` only after the meeting row exists.
 *   - Auth: non-admin sessions are rejected.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { POST } from '../../src/pages/api/admin/entities/[id]/send-booking-link'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { verifyBookingLink, DEFAULT_BOOKING_LINK_TTL_DAYS } from '../../src/lib/booking/signed-link'

const migrationsDir = resolve(process.cwd(), 'migrations')
const TEST_SIGNING_KEY = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='

const ORG_ID = 'org-test'
const ENTITY_ID = 'entity-test'
const ADMIN_ID = 'admin-test'

interface CallOptions {
  session: {
    userId: string
    orgId: string
    role: string
    email: string
    expiresAt: string
  } | null
  entityId: string
  body?: Record<string, unknown>
}

function buildContext(opts: CallOptions) {
  const request = new Request(
    `http://test.local/api/admin/entities/${opts.entityId}/send-booking-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts.body ?? {}),
    }
  )
  return {
    request,
    params: { id: opts.entityId },
    locals: { session: opts.session },
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  }
}

const adminSession = {
  userId: ADMIN_ID,
  orgId: ORG_ID,
  role: 'admin',
  email: 'admin@example.com',
  expiresAt: '2099-01-01T00:00:00Z',
}

describe('POST /api/admin/entities/[id]/send-booking-link (#467)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Org Test', 'org-test')
      .run()

    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at)
         VALUES (?, ?, ?, ?, 'prospect', datetime('now'))`
      )
      .bind(ENTITY_ID, ORG_ID, 'Phoenix Plumbing Co.', 'phoenix-plumbing-co')
      .run()

    await db
      .prepare(
        `INSERT INTO contacts (id, org_id, entity_id, name, email)
         VALUES (?, ?, ?, ?, ?)`
      )
      .bind('contact-1', ORG_ID, ENTITY_ID, 'Maria Garcia', 'maria@phoenixplumbing.example')
      .run()

    Object.assign(testEnv, {
      DB: db,
      BOOKING_ENCRYPTION_KEY: TEST_SIGNING_KEY,
      APP_BASE_URL: 'https://smd.services',
    })
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
  })

  it('creates a scheduled assessment, transitions stage, and returns a signed URL', async () => {
    const ctx = buildContext({
      session: adminSession,
      entityId: ENTITY_ID,
      body: { duration_minutes: 30, meeting_type: 'discovery' },
    })

    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(200)

    const body = (await response.json()) as {
      ok: boolean
      assessment_id: string
      booking_url: string
      token_ttl_days: number
      contact_email: string
      outreach_template: string
      mailto_url: string
    }
    expect(body.ok).toBe(true)
    expect(body.assessment_id).toMatch(/^[0-9a-f-]+$/)
    expect(body.token_ttl_days).toBe(DEFAULT_BOOKING_LINK_TTL_DAYS)
    expect(body.contact_email).toBe('maria@phoenixplumbing.example')
    expect(body.booking_url).toMatch(/^https:\/\/smd\.services\/book\?t=/)
    expect(body.outreach_template).toContain(body.booking_url)
    expect(body.outreach_template).toContain('Maria')
    expect(body.mailto_url).toMatch(/^mailto:/)

    // --- AC: meeting row created in scheduled status, no slot yet -----------
    const assessment = await db
      .prepare('SELECT * FROM assessments WHERE id = ?')
      .bind(body.assessment_id)
      .first<{ status: string; scheduled_at: string | null; entity_id: string; org_id: string }>()
    expect(assessment).not.toBeNull()
    expect(assessment!.status).toBe('scheduled')
    expect(assessment!.scheduled_at).toBeNull()
    expect(assessment!.entity_id).toBe(ENTITY_ID)
    expect(assessment!.org_id).toBe(ORG_ID)

    // --- AC: entity transitioned to `assessing` ----------------------------
    const entity = await db
      .prepare('SELECT stage FROM entities WHERE id = ?')
      .bind(ENTITY_ID)
      .first<{ stage: string }>()
    expect(entity!.stage).toBe('meetings')

    // --- AC: signed URL is verifiable and carries the right payload ---------
    const token = new URL(body.booking_url).searchParams.get('t')
    expect(token).toBeTruthy()
    const verify = await verifyBookingLink(token!)
    expect(verify.ok).toBe(true)
    if (!verify.ok) return
    expect(verify.payload.entity_id).toBe(ENTITY_ID)
    expect(verify.payload.assessment_id).toBe(body.assessment_id)
    expect(verify.payload.contact_id).toBe('contact-1')
    expect(verify.payload.duration_minutes).toBe(30)
    expect(verify.payload.meeting_type).toBe('discovery')

    // --- AC: TTL is ~14 days from now --------------------------------------
    const now = Math.floor(Date.now() / 1000)
    const expected = now + DEFAULT_BOOKING_LINK_TTL_DAYS * 24 * 60 * 60
    expect(verify.payload.exp).toBeGreaterThanOrEqual(expected - 10)
    expect(verify.payload.exp).toBeLessThanOrEqual(expected + 10)

    // --- AC: context timeline gets an outreach_draft entry ------------------
    const contextRow = await db
      .prepare(
        `SELECT type, source, content FROM context
         WHERE entity_id = ? AND type = 'outreach_draft' AND source = 'send_booking_link'`
      )
      .bind(ENTITY_ID)
      .first<{ type: string; source: string; content: string }>()
    expect(contextRow).not.toBeNull()
    expect(contextRow!.content).toContain(body.booking_url)
  })

  it('rejects non-admin sessions with 401', async () => {
    const ctx = buildContext({
      session: { ...adminSession, role: 'client' },
      entityId: ENTITY_ID,
    })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(401)
  })

  it('rejects when session is absent', async () => {
    const ctx = buildContext({ session: null, entityId: ENTITY_ID })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(401)
  })

  it('returns 409 when entity is not in prospect stage', async () => {
    await db.prepare(`UPDATE entities SET stage = 'meetings' WHERE id = ?`).bind(ENTITY_ID).run()

    const ctx = buildContext({ session: adminSession, entityId: ENTITY_ID })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(409)
    const body = (await response.json()) as { error: string }
    expect(body.error).toBe('invalid_stage')

    // The guard ran before anything was mutated: no assessment was created.
    const count = await db
      .prepare(`SELECT COUNT(*) as c FROM assessments WHERE entity_id = ?`)
      .bind(ENTITY_ID)
      .first<{ c: number }>()
    expect(count!.c).toBe(0)
  })

  it('returns 404 when entity does not exist', async () => {
    const ctx = buildContext({ session: adminSession, entityId: 'nonexistent' })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(404)
  })

  it('defaults meeting_type/duration sensibly when omitted', async () => {
    const ctx = buildContext({ session: adminSession, entityId: ENTITY_ID, body: {} })
    const response = await POST(ctx as unknown as Parameters<typeof POST>[0])
    expect(response.status).toBe(200)
    const body = (await response.json()) as { booking_url: string }
    const token = new URL(body.booking_url).searchParams.get('t')
    const verify = await verifyBookingLink(token!)
    if (!verify.ok) throw new Error('expected ok')
    expect(verify.payload.duration_minutes).toBe(30) // BOOKING_CONFIG.slot_minutes default
    expect(verify.payload.meeting_type).toBeNull()
  })
})
