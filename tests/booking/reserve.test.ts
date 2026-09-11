/**
 * End-to-end behavioral tests for POST /api/booking/reserve.
 *
 * Exercises the three-phase commit (rate-limit / DB commit / Google sync)
 * with real D1 migrations via @venturecrane/crane-test-harness. External
 * services (Google Calendar, Resend) are mocked at the helper boundary so
 * the handler's own wiring — validation, rollback on Google failure,
 * post-commit confirmation emails — is what's actually under test.
 *
 * Coverage matches the actual handler shape (src/pages/api/booking/reserve.ts).
 * Notably we do NOT test "outside business hours" rejection because the
 * handler does not enforce that — slot bounds are enforced at slot-listing
 * time. The reserve handler only validates parseability + 24h min_notice.
 */

import { describe, it, expect, beforeEach, afterEach, vi, beforeAll } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database, KVNamespace } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { ORG_ID } from '../../src/lib/constants'
import { BOOKING_CONFIG } from '../../src/lib/booking/config'

// ---------------------------------------------------------------------------
// Mocks for external boundaries.
// ---------------------------------------------------------------------------
//
// Google Calendar event creation lives in lib/booking/reserve-helpers.ts — mock it so we
// can flip success/failure per test. The other helpers (jsonResponse,
// trimString, etc.) are pure utilities; we re-export them unchanged.
//
// Top-level vi.mock factories cannot reference outer-scope variables
// (vitest hoists them), so we expose mutable knobs via a setter.
let googleEventResult: { eventId: string; htmlLink: string | null } | Error = {
  eventId: 'gcal-event-test-001',
  htmlLink: 'https://calendar.google.com/event?eid=test',
}

vi.mock('../../src/lib/booking/reserve-helpers', async () => {
  const actual = await vi.importActual<typeof import('../../src/lib/booking/reserve-helpers')>(
    '../../src/lib/booking/reserve-helpers'
  )
  return {
    ...actual,
    createGoogleCalendarEvent: vi.fn(async () => {
      if (googleEventResult instanceof Error) throw googleEventResult
      return googleEventResult
    }),
  }
})

// Resend email is best-effort in the handler — failures are caught and
// logged. Mock it to a no-op so tests don't make real HTTP calls.
vi.mock('../../src/lib/email/resend', () => ({
  sendEmail: vi.fn(async () => ({ success: true, id: 'mock-resend' })),
}))

// Stub the integrations module so we don't need to encrypt a refresh token
// or run the OAuth refresh dance. The real getGoogleAccessToken and
// getIntegration are both covered by their own tests.
let integrationStub: { id: string; calendar_id: string; org_id: string } | null | undefined =
  undefined
let accessTokenStub: string | null = 'fake-access-token'

vi.mock('../../src/lib/db/integrations', async () => {
  const actual = await vi.importActual<typeof import('../../src/lib/db/integrations')>(
    '../../src/lib/db/integrations'
  )
  return {
    ...actual,
    getIntegration: vi.fn(async () => integrationStub),
    getGoogleAccessToken: vi.fn(async () => accessTokenStub),
  }
})

// Import the handler AFTER mocks are declared.
import { POST } from '../../src/pages/api/booking/reserve'

// ---------------------------------------------------------------------------
// Test KV (in-memory) to back BOOKING_CACHE for rate-limit assertions.
// ---------------------------------------------------------------------------

