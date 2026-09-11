/**
 * ss#2500 - the off-Machine pin history.
 *
 * WHAT THIS PROTECTS. The audit ledger is a hash chain, and a chain walk cannot
 * see rows cut off the END: the surviving prefix is a valid chain. Measured, not
 * argued (vfy_01M0H8D1CV2X8J9ZACMAC8E6E2). The only input that closes it is a
 * head recorded somewhere the seat cannot reach, and this table is that
 * recording. Every property below is one a later verifier depends on:
 *
 *  - a pin is never overwritten by a later beat (a pin that could be is not one);
 *  - a repeated head does not multiply rows, so the table stays readable at a
 *    60-second beat without dropping any distinct head;
 *  - a head that comes BACK after a different one still inserts, because that is
 *    a ledger regression and collapsing it would erase the evidence;
 *  - a junk head is dropped rather than pinned, because a pin that can never
 *    match would accuse a healthy ledger every day until someone read the row.
 *
 * Posted at the REAL ingest handler rather than calling recordAuditHead
 * directly: the defect class this closes (ss#2287) is a field that reaches the
 * wire and is dropped at ingest, and only the handler proves the wiring.
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
const ORG_ID = 'org-pin'
const ENTITY = 'ent-pin'
const SLUG = 'pin-co'

const HEAD_A = 'a'.repeat(64)
const HEAD_B = 'b'.repeat(64)

interface PinRow {
  audit_head: string
  audit_rows: number | null
  first_seen_heartbeat_ts: string
  last_seen_heartbeat_ts: string
  beats: number
}

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Pin Org', 'pin-org', datetime('now'), datetime('now'))`
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
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-08-20T00:00:00Z')`
    )
    .bind(ENTITY, ORG_ID, SLUG)
    .run()
}

async function beat(body: Record<string, unknown>): Promise<Response> {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': SLUG,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return POST({ request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0])
}

async function pins(db: D1Database): Promise<PinRow[]> {
  const result = await db
    .prepare(
      `SELECT audit_head, audit_rows, first_seen_heartbeat_ts, last_seen_heartbeat_ts, beats
         FROM audit_head_history WHERE customer_slug = ? ORDER BY id ASC`
    )
    .bind(SLUG)
    .all<PinRow>()
  return result.results
}

describe('audit head history - the pin the seat cannot reach (ss#2500)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, MACHINE_HEARTBEAT_KEY: MACHINE_KEY })
  })

  it('a heartbeat carrying a head pins it', async () => {
    const response = await beat({
      heartbeat_ts: '2026-08-20T00:00:00.000Z',
      audit_head: HEAD_A,
      audit_rows: 1473,
    })
    expect(response.status).toBe(200)

    const rows = await pins(db)
    expect(rows).toHaveLength(1)
    expect(rows[0].audit_head).toBe(HEAD_A)
    expect(rows[0].audit_rows).toBe(1473)
    expect(rows[0].first_seen_heartbeat_ts).toBe('2026-08-20T00:00:00.000Z')
    expect(rows[0].beats).toBe(1)
  })

  it('a repeated head refreshes liveness without touching the pin or adding a row', async () => {
    await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z', audit_head: HEAD_A, audit_rows: 10 })
    await beat({ heartbeat_ts: '2026-08-20T00:01:00.000Z', audit_head: HEAD_A, audit_rows: 10 })
    await beat({ heartbeat_ts: '2026-08-20T00:02:00.000Z', audit_head: HEAD_A, audit_rows: 10 })

    const rows = await pins(db)
    expect(rows).toHaveLength(1)
    expect(rows[0].beats).toBe(3)
    // The pin fields are untouched; only the liveness field moved.
    expect(rows[0].first_seen_heartbeat_ts).toBe('2026-08-20T00:00:00.000Z')
    expect(rows[0].last_seen_heartbeat_ts).toBe('2026-08-20T00:02:00.000Z')
  })

  it('a new head appends rather than replacing, so no pin is ever lost', async () => {
    await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z', audit_head: HEAD_A, audit_rows: 10 })
    await beat({ heartbeat_ts: '2026-08-20T00:01:00.000Z', audit_head: HEAD_B, audit_rows: 11 })

    const rows = await pins(db)
    expect(rows.map((r) => r.audit_head)).toEqual([HEAD_A, HEAD_B])
  })

  it('a head that comes back after another one inserts again (a ledger regression is evidence)', async () => {
    await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z', audit_head: HEAD_A, audit_rows: 10 })
    await beat({ heartbeat_ts: '2026-08-20T00:01:00.000Z', audit_head: HEAD_B, audit_rows: 11 })
    await beat({ heartbeat_ts: '2026-08-20T00:02:00.000Z', audit_head: HEAD_A, audit_rows: 10 })

    const rows = await pins(db)
    expect(rows.map((r) => r.audit_head)).toEqual([HEAD_A, HEAD_B, HEAD_A])
  })

  it('a beat with no head pins nothing rather than pinning a NULL', async () => {
    const response = await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z' })
    expect(response.status).toBe(200)
    expect(await pins(db)).toHaveLength(0)
  })

  it('NEGATIVE CONTROL: a junk head is dropped, not pinned', async () => {
    // Without this the parser could be accepting anything and every test above
    // would still pass. A pinned junk head can never match an export, so the
    // daily verifier would report a break on a healthy ledger every day.
    for (const junk of ['not-a-hash', 'A'.repeat(64), 'a'.repeat(63), 12345, null]) {
      await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z', audit_head: junk })
    }
    expect(await pins(db)).toHaveLength(0)
  })

  it('a junk row count is stored NULL rather than coerced', async () => {
    for (const junk of ['lots', -1, 1.5]) {
      await beat({
        heartbeat_ts: '2026-08-20T00:00:00.000Z',
        audit_head: HEAD_A,
        audit_rows: junk,
      })
    }
    const rows = await pins(db)
    // One pin (the head never changed), and the count it was pinned with is
    // NULL rather than a number nobody sent.
    expect(rows).toHaveLength(1)
    expect(rows[0].audit_rows).toBeNull()
  })

  it('an all-zero digest pins like any other head', async () => {
    // Not a hypothetical: the heartbeat parity manifest (ss#2498) uses 64 zeros
    // as its sample, so this exact value reaches this handler on every parity
    // run. It is valid hex and must pin; a parser that special-cased a
    // zero-looking digest would silently drop a real beat.
    await beat({ heartbeat_ts: '2026-08-20T00:00:00.000Z', audit_head: '0'.repeat(64) })
    const rows = await pins(db)
    expect(rows).toHaveLength(1)
    expect(rows[0].audit_head).toBe('0'.repeat(64))
  })
})
