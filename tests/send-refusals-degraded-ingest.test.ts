/**
 * The 'degraded' send-refusal kind reaches the pager (2026-08-24).
 *
 * A routine whose own pre-run withholds an unfit digest writes a
 * SUPPRESSED_WAKE with a digest_degraded basis; the overlay heartbeat counts it
 * as a send-refusal event of kind 'degraded' riding the existing ss#2547
 * fields. Two properties are pinned here because the page depends on them and
 * nothing else asserted them:
 *
 *  1. The ingest accepts the 'degraded' kind into `send_refusals_json` (an
 *     unknown kind is still dropped — the negative control).
 *  2. `send_refusals_last_ts` (the pager's marker) is stored INDEPENDENTLY of
 *     the event-kind filter: a beat whose detail list is entirely dropped still
 *     lands the marker, so a junk or future-kind list can never suppress the
 *     page itself. That independence existed by construction; this makes it a
 *     contract instead of a coincidence.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { POST } from '../src/pages/api/internal/heartbeat'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = path.join(path.resolve(__dirname, '..'), 'migrations')

const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const ORG_ID = 'org-degraded'
const ENTITY = 'ent-degraded'
const SLUG = 'degraded-co'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Degraded Org', 'degraded-org', datetime('now'), datetime('now'))`
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

const MARKER = '2026-08-24T14:03:51.665Z'

describe('degraded send-refusal ingest', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, MACHINE_HEARTBEAT_KEY: MACHINE_KEY })
  })

  async function row(): Promise<Record<string, unknown> | null> {
    return await db
      .prepare(
        `SELECT send_refusals, send_refusals_last_ts, send_refusals_json
         FROM fleet_status WHERE customer_slug = ?`
      )
      .bind(SLUG)
      .first()
  }

  it("a degraded-only beat stores the count, the marker, and the event's reason", async () => {
    const response = await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-24T14:04:00.000Z',
        send_refusals: 1,
        send_refusals_last_ts: MARKER,
        send_refusals_json: [
          {
            ts: MARKER,
            kind: 'degraded',
            routine: 'op-managed:operator:deadline-miss-escalator',
            reason: '12 deadline(s) withheld: 0 matter numbers resolved, 12 lookup(s) failed',
          },
        ],
      })
    )
    expect(response.status).toBe(200)
    const stored = await row()
    expect(stored?.send_refusals).toBe(1)
    expect(stored?.send_refusals_last_ts).toBe(MARKER)
    const events = JSON.parse(String(stored?.send_refusals_json))
    expect(events).toHaveLength(1)
    expect(events[0].kind).toBe('degraded')
    expect(events[0].reason).toContain('withheld')
  })

  it('the marker lands even when every event is an unknown kind (marker independence)', async () => {
    const response = await POST(
      heartbeatRequest({
        heartbeat_ts: '2026-08-24T14:04:00.000Z',
        send_refusals: 1,
        send_refusals_last_ts: MARKER,
        send_refusals_json: [{ ts: MARKER, kind: 'some-future-kind', reason: 'x' }],
      })
    )
    expect(response.status).toBe(200)
    const stored = await row()
    // The unknown kind is dropped from the detail list (negative control)...
    const events = JSON.parse(String(stored?.send_refusals_json))
    expect(events).toHaveLength(0)
    // ...but the pager's marker is parsed independently and still lands.
    expect(stored?.send_refusals_last_ts).toBe(MARKER)
  })
})
