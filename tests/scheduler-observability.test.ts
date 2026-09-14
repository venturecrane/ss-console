/**
 * WP-1 integration coverage for scheduler observability (work-liveness).
 *
 * Three real-D1 concerns the pure worker/roster unit tests can't reach:
 *   1. Migration 0093 — the fleet_status re-key and fleet_alert_state CHECK
 *      widening preserve live rows and accept exactly the four conditions.
 *   2. Ingest (src/pages/api/internal/heartbeat.ts) — first-ever coverage:
 *      the re-key regression (two slugs on one entity => two rows), scheduler
 *      field storage/coercion/junk/absent-overwrite, freshness derivation.
 *   3. Worker runOnce against the migrated schema — markOpen accepts the two
 *      new conditions, and stale_holds surfaces the orphan case.
 */

import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { seedMachineCredential } from './helpers/machine-credential'
import path from 'node:path'
import { POST } from '../src/pages/api/internal/heartbeat'
import { runOnce, type Env as WorkerEnv } from '../workers/fleet-alerts/src/index'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'

const ORG_ID = 'org-sched'
const ENTITY_A = 'ent-sched-a'

function allMigrations(): string[] {
  return discoverNumericMigrations(migrationsDir)
}
function migration0093(): string[] {
  return allMigrations().filter((f) => f.includes('0093_scheduler_observability'))
}
function before0093(): string[] {
  return allMigrations().filter((f) => !f.includes('0093_scheduler_observability'))
}

async function seedOrg(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Sched Org', 'sched-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
}

async function seedEntity(db: D1Database, entityId: string, slug: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(entityId, ORG_ID, slug, slug)
    .run()
}

async function seedConfig(db: D1Database, entityId: string, slug: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-07-24T00:00:00Z')`
    )
    .bind(entityId, ORG_ID, slug)
    .run()
}

// ===========================================================================
// 1. Migration 0093
// ===========================================================================

describe('migration 0093 — fleet_status re-key + fleet_alert_state CHECK widen', () => {
  beforeAll(() => {
    expect(migration0093()).toHaveLength(1)
  })

  it('preserves fleet_status and fleet_alert_state rows across the rebuild', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: before0093() })
    await seedOrg(db)
    await seedEntity(db, ENTITY_A, 'alpha')

    // Pre-0093 fleet_status shape (entity_id PK, no scheduler columns).
    await db
      .prepare(
        `INSERT INTO fleet_status
           (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, sticky_stop_level, updated_at)
         VALUES (?, 'alpha', '2026-07-04T11:59:00Z', 'green', 'OK', datetime('now'))`
      )
      .bind(ENTITY_A)
      .run()
    await db
      .prepare(
        `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
         VALUES ('alpha', 'heartbeat_red', 'open', datetime('now')),
                ('beta', 'hard_stop', 'resolved', datetime('now'))`
      )
      .run()

    await runMigrations(db, { files: migration0093() })

    const fs = await db
      .prepare('SELECT * FROM fleet_status WHERE customer_slug = ?')
      .bind('alpha')
      .first<Record<string, unknown>>()
    expect(fs?.entity_id).toBe(ENTITY_A)
    expect(fs?.heartbeat_status).toBe('green')
    expect(fs?.sticky_stop_level).toBe('OK')
    // New columns exist and default NULL for copied rows.
    expect(fs?.scheduler_ok).toBeNull()
    expect(fs?.scheduler_job_count).toBeNull()
    expect(fs?.scheduler_max_overdue_seconds).toBeNull()

    const alerts = await db
      .prepare(
        'SELECT customer_slug, condition, status FROM fleet_alert_state ORDER BY customer_slug'
      )
      .all<{ customer_slug: string; condition: string; status: string }>()
    expect(alerts.results).toHaveLength(2)
    expect(alerts.results?.[0]).toMatchObject({
      customer_slug: 'alpha',
      condition: 'heartbeat_red',
    })
    expect(alerts.results?.[1]).toMatchObject({ customer_slug: 'beta', condition: 'hard_stop' })
  })

  it('the widened CHECK accepts all four conditions and rejects a fifth', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })

    for (const condition of ['heartbeat_red', 'hard_stop', 'scheduler_error', 'work_overdue']) {
      await db
        .prepare(
          `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
           VALUES (?, ?, 'open', datetime('now'))`
        )
        .bind(`c-${condition}`, condition)
        .run()
    }
    const count = await db
      .prepare('SELECT COUNT(*) AS n FROM fleet_alert_state')
      .first<{ n: number }>()
    expect(count?.n).toBe(4)

    await expect(
      db
        .prepare(
          `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
           VALUES ('x', 'bogus_condition', 'open', datetime('now'))`
        )
        .run()
    ).rejects.toThrow()
  })
})

