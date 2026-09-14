/**
 * ss#2287 — webhook expected-tool surface, console side.
 *
 * The defect this closes: the seat has emitted `webhook_surface_ok` and
 * `webhook_surface` on EVERY heartbeat since ss#2222 (overlay
 * `shared/heartbeat.py`, fed by `shared/webhook_surface_check.check()`), and
 * ss-console had no column, no parser, and no alert for either. The whole warn
 * tier was written to the wire and dropped at ingest. The overlay docstring says
 * the empty map "is what RESOLVES an open alert"; there was no alert to resolve.
 *
 * Covered here: the ingest seam (migration 0106 + heartbeat.ts) — 1/0/NULL
 * coercion for the check's own health, the per-tool map's parse-not-cast
 * guardrails, and the overwrite-including-NULL discipline every alert-driving
 * field shares. The alerting half lives in the fleet-alerts Worker's own suite
 * (`workers/fleet-alerts/src/index.test.ts`), which is where the condition
 * machinery is testable without a Worker deploy.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { seedMachineCredential } from './helpers/machine-credential'
import path from 'node:path'
import { POST } from '../src/pages/api/internal/heartbeat'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const ORG_ID = 'org-surface'
const ENTITY = 'ent-surface'
const SLUG = 'surface-co'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Surface Org', 'surface-org', datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY, ORG_ID, SLUG, SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-08-11T00:00:00Z')`
    )
    .bind(ENTITY, ORG_ID, SLUG)
    .run()
}

function heartbeatRequest(body: Record<string, unknown>): Parameters<typeof POST>[0] {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': SLUG,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0]
}

async function readSurface(
  db: D1Database
): Promise<{ webhook_surface_ok: unknown; webhook_surface_json: unknown } | null> {
  const row = await db
    .prepare(
      'SELECT webhook_surface_ok, webhook_surface_json FROM fleet_status WHERE customer_slug = ?'
    )
    .bind(SLUG)
    .first<{ webhook_surface_ok: unknown; webhook_surface_json: unknown }>()
  return row ?? null
}

const beat = (extra: Record<string, unknown> = {}) => ({
  heartbeat_ts: new Date().toISOString(),
  ...extra,
})

describe('POST /api/internal/heartbeat — webhook_surface (ss#2287)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
  })

  it('stores webhook_surface_ok:false as 0 — the seat saying it cannot look must land', async () => {
    // The regression this whole issue is about: this exact body was posted on
    // every beat and reached no column.
    await POST(heartbeatRequest(beat({ webhook_surface_ok: false })))
    expect((await readSurface(db))?.webhook_surface_ok).toBe(0)
  })

  it('stores the true/1 forms as 1, matching the other *_ok fields', async () => {
    await POST(heartbeatRequest(beat({ webhook_surface_ok: true })))
    expect((await readSurface(db))?.webhook_surface_ok).toBe(1)
    await POST(heartbeatRequest(beat({ webhook_surface_ok: 0 })))
    expect((await readSurface(db))?.webhook_surface_ok).toBe(0)
  })

  it('stores NULL for junk — never manufactures a surface verdict', async () => {
    await POST(heartbeatRequest(beat({ webhook_surface_ok: 'yes' })))
    expect((await readSurface(db))?.webhook_surface_ok).toBeNull()
  })

  it('stores the per-tool map, keeping both sides of each entry', async () => {
    await POST(
      heartbeatRequest(
        beat({
          webhook_surface_ok: true,
          webhook_surface: { operator_seat_facts: { expected: true, offered: false } },
        })
      )
    )
    const row = await readSurface(db)
    expect(JSON.parse(String(row?.webhook_surface_json))).toEqual({
      operator_seat_facts: { expected: true, offered: false },
    })
  })

  it('stores an EMPTY reported map as {} — the state that resolves an open alert', async () => {
    // Distinct from absent. The overlay docstring is explicit: an empty map is a
    // real "checked, every expected tool is offered" answer. Coercing it to NULL
    // would leave a repaired surface paging forever.
    await POST(heartbeatRequest(beat({ webhook_surface_ok: true, webhook_surface: {} })))
    expect(JSON.parse(String((await readSurface(db))?.webhook_surface_json))).toEqual({})
  })

  it('drops an entry missing either flag, never defaulting one', async () => {
    // `offered` is what opens and closes the alert; inferring it would be
    // manufacturing the verdict, exactly as spec_control refuses to.
    await POST(
      heartbeatRequest(
        beat({
          webhook_surface_ok: true,
          webhook_surface: {
            operator_seat_facts: { expected: true },
            other_tool: { expected: true, offered: true },
          },
        })
      )
    )
    expect(JSON.parse(String((await readSurface(db))?.webhook_surface_json))).toEqual({
      other_tool: { expected: true, offered: true },
    })
  })

  it('a structurally-invalid map is whole-map NULL (trust nothing this beat)', async () => {
    await POST(heartbeatRequest(beat({ webhook_surface_ok: true, webhook_surface: ['nope'] })))
    expect((await readSurface(db))?.webhook_surface_json).toBeNull()
  })

  it('a beat WITHOUT the fields overwrites stored values back to NULL', async () => {
    // Overwrite-including-NULL: an overlay rollback that drops the fields must
    // not pin a stale verdict, and the Worker holds open alerts on NULL.
    await POST(
      heartbeatRequest(
        beat({
          webhook_surface_ok: false,
          webhook_surface: { operator_seat_facts: { expected: true, offered: false } },
        })
      )
    )
    expect((await readSurface(db))?.webhook_surface_ok).toBe(0)
    await POST(heartbeatRequest(beat()))
    const row = await readSurface(db)
    expect(row?.webhook_surface_ok).toBeNull()
    expect(row?.webhook_surface_json).toBeNull()
  })
})
