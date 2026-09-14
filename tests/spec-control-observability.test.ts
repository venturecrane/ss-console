/**
 * Integration coverage for authored-spec control alerting (ss#2234).
 *
 * The incident this closes (ss#2228): `pilot-smokeball` declared
 * `output_classes.staff.voice_spec: expected`, the staff spec was never
 * installed, and every autonomous staff send refused for six days. The gate
 * wrote an audit row each time. Nobody reads audit rows, so the firm's mail
 * stopped and nothing said so.
 *
 * Real-D1 concerns the pure worker unit tests cannot reach:
 *   1. Migration 0104 — the fleet_alert_state CHECK rebuild accepts the new
 *      literal and the spec_control_broken:<class>.<prop> prefix, rejects junk,
 *      and preserves pre-existing open rows byte-identically (a mangled copy
 *      would manufacture a false RECOVERED on deploy).
 *   2. Ingest — the spec_control map parse (store / drop-entry / whole-map
 *      NULL) and the absent-overwrites-to-NULL contract.
 *   3. Worker runOnce against the rebuilt schema — an alert opens, resolves
 *      when a spec lands, resolves differently when the DECLARATION is
 *      withdrawn, and holds when the seat says it cannot look.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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

const ORG_ID = 'org-spec'
const ENTITY_A = 'ent-spec-a'
const FRESH = new Date().toISOString()

function allMigrations(): string[] {
  return discoverNumericMigrations(migrationsDir)
}
function before0104(): string[] {
  return allMigrations().filter((f) => !f.includes('0104_spec_control_observability'))
}
function migration0104(): string[] {
  return allMigrations().filter((f) => f.includes('0104_spec_control_observability'))
}

async function seedOrgEntityConfig(db: D1Database, slug: string): Promise<void> {
  await db
    .prepare(
      `INSERT OR IGNORE INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Spec Org', 'spec-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT OR IGNORE INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, 'spec-entity', 'spec-entity', 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY_A, ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-08-10T00:00:00Z')`
    )
    .bind(ENTITY_A, ORG_ID, slug)
    .run()
}

// ===========================================================================
// 1. Migration 0104
// ===========================================================================

describe('migration 0104 — spec-control columns + CHECK widening', () => {
  it('preserves a pre-existing open alert row byte-identically across the rebuild', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: before0104() })
    await db
      .prepare(
        `INSERT INTO fleet_alert_state
           (customer_slug, condition, status, opened_at, resolved_at, last_alert_id, updated_at)
         VALUES ('pilot-smokeball', 'connector_down:smokeball', 'open',
                 '2026-08-09T01:37:00Z', NULL, 'resend-abc', '2026-08-09T01:37:00Z')`
      )
      .run()
    await runMigrations(db, { files: migration0104() })
    const row = await db
      .prepare(`SELECT * FROM fleet_alert_state WHERE customer_slug = 'pilot-smokeball'`)
      .first<Record<string, unknown>>()
    expect(row).toEqual({
      customer_slug: 'pilot-smokeball',
      condition: 'connector_down:smokeball',
      status: 'open',
      opened_at: '2026-08-09T01:37:00Z',
      resolved_at: null,
      last_alert_id: 'resend-abc',
      updated_at: '2026-08-09T01:37:00Z',
    })
  })

  it('CHECK accepts the new literal + the spec_control_broken prefix, rejects junk', async () => {
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
    await expect(insert('spec_control_unprovable')).resolves.toBeTruthy()
    await expect(insert('spec_control_broken:staff.voice')).resolves.toBeTruthy()
    await expect(insert('spec_control_broken:outbound_client.format')).resolves.toBeTruthy()
    await expect(insert('junk_condition')).rejects.toThrow()
    // The prefix requires a colon — a bare 'spec_control_broken' is not one.
    await expect(insert('spec_control_broken')).rejects.toThrow()
    // Older vocabularies must still be accepted after the rebuild.
    await expect(insert('connector_token_expiring:smokeball')).resolves.toBeTruthy()
    await expect(insert('heartbeat_red')).resolves.toBeTruthy()
  })

  it('fleet_status gains the two nullable spec-control columns', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'colcheck')
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, heartbeat_status, spec_control_json, spec_control_ok)
         VALUES (?, 'colcheck', 'green', '{}', 1)`
      )
      .bind(ENTITY_A)
      .run()
    const row = await db
      .prepare(
        `SELECT spec_control_json, spec_control_ok FROM fleet_status WHERE customer_slug = 'colcheck'`
      )
      .first<Record<string, unknown>>()
    expect(row).toEqual({ spec_control_json: '{}', spec_control_ok: 1 })
  })
})

// ===========================================================================
// 2. Ingest
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
    .prepare('SELECT spec_control_json, spec_control_ok FROM fleet_status WHERE customer_slug = ?')
    .bind(slug)
    .first<Record<string, unknown>>()
}

describe('POST /api/internal/heartbeat — spec-control fields (ss#2234)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'alpha')
    ;(testEnv as unknown as Record<string, unknown>).DB = db
    await seedMachineCredential(db, 'alpha', MACHINE_KEY)
  })

  it('stores a valid spec_control map and spec_control_ok', async () => {
    const res = await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        spec_control_ok: true,
        spec_control: {
          'staff.voice': { declared: true, installed: false },
          'work_product.voice': { declared: true, installed: true },
        },
      })
    )
    expect(res.status).toBe(200)
    const row = await readFleet(db, 'alpha')
    expect(row?.spec_control_ok).toBe(1)
    const map = JSON.parse(String(row?.spec_control_json))
    expect(map['staff.voice']).toEqual({ declared: true, installed: false })
    expect(map['work_product.voice']).toEqual({ declared: true, installed: true })
  })

  it('drops an invalid entry but keeps valid siblings', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        spec_control: {
          'staff.voice': { declared: true, installed: false },
          // `installed` is what opens and closes the alert — never defaulted.
          'staff.format': { declared: true },
          'staff.voice.extra': { declared: true, installed: true },
          'bad key!': { declared: true, installed: true },
          'staff.tone': { declared: true, installed: true },
        },
      })
    )
    const map = JSON.parse(String((await readFleet(db, 'alpha'))?.spec_control_json))
    expect(Object.keys(map)).toEqual(['staff.voice'])
  })

  it('structurally-invalid map stores NULL (trust nothing, hold everything)', async () => {
    for (const junk of [['array'], 'string', 42]) {
      await POST(
        heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString(), spec_control: junk })
      )
      expect((await readFleet(db, 'alpha'))?.spec_control_json).toBeNull()
    }
  })

  it('absent fields overwrite back to NULL (never COALESCE-pinned)', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        spec_control_ok: true,
        spec_control: { 'staff.voice': { declared: true, installed: false } },
      })
    )
    expect((await readFleet(db, 'alpha'))?.spec_control_json).not.toBeNull()
    // A seat that stops reporting must not leave a stale verdict pinned.
    await POST(heartbeatRequest('alpha', { heartbeat_ts: new Date().toISOString() }))
    const row = await readFleet(db, 'alpha')
    expect(row?.spec_control_json).toBeNull()
    expect(row?.spec_control_ok).toBeNull()
  })

  it('spec_control_ok=false reaches the wire as 0, not as absence', async () => {
    await POST(
      heartbeatRequest('alpha', {
        heartbeat_ts: new Date().toISOString(),
        spec_control_ok: false,
      })
    )
    expect((await readFleet(db, 'alpha'))?.spec_control_ok).toBe(0)
  })
})

// ===========================================================================
// 3. Worker lifecycle against the rebuilt schema
// ===========================================================================

describe('fleet-alerts — spec_control conditions end to end', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: allMigrations() })
    await seedOrgEntityConfig(db, 'seat')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ id: 'resend-1' }), { status: 200 }))
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function workerEnv(): WorkerEnv {
    return { DB: db, RESEND_API_KEY: 'rk_test' }
  }

  async function setSpecControl(json: string | null, ok: number | null = 1): Promise<void> {
    await db
      .prepare(
        `INSERT INTO fleet_status (entity_id, customer_slug, last_heartbeat_ts, heartbeat_status, spec_control_json, spec_control_ok)
         VALUES (?, 'seat', ?, 'green', ?, ?)
         ON CONFLICT(customer_slug) DO UPDATE SET
           spec_control_json = excluded.spec_control_json,
           spec_control_ok   = excluded.spec_control_ok`
      )
      .bind(ENTITY_A, FRESH, json, ok)
      .run()
  }

  async function alertStatus(condition: string): Promise<string | null> {
    const row = await db
      .prepare(
        `SELECT status FROM fleet_alert_state WHERE customer_slug = 'seat' AND condition = ?`
      )
      .bind(condition)
      .first<{ status: string }>()
    return row?.status ?? null
  }

  it('opens on a declared-but-uninstalled spec and resolves when the spec lands', async () => {
    await setSpecControl(JSON.stringify({ 'staff.voice': { declared: true, installed: false } }))
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('open')

    await setSpecControl(JSON.stringify({ 'staff.voice': { declared: true, installed: true } }))
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('resolved')
  })

  it('resolves when the DECLARATION is withdrawn, not only when a spec lands', async () => {
    // The regression this guards: flipping voice_spec expected -> none removes
    // the key from the seat's map entirely. Nothing would evaluate it again, so
    // without the open-keys feedback the alert would sit open forever — a
    // control whose all-clear never arrives is the defect being fixed here.
    await setSpecControl(JSON.stringify({ 'staff.voice': { declared: true, installed: false } }))
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('open')

    await setSpecControl('{}')
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('resolved')
  })

  it('holds an open key when the seat reports it cannot look, and pages separately', async () => {
    await setSpecControl(JSON.stringify({ 'staff.voice': { declared: true, installed: false } }))
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('open')

    // ok=0 means the seat cannot read its own manifest. The per-key alert must
    // HOLD (not resolve on data the seat just disowned) and a distinct
    // condition must page: "we cannot look" is our fault, not the firm's.
    await setSpecControl('{}', 0)
    await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('open')
    expect(await alertStatus('spec_control_unprovable')).toBe('open')
  })

  it('surfaces a stale hold when the whole map goes NULL', async () => {
    await setSpecControl(JSON.stringify({ 'staff.voice': { declared: true, installed: false } }))
    await runOnce(workerEnv(), Date.now())
    await setSpecControl(null, null)
    const summary = await runOnce(workerEnv(), Date.now())
    expect(await alertStatus('spec_control_broken:staff.voice')).toBe('open')
    expect(summary.stale_holds).toContainEqual({
      customer_slug: 'seat',
      condition: 'spec_control_broken:staff.voice',
    })
  })

  it('does not open anything for a seat with no declarations', async () => {
    await setSpecControl('{}')
    await runOnce(workerEnv(), Date.now())
    const rows = await db
      .prepare(
        `SELECT condition FROM fleet_alert_state
          WHERE customer_slug = 'seat' AND status = 'open' AND condition LIKE 'spec_control%'`
      )
      .all<{ condition: string }>()
    expect(rows.results ?? []).toEqual([])
  })
})