// ===========================================================================
// 2. Ingest — POST /api/internal/heartbeat
// ===========================================================================

function heartbeatRequest(
  slug: string,
  body: Record<string, unknown>,
  key: string = MACHINE_KEY
): Parameters<typeof POST>[0] {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${key}`,
      'X-Tenant-Slug': slug,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0]
}

async function readFleet(db: D1Database, slug: string): Promise<Record<string, unknown> | null> {
  return db
    .prepare('SELECT * FROM fleet_status WHERE customer_slug = ?')
    .bind(slug)
    .first<Record<string, unknown>>()
}

describe('POST /api/internal/heartbeat — scheduler fields + re-key', () => {
  beforeEach(async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrg(db)
    // Two slugs share ONE entity — the multi-operator model that motivated the
    // re-key. Both resolve to ENTITY_A via customer_configs.
    await seedEntity(db, ENTITY_A, 'alpha')
    await seedConfig(db, ENTITY_A, 'alpha')
    await seedConfig(db, ENTITY_A, 'beta')
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, 'alpha', MACHINE_KEY)
    await seedMachineCredential(db, 'beta', MACHINE_KEY)
  })

  it('two slugs sharing one entity_id produce TWO rows (re-key regression)', async () => {
    const db = (testEnv as unknown as { DB: D1Database }).DB
    const now = new Date().toISOString()
    expect((await POST(heartbeatRequest('alpha', { heartbeat_ts: now }))).status).toBe(200)
    expect((await POST(heartbeatRequest('beta', { heartbeat_ts: now }))).status).toBe(200)

    const rows = await db
      .prepare('SELECT customer_slug, entity_id FROM fleet_status ORDER BY customer_slug')
      .all<{ customer_slug: string; entity_id: string }>()
    expect(rows.results).toHaveLength(2)
    expect(rows.results?.every((r) => r.entity_id === ENTITY_A)).toBe(true)
    expect(rows.results?.map((r) => r.customer_slug)).toEqual(['alpha', 'beta'])
  })

  it('stores the three scheduler fields', async () => {
    const db = (testEnv as unknown as { DB: D1Database }).DB
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        scheduler_ok: 1,
        scheduler_job_count: 3,
        scheduler_max_overdue_seconds: 120,
      })
    )
    const row = await readFleet(db, 'alpha')
    expect(row?.scheduler_ok).toBe(1)
    expect(row?.scheduler_job_count).toBe(3)
    expect(row?.scheduler_max_overdue_seconds).toBe(120)
  })

  it('coerces a boolean scheduler_ok to 1/0', async () => {
    const db = (testEnv as unknown as { DB: D1Database }).DB
    await POST(
      heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString(), scheduler_ok: true })
    )
    expect((await readFleet(db, 'alpha'))?.scheduler_ok).toBe(1)
    await POST(
      heartbeatRequest('beta', { heartbeat_ts: new Date().toISOString(), scheduler_ok: false })
    )
    expect((await readFleet(db, 'beta'))?.scheduler_ok).toBe(0)
  })

  it('stores NULL for junk scheduler values (never guesses a verdict)', async () => {
    const db = (testEnv as unknown as { DB: D1Database }).DB
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        scheduler_ok: 'yes',
        scheduler_job_count: -1,
        scheduler_max_overdue_seconds: 1.5,
      })
    )
    const row = await readFleet(db, 'alpha')
    expect(row?.scheduler_ok).toBeNull()
    expect(row?.scheduler_job_count).toBeNull()
    expect(row?.scheduler_max_overdue_seconds).toBeNull()
  })

  it('absent scheduler fields OVERWRITE prior values back to NULL (no COALESCE pin)', async () => {
    const db = (testEnv as unknown as { DB: D1Database }).DB
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        scheduler_ok: 0,
        scheduler_job_count: 5,
        scheduler_max_overdue_seconds: 999,
      })
    )
    expect((await readFleet(db, 'alpha'))?.scheduler_ok).toBe(0)

    // A later beat that omits the fields (e.g. overlay rolled back) must clear
    // them — a pinned scheduler_ok=0 would page forever.
    await POST(heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString() }))
    const row = await readFleet(db, 'alpha')
    expect(row?.scheduler_ok).toBeNull()
    expect(row?.scheduler_job_count).toBeNull()
    expect(row?.scheduler_max_overdue_seconds).toBeNull()
  })

  it('leaves heartbeat_status freshness derivation unchanged (fresh ts => green)', async () => {
    const res = await POST(heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString() }))
    const body = await res.json<{ ok: boolean; heartbeat_status: string }>()
    expect(body.ok).toBe(true)
    expect(body.heartbeat_status).toBe('green')
  })
})

// ===========================================================================
// 3. Worker runOnce against the migrated schema
// ===========================================================================

async function seedFleetRow(
  db: D1Database,
  slug: string,
  opts: {
    last_heartbeat_ts?: string
    sticky_stop_level?: string
    scheduler_ok?: number | null
    scheduler_max_overdue_seconds?: number | null
  }
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO fleet_status
         (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, sticky_stop_level,
          scheduler_ok, scheduler_max_overdue_seconds, updated_at)
       VALUES (?, ?, ?, 'green', ?, ?, ?, datetime('now'))`
    )
    .bind(
      ENTITY_A,
      slug,
      opts.last_heartbeat_ts ?? null,
      opts.sticky_stop_level ?? 'OK',
      opts.scheduler_ok ?? null,
      opts.scheduler_max_overdue_seconds ?? null
    )
    .run()
}