function createMemoryKv(): KVNamespace {
  const store = new Map<string, string>()
  return {
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
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const migrationsDir = resolve(process.cwd(), 'migrations')

installWorkerdPolyfills()

function buildContext(opts: { body: Record<string, unknown>; ip?: string }) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (opts.ip) headers.set('cf-connecting-ip', opts.ip)
  const request = new Request('http://test.local/api/booking/reserve', {
    method: 'POST',
    headers,
    body: JSON.stringify(opts.body),
  })
  return {
    request,
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof POST>[0]
}

/**
 * Parse a Response body as JSON with a caller-supplied shape. Vitest assertions
 * are loose enough to accept `unknown`, but TypeScript's strict mode rejects
 * member access on it — and ESLint flags `as` casts as redundant when used
 * inline. A tiny typed helper threads the needle.
 */
async function parseJson<T>(res: Response): Promise<T> {
  return res.json()
}

/** Returns an ISO timestamp far enough in the future to satisfy min_notice. */
function futureSlotIso(daysAhead = 3): string {
  const d = new Date(Date.now() + daysAhead * 24 * 60 * 60 * 1000)
  d.setUTCMinutes(0, 0, 0)
  return d.toISOString()
}

/** Returns an ISO timestamp before the min_notice window. */
function nearTermSlotIso(): string {
  return new Date(Date.now() + 30 * 60 * 1000).toISOString()
}

const validBody = (overrides: Record<string, unknown> = {}) => ({
  name: 'Maria Garcia',
  email: 'maria@phoenixplumbing.example',
  business_name: 'Phoenix Plumbing Co.',
  phone: '+1-602-555-0100',
  slot_start_utc: futureSlotIso(),
  ...overrides,
})

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('POST /api/booking/reserve', () => {
  let db: D1Database
  let kv: KVNamespace

  beforeAll(() => {
    expect(discoverNumericMigrations(migrationsDir).length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    // Migration 0003_seed_data already inserts the SMD Services org row
    // matching ORG_ID, so we don't reseed here.

    kv = createMemoryKv()

    Object.assign(testEnv, {
      DB: db,
      BOOKING_CACHE: kv,
      RESEND_API_KEY: 'fake-resend-key',
      APP_BASE_URL: 'https://smd.services',
      ADMIN_BASE_URL: 'https://admin.smd.services',
    })

    integrationStub = {
      id: 'integration-test',
      calendar_id: 'primary',
      org_id: ORG_ID,
    }
    accessTokenStub = 'fake-access-token'
    googleEventResult = {
      eventId: 'gcal-event-test-001',
      htmlLink: 'https://calendar.google.com/event?eid=test',
    }
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.clearAllMocks()
  })

  // -------------------------------------------------------------------------
  // Validation
  // -------------------------------------------------------------------------

  describe('input validation', () => {
    it('rejects requests missing required fields with 400', async () => {
      const res = await POST(
        buildContext({
          body: { name: 'Maria', email: '', business_name: 'Phoenix Plumbing Co.' },
        })
      )
      expect(res.status).toBe(400)
      const json = await parseJson<{ error: string; message: string }>(res)
      expect(json.error).toBe('validation_failed')
      expect(json.message).toContain('required')
    })

    it('rejects requests with an invalid email with 400', async () => {
      const res = await POST(
        buildContext({
          body: validBody({ email: 'not-an-email' }),
        })
      )
      expect(res.status).toBe(400)
      const json = await parseJson<{ error: string; message: string }>(res)
      expect(json.error).toBe('validation_failed')
      expect(json.message).toBe('Invalid email address')
    })

    it('rejects past or near-term slots inside min_notice with 400 slot_unavailable', async () => {
      const res = await POST(
        buildContext({
          body: validBody({ slot_start_utc: nearTermSlotIso() }),
        })
      )
      expect(res.status).toBe(400)
      const json = await parseJson<{ error: string }>(res)
      expect(json.error).toBe('slot_unavailable')
    })

    it('accepts bookings without phone (V3 intake form drops the phone field)', async () => {
      // Phone was historically required for non-prefill-token bookings, but
      // the V3 unified intake form no longer collects it. Reserve treats
      // phone as optional; admin send-link flows continue to work
      // unchanged via prefill_token.
      const res = await POST(
        buildContext({
          body: validBody({ phone: '' }),
        })
      )
      expect(res.status).toBe(201)
    })

    it('accepts bookings without business_name and derives it from the email domain', async () => {
      const res = await POST(
        buildContext({
          body: validBody({ business_name: '', email: 'jane@acme.com' }),
        })
      )
      expect(res.status).toBe(201)
    })
  })

  // -------------------------------------------------------------------------
  // Rate limiting
  // -------------------------------------------------------------------------

  it('returns 429 when the IP rate-limit bucket is full', async () => {
    // The reserve bucket caps at 10/hour per IP. Fast-forward by pre-loading
    // the KV with a count at the limit for the current window.
    const ip = '203.0.113.5'
    const windowSeconds = 60 * 60
    const windowId = Math.floor(Date.now() / 1000 / windowSeconds)
    const key = `rl:reserve:${ip}:${windowId}`
    await kv.put(key, '10')

    const res = await POST(
      buildContext({
        body: validBody(),
        ip,
      })
    )
    expect(res.status).toBe(429)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('rate_limited')
  })

  // -------------------------------------------------------------------------
  // Google integration unavailable
  // -------------------------------------------------------------------------

  it('returns 503 calendar_unavailable when no Google integration is connected', async () => {
    integrationStub = null
    const res = await POST(
      buildContext({
        body: validBody(),
      })
    )
    expect(res.status).toBe(503)
    const json = await parseJson<{
      error: string
      fallback: { type: string }
    }>(res)
    expect(json.error).toBe('calendar_unavailable')
    expect(json.fallback.type).toBe('email')
  })

  it('returns 503 calendar_unavailable when access token refresh fails', async () => {
    accessTokenStub = null
    const res = await POST(
      buildContext({
        body: validBody(),
      })
    )
    expect(res.status).toBe(503)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('calendar_unavailable')
  })

  // -------------------------------------------------------------------------
  // Happy path
  // -------------------------------------------------------------------------

  it('commits assessment + meeting + sidecar rows and returns 201 with tokens', async () => {
    const res = await POST(
      buildContext({
        body: validBody(),
      })
    )

    expect(res.status).toBe(201)
    const json = await parseJson<{
      ok: boolean
      assessment_id: string
      meeting_id: string
      schedule_id: string
      meeting_schedule_id: string
      manage_url: string
      meet_url: string
    }>(res)
    expect(json.ok).toBe(true)
    expect(json.assessment_id).toBe(json.meeting_id) // dual-write invariant
    expect(json.schedule_id).toBeTruthy()
    expect(json.meeting_schedule_id).toBeTruthy()
    expect(json.meet_url).toBe(BOOKING_CONFIG.meeting_url)
    expect(json.manage_url).toContain('/book/manage?token=')

    // Verify D1 state
    const assessment = await db
      .prepare('SELECT id, scheduled_at FROM assessments WHERE id = ?')
      .bind(json.assessment_id)
      .first<{ id: string; scheduled_at: string }>()
    expect(assessment).not.toBeNull()
    expect(assessment!.scheduled_at).toBeTruthy()

    const meeting = await db
      .prepare('SELECT id FROM meetings WHERE id = ?')
      .bind(json.meeting_id)
      .first<{ id: string }>()
    expect(meeting).not.toBeNull()

    const schedule = await db
      .prepare(
        'SELECT id, google_event_id, manage_token_hash FROM assessment_schedule WHERE id = ?'
      )
      .bind(json.schedule_id)
      .first<{ id: string; google_event_id: string; manage_token_hash: string }>()
    expect(schedule).not.toBeNull()
    expect(schedule!.google_event_id).toBe('gcal-event-test-001')
    expect(schedule!.manage_token_hash).toBeTruthy()

    const meetingSchedule = await db
      .prepare('SELECT google_event_id FROM meeting_schedule WHERE id = ?')
      .bind(json.meeting_schedule_id)
      .first<{ google_event_id: string }>()
    expect(meetingSchedule!.google_event_id).toBe('gcal-event-test-001')

    // Hold released after success
    const holds = await db.prepare('SELECT id FROM booking_holds').all()
    expect(holds.results).toHaveLength(0)
  })

  it('promotes the entity to the meetings stage on success', async () => {
    const res = await POST(buildContext({ body: validBody() }))
    expect(res.status).toBe(201)
    const json = await parseJson<{ ok: boolean }>(res)
    expect(json.ok).toBe(true)

    // Find the entity row keyed off the assessment we just committed —
    // slug derivation isn't part of this handler's contract, so we don't
    // assert against it directly.
    const entity = await db
      .prepare(
        `SELECT e.stage
         FROM entities e
         INNER JOIN assessments a ON a.entity_id = e.id
         WHERE a.org_id = ?`
      )
      .bind(ORG_ID)
      .first<{ stage: string }>()
    expect(entity?.stage).toBe('meetings')
  })

  // -------------------------------------------------------------------------
  // Google sync failure → rollback
  // -------------------------------------------------------------------------

  it('rolls back DB writes when Google Calendar event creation fails', async () => {
    googleEventResult = new Error('Google Calendar API 503: backend unavailable')
    // The handler logs this failure via console.error before rolling back —
    // suppress the expected log so test output stays clean.
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const res = await POST(
      buildContext({
        body: validBody(),
      })
    )

    errSpy.mockRestore()

    expect(res.status).toBe(503)
    const json = await parseJson<{ error: string; fallback: { email: string } }>(res)
    expect(json.error).toBe('calendar_sync_failed')
    expect(json.fallback.email).toBe('team@smd.services')

    // No assessment row should remain
    const assessments = await db.prepare('SELECT id FROM assessments').all()
    expect(assessments.results).toHaveLength(0)

    // No meeting row
    const meetings = await db.prepare('SELECT id FROM meetings').all()
    expect(meetings.results).toHaveLength(0)

    // No sidecar rows
    const schedule = await db.prepare('SELECT id FROM assessment_schedule').all()
    expect(schedule.results).toHaveLength(0)

    const meetingSchedule = await db.prepare('SELECT id FROM meeting_schedule').all()
    expect(meetingSchedule.results).toHaveLength(0)

    // No leftover hold
    const holds = await db.prepare('SELECT id FROM booking_holds').all()
    expect(holds.results).toHaveLength(0)
  })
})
