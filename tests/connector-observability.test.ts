/**
 * ADR 0080 integration coverage for connector observability (ss#1990).
 *
 * Real-D1 concerns the pure worker unit tests can't reach:
 *   1. Migration 0094 — the fleet_alert_state CHECK rebuild accepts the
 *      connector_check_error literal and the connector_down:<server> prefix,
 *      rejects junk, and preserves pre-existing open rows byte-identically
 *      (a mangled copy would manufacture a false RECOVERED on deploy).
 *   2. Ingest — connectors map three-tier parse (store / drop-entry /
 *      whole-map NULL) + connector_check_ok coercion + absent-overwrite.
 *   3. Worker runOnce — a connector_down:<server> alert opens against the
 *      rebuilt schema, resolves ONLY on a proven success, and holds (with a
 *      stale_holds surface) when the server key disappears from the map.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { readFileSync } from 'node:fs'
import { POST } from '../src/pages/api/internal/heartbeat'
import { runOnce, type Env as WorkerEnv } from '../workers/fleet-alerts/src/index'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'

const ORG_ID = 'org-conn'
const ENTITY_A = 'ent-conn-a'

function allMigrations(): string[] {
  return discoverNumericMigrations(migrationsDir)
}
function before0094(): string[] {
  return allMigrations().filter((f) => !f.includes('0094_connector_observability'))
}
function migration0094(): string[] {
  return allMigrations().filter((f) => f.includes('0094_connector_observability'))
}

async function seedOrgEntityConfig(db: D1Database, slug: string): Promise<void> {
  await db
    .prepare(
      `INSERT OR IGNORE INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Conn Org', 'conn-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT OR IGNORE INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'conn-entity', 'conn-entity', 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_A, ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-07-25T00:00:00Z')`
    )
    .bind(ENTITY_A, ORG_ID, slug)
    .run()
}

// ===========================================================================
// 1. Migration 0094
// ===========================================================================

describe('migration 0094 — connector columns + CHECK widening', () => {
  it('preserves a pre-existing open alert row byte-identically across the rebuild', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: before0094() })
    await db
      .prepare(
        `INSERT INTO fleet_alert_state
           (customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
         VALUES ('pilot-smokeball', 'scheduler_error', 'open',
                 '2026-07-25T01:37:00Z', NULL, 'resend-abc', '2026-07-25T01:37:00Z')`
      )
      .run()
    await runMigrations(db, { files: migration0094() })
    const row = await db
      .prepare(`SELECT * FROM fleet_alert_state WHERE customer_slug = 'pilot-smokeball'`)
      .first<Record<string, unknown>>()
    expect(row).toEqual({
      customer_slug: 'pilot-smokeball',
      condition: 'scheduler_error',
      status: 'open',
      opened_at: '2026-07-25T01:37:00Z',
      resolved_at: null,
      last_alert_id: 'resend-abc',
      updated_at: '2026-07-25T01:37:00Z',
    })
  })

  it('CHECK accepts the new literal + the connector_down prefix, rejects junk', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    const insert = (condition: string) =>
      db
        .prepare(
          `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
           VALUES ('seat', ?, 'open', datetime('now'))`
        )
        .bind(condition)
        .run()
    await expect(insert('connector_check_error')).resolves.toBeTruthy()
    await expect(insert('connector_down:smokeball')).resolves.toBeTruthy()
    await expect(insert('connector_down:msgraph_mail')).resolves.toBeTruthy()
    await expect(insert('junk_condition')).rejects.toThrow()
    // The prefix requires a colon — a bare 'connector_down' is not a condition.
    await expect(insert('connector_down')).rejects.toThrow()
  })

  it('fleet_status gains the two nullable connector columns', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'colcheck')
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, heartbeat_status, connectors_json, connector_check_ok)
         VALUES (?, 'colcheck', 'green', '{}', 1)`
      )
      .bind(ENTITY_A)
      .run()
    const row = await db
      .prepare(
        `SELECT connectors_json, connector_check_ok FROM fleet_status WHERE customer_slug = 'colcheck'`
      )
      .first<Record<string, unknown>>()
    expect(row).toEqual({ connectors_json: '{}', connector_check_ok: 1 })
  })
})

// ===========================================================================
// 2. Ingest — connectors map three-tier parse
// ===========================================================================

function heartbeatRequest(slug: string, body: Record<string, unknown>): Parameters<typeof POST>[0] {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': slug,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0]
}

async function readFleet(db: D1Database, slug: string): Promise<Record<string, unknown> | null> {
  return db
    .prepare('SELECT connectors_json, connector_check_ok FROM fleet_status WHERE customer_slug = ?')
    .bind(slug)
    .first<Record<string, unknown>>()
}

describe('POST /api/internal/heartbeat — connector fields (ADR 0080)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'alpha')
    ;(testEnv as unknown as Record<string, unknown>).DB = db
    ;(testEnv as unknown as Record<string, unknown>).MACHINE_HEARTBEAT_KEY = MACHINE_KEY
  })

  it('stores a valid connectors map and connector_check_ok', async () => {
    const res = await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connector_check_ok: true,
        connectors: {
          smokeball: {
            consecutive_failures: 4,
            run_age_seconds: 400,
            conn_evidence: true,
            last_ok_age_seconds: 900,
            last_error_message: 'Smokeball GET /matters -> HTTP 401: (empty body)',
          },
          agentmail: { consecutive_failures: 0, last_ok_age_seconds: 30 },
        },
      })
    )
    expect(res.status).toBe(200)
    const row = await readFleet(db, 'alpha')
    expect(row?.connector_check_ok).toBe(1)
    const map = JSON.parse(String(row?.connectors_json))
    expect(map.smokeball.consecutive_failures).toBe(4)
    expect(map.smokeball.conn_evidence).toBe(true)
    expect(map.agentmail.consecutive_failures).toBe(0)
  })

  it('drops an invalid entry but keeps valid siblings (tier 2)', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connectors: {
          good: { consecutive_failures: 0 },
          bad_count: { consecutive_failures: 'seven' },
          // A failure run without its writer-side age can satisfy no
          // age-gated condition — entry dropped, not coerced.
          ageless_run: { consecutive_failures: 5 },
          'bad name!': { consecutive_failures: 0 },
        },
      })
    )
    const map = JSON.parse(String((await readFleet(db, 'alpha'))?.connectors_json))
    expect(Object.keys(map)).toEqual(['good'])
  })

  it('structurally-invalid map stores NULL (tier 3: trust nothing, hold everything)', async () => {
    for (const junk of [['array'], 'string', 42]) {
      await POST(
        heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString(), connectors: junk })
      )
      expect((await readFleet(db, 'alpha'))?.connectors_json).toBeNull()
    }
  })

  it('absent fields overwrite back to NULL (never COALESCE-pinned)', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connector_check_ok: 0,
        connectors: { smokeball: { consecutive_failures: 0 } },
      })
    )
    await POST(heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString() }))
    const row = await readFleet(db, 'alpha')
    expect(row?.connectors_json).toBeNull()
    expect(row?.connector_check_ok).toBeNull()
  })

  it('truncates an oversized last_error_message at the ingest boundary', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connectors: {
          smokeball: {
            consecutive_failures: 1,
            run_age_seconds: 10,
            last_error_message: 'x'.repeat(5000),
          },
        },
      })
    )
    const map = JSON.parse(String((await readFleet(db, 'alpha'))?.connectors_json))
    expect(map.smokeball.last_error_message.length).toBe(200)
  })
})

// ===========================================================================
// 3. Worker runOnce — open / resolve / hold end-to-end
// ===========================================================================

describe('worker runOnce — connector_down lifecycle', () => {
  const FRESH = '2026-07-25T12:00:00.000Z'
  const NOW = Date.parse('2026-07-25T12:00:30.000Z')

  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'seat')
    // Fresh Response per call — a single mocked Response's body is one-shot,
    // and the lifecycle tests send more than one email.
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response(JSON.stringify({ id: 'rk-1' }), { status: 200 }))
        )
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function workerEnv(): WorkerEnv {
    return { DB: db, RESEND_API_KEY: 'rk_test', CONNECTOR_DOWN_RUN_AGE_SECONDS: '300' }
  }

  async function setConnectors(json: string | null): Promise<void> {
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, connectors_json)
         VALUES (?, 'seat', ?, 'green', ?)
         ON CONFLICT(customer_slug) DO UPDATE SET connectors_json = excluded.connectors_json`
      )
      .bind(ENTITY_A, FRESH, json)
      .run()
  }

  it('opens on a sustained conn-class run, resolves ONLY on proven success', async () => {
    await setConnectors(
      JSON.stringify({
        smokeball: { consecutive_failures: 4, run_age_seconds: 400, conn_evidence: true },
      })
    )
    const opened = await runOnce(workerEnv(), NOW)
    expect(opened.transitions).toEqual([
      expect.objectContaining({
        customer_slug: 'seat',
        condition: 'connector_down:smokeball',
        kind: 'opened',
      }),
    ])

    // Ambiguous count (failing again, not yet proven down) → HOLD, no transition.
    await setConnectors(
      JSON.stringify({ smokeball: { consecutive_failures: 1, run_age_seconds: 5 } })
    )
    const held = await runOnce(workerEnv(), NOW)
    expect(held.transitions).toEqual([])

    // Proven success → resolve.
    await setConnectors(JSON.stringify({ smokeball: { consecutive_failures: 0 } }))
    const resolved = await runOnce(workerEnv(), NOW)
    expect(resolved.transitions).toEqual([
      expect.objectContaining({
        customer_slug: 'seat',
        condition: 'connector_down:smokeball',
        kind: 'resolved',
      }),
    ])
  })

  it('an open alert whose server key vanished from the map is a stale hold, not a resolve', async () => {
    await setConnectors(
      JSON.stringify({
        smokeball: { consecutive_failures: 4, run_age_seconds: 400, conn_evidence: true },
      })
    )
    await runOnce(workerEnv(), NOW)

    // tmpfs wipe on restart: map is empty; the alert must HOLD and surface.
    await setConnectors('{}')
    const summary = await runOnce(workerEnv(), NOW)
    expect(summary.transitions).toEqual([])
    expect(summary.stale_holds).toEqual([
      { customer_slug: 'seat', condition: 'connector_down:smokeball' },
    ])
  })

  it('connector_check_error opens and resolves on the reported boolean', async () => {
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, connector_check_ok)
         VALUES (?, 'seat', ?, 'green', 0)`
      )
      .bind(ENTITY_A, FRESH)
      .run()
    const opened = await runOnce(workerEnv(), NOW)
    expect(opened.transitions).toEqual([
      expect.objectContaining({ condition: 'connector_check_error', kind: 'opened' }),
    ])
    await db
      .prepare(`UPDATE fleet_status SET connector_check_ok = 1 WHERE customer_slug = 'seat'`)
      .run()
    const resolved = await runOnce(workerEnv(), NOW)
    expect(resolved.transitions).toEqual([
      expect.objectContaining({ condition: 'connector_check_error', kind: 'resolved' }),
    ])
  })
})

// ===========================================================================
// 4. Migration 0103 + token-age ingest + connector_token_expiring lifecycle
//    (ss#2148, ADR 0080 amendment 2026-08-09)
// ===========================================================================

describe('migration 0103 — token-age column + CHECK widening', () => {
  it('preserves a pre-existing open alert row across the rebuild and accepts the new prefix', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    const insert = (condition: string) =>
      db
        .prepare(
          `INSERT INTO fleet_alert_state (customer_slug, condition, status, opened_at)
           VALUES ('seat', ?, 'open', datetime('now'))`
        )
        .bind(condition)
        .run()
    await expect(insert('connector_token_expiring:smokeball')).resolves.toBeTruthy()
    await expect(insert('connector_down:smokeball')).resolves.toBeTruthy()
    await expect(insert('junk_condition')).rejects.toThrow()
    await expect(insert('connector_token_expiring')).rejects.toThrow() // prefix needs a colon
  })

  it('fleet_status gains the nullable connector_token_age_json column', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'tokcol')
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, heartbeat_status, connector_token_age_json)
         VALUES (?, 'tokcol', 'green', '{"smokeball":86400}')`
      )
      .bind(ENTITY_A)
      .run()
    const row = await db
      .prepare(`SELECT connector_token_age_json FROM fleet_status WHERE customer_slug = 'tokcol'`)
      .first<Record<string, unknown>>()
    expect(row?.connector_token_age_json).toBe('{"smokeball":86400}')
  })
})

describe('POST /api/internal/heartbeat — connector_token_age (ss#2148)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'alpha')
    ;(testEnv as unknown as Record<string, unknown>).DB = db
    ;(testEnv as unknown as Record<string, unknown>).MACHINE_HEARTBEAT_KEY = MACHINE_KEY
  })

  async function readTokenAge(slug: string): Promise<unknown> {
    const row = await db
      .prepare('SELECT connector_token_age_json FROM fleet_status WHERE customer_slug = ?')
      .bind(slug)
      .first<Record<string, unknown>>()
    return row?.connector_token_age_json
  }

  it('stores a valid token-age map, dropping junk entries', async () => {
    const res = await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connector_token_age: {
          smokeball: 2160000,
          'bad name!': 5,
          negative: -1,
          stringy: 'old',
        },
      })
    )
    expect(res.status).toBe(200)
    expect(JSON.parse(String(await readTokenAge('alpha')))).toEqual({ smokeball: 2160000 })
  })

  it('structurally-invalid map stores NULL and absence overwrites back to NULL', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connector_token_age: { smokeball: 100 },
      })
    )
    expect(await readTokenAge('alpha')).not.toBeNull()
    await POST(heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString() }))
    expect(await readTokenAge('alpha')).toBeNull()
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        connector_token_age: 'junk',
      })
    )
    expect(await readTokenAge('alpha')).toBeNull()
  })
})

describe('worker runOnce — connector_token_expiring lifecycle', () => {
  const FRESH = '2026-07-25T12:00:00.000Z'
  const NOW = Date.parse('2026-07-25T12:00:30.000Z')

  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'seat')
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response(JSON.stringify({ id: 'rk-1' }), { status: 200 }))
        )
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function workerEnv(): WorkerEnv {
    return {
      DB: db,
      RESEND_API_KEY: 'rk_test',
      SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS: '30',
      TOKEN_EXPIRY_WARN_DAYS: '5',
    }
  }

  async function setTokenAge(json: string | null): Promise<void> {
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, connector_token_age_json)
         VALUES (?, 'seat', ?, 'green', ?)
         ON CONFLICT(customer_slug) DO UPDATE SET connector_token_age_json = excluded.connector_token_age_json`
      )
      .bind(ENTITY_A, FRESH, json)
      .run()
  }

  it('opens at the warn horizon and resolves when the token file is rewritten', async () => {
    await setTokenAge(JSON.stringify({ smokeball: 26 * 86400 }))
    const opened = await runOnce(workerEnv(), NOW)
    expect(opened.transitions).toEqual([
      expect.objectContaining({ condition: 'connector_token_expiring:smokeball', kind: 'opened' }),
    ])
    // Rotation resets the file mtime → a small age → the condition resolves.
    await setTokenAge(JSON.stringify({ smokeball: 60 }))
    const resolved = await runOnce(workerEnv(), NOW)
    expect(resolved.transitions).toEqual([
      expect.objectContaining({
        condition: 'connector_token_expiring:smokeball',
        kind: 'resolved',
      }),
    ])
  })

  it('a seat that stops reporting ages HOLDS the open alert (stale hold, never a false resolve)', async () => {
    await setTokenAge(JSON.stringify({ smokeball: 26 * 86400 }))
    await runOnce(workerEnv(), NOW)
    await setTokenAge(null)
    const held = await runOnce(workerEnv(), NOW)
    expect(held.transitions).toEqual([])
    expect(held.stale_holds).toEqual([
      expect.objectContaining({ condition: 'connector_token_expiring:smokeball' }),
    ])
  })
})

// ---------------------------------------------------------------------------
// 4b. The vendor lifetime cutover (2026-09-02 incident).
//
// Smokeball raised refresh-token validity 30d -> 180d effective 2026-08-24,
// keyed to the token's ISSUE date. Both regimes were live at once: ashton-price
// held a pre-cutover token while pilot-smokeball was about to receive a
// post-cutover one, so a single global lifetime constant is wrong for one of
// them whichever value it takes.
//
// These two tests are the falsifiers the original design never had. Each fails
// loudly if effectiveLifetimeDays() is removed and the configured 180 is applied
// flat, and they fail in OPPOSITE directions — which is the point, because the
// one-constant bug is silent in both.
// ---------------------------------------------------------------------------

describe('worker runOnce — Smokeball 2026-08-24 lifetime cutover', () => {
  const NOW = Date.parse('2026-09-02T15:00:00.000Z')
  const FRESH = '2026-09-02T14:59:30.000Z'

  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'seat')
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response(JSON.stringify({ id: 'rk-1' }), { status: 200 }))
        )
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  // The live config after the cutover: 180 is the CURRENT vendor lifetime.
  function workerEnv(): WorkerEnv {
    return {
      DB: db,
      RESEND_API_KEY: 'rk_test',
      SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS: '180',
      TOKEN_EXPIRY_WARN_DAYS: '5',
    }
  }

  async function setTokenAge(ageSeconds: number): Promise<void> {
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, connector_token_age_json)
         VALUES (?, 'seat', ?, 'green', ?)
         ON CONFLICT(customer_slug) DO UPDATE SET connector_token_age_json = excluded.connector_token_age_json`
      )
      .bind(ENTITY_A, FRESH, JSON.stringify({ smokeball: ageSeconds }))
      .run()
  }

  // Only this condition is under test; a far-future `now` also ages the
  // heartbeat, and those transitions are not what these cases assert about.
  function tokenTransitions(result: { transitions: Array<{ condition: string }> }) {
    return result.transitions.filter(
      (t) => t.condition === 'connector_token_expiring:smokeball'
    ) as Array<{ condition: string; kind?: string; detail?: string }>
  }

  it('a PRE-cutover token still warns at its real 30d horizon (the ashton-price case)', async () => {
    // Issued 2026-08-07, i.e. before the 08-24 cutover, so it genuinely dies at
    // 30 days. At 26 days old it is inside the 25-day warn horizon and MUST
    // page. Judged flat against 180 it would stay silent all the way to death.
    await setTokenAge(26 * 86400)
    const opened = tokenTransitions(await runOnce(workerEnv(), NOW))
    expect(opened).toHaveLength(1)
    expect(opened[0]?.kind).toBe('opened')
    expect(opened[0]?.detail).toContain('recorded lifetime 30d')
    expect(opened[0]?.detail).toContain('pre-cutover token')
  })

  it('a POST-cutover token stays quiet at 26d instead of latching open for ~155d', async () => {
    // Same age, but `now` is far enough past the cutover that issued_at lands
    // after 2026-08-24, so it lives 180 days and 26 days is nowhere near its
    // horizon. Judged flat against 30 it would open here, page once, then sit
    // open and SILENT through the real expiry — the edge-triggered failure that
    // made the 2026-08-27 warning useless.
    await setTokenAge(26 * 86400)
    const quiet = tokenTransitions(await runOnce(workerEnv(), Date.parse('2026-10-01T15:00:00Z')))
    expect(quiet).toEqual([])
  })

  it('a post-cutover token DOES warn once it reaches the 175d horizon', async () => {
    await setTokenAge(176 * 86400)
    const opened = tokenTransitions(await runOnce(workerEnv(), Date.parse('2027-03-01T15:00:00Z')))
    expect(opened).toHaveLength(1)
    expect(opened[0]?.kind).toBe('opened')
    expect(opened[0]?.detail).toContain('recorded lifetime 180d')
    expect(opened[0]?.detail).not.toContain('pre-cutover token')
  })

  // The cases above supply their own env, so none of them can see the DEPLOYED
  // constant drift. That is not hypothetical: the deployed value said 30 for
  // three weeks while the vendor's current lifetime was 180, and no test failed.
  it('wrangler.toml declares the CURRENT vendor lifetime, not the pre-cutover one', () => {
    const source = readFileSync(
      path.resolve(process.cwd(), 'workers/fleet-alerts/wrangler.toml'),
      'utf-8'
    )
    expect(source).toMatch(/^SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS = "180"$/m)
    // A bare 30 here would silence the pre-expiry warning for every
    // post-cutover token until ~5 days before death, and latch it open and
    // silent for months. src/token-expiry.ts owns the pre-cutover case.
    expect(source).not.toMatch(/^SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS = "30"$/m)
  })
})