describe('worker runOnce against the rebuilt schema', () => {
  const FRESH = '2026-07-24T12:00:00.000Z'
  const NOW = Date.parse('2026-07-24T12:00:30.000Z') // 30s after FRESH → heartbeat green

  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrg(db)
    await seedEntity(db, ENTITY_A, 'shared')
    const okMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: 'rk-1' }), { status: 200 }))
    vi.stubGlobal('fetch', okMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function workerEnv(): WorkerEnv {
    return { DB: db, RESEND_API_KEY: 'rk_test', WORK_OVERDUE_RED_SECONDS: '900' }
  }

  it('markOpen writes a scheduler_error row (CHECK accepts it, insert succeeds)', async () => {
    await seedFleetRow(db, 'sched', { last_heartbeat_ts: FRESH, scheduler_ok: 0 })
    const summary = await runOnce(workerEnv(), NOW)
    expect(summary.transitions).toEqual([
      expect.objectContaining({
        customer_slug: 'sched',
        condition: 'scheduler_error',
        kind: 'opened',
      }),
    ])
    const alert = await db
      .prepare('SELECT status FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?')
      .bind('sched', 'scheduler_error')
      .first<{ status: string }>()
    expect(alert?.status).toBe('open')
  })

  it('markOpen writes a work_overdue row against the rebuilt schema', async () => {
    await seedFleetRow(db, 'overdue', {
      last_heartbeat_ts: FRESH,
      scheduler_ok: 1,
      scheduler_max_overdue_seconds: 1000,
    })
    const summary = await runOnce(workerEnv(), NOW)
    expect(summary.transitions).toEqual([
      expect.objectContaining({
        customer_slug: 'overdue',
        condition: 'work_overdue',
        kind: 'opened',
      }),
    ])
    const alert = await db
      .prepare('SELECT status FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?')
      .bind('overdue', 'work_overdue')
      .first<{ status: string }>()
    expect(alert?.status).toBe('open')
  })

  it('stale_holds surfaces an open alert whose seat has no fleet_status row', async () => {
    // The live pilot-smokeball orphan: an open row with no fleet_status row.
    await db
      .prepare(
        `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
         VALUES ('pilot-smokeball', 'heartbeat_red', 'open', datetime('now'))`
      )
      .run()
    const summary = await runOnce(workerEnv(), NOW)
    expect(summary.stale_holds).toEqual([
      { customer_slug: 'pilot-smokeball', condition: 'heartbeat_red' },
    ])
  })
})
