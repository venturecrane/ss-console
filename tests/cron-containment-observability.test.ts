/**
 * ss#2276 — cron containment visibility, console side.
 *
 * The overlay's CRON_CONTAINMENT volume sentinel durably disables all managed
 * cron jobs across boots (the #2258-incident lever) and reports the state on
 * every heartbeat as `cron_containment` 1/0. This file covers the two console
 * seams:
 *
 *   1. Ingest (migration 0105 + heartbeat.ts): stored 1/0, junk → NULL, and
 *      the overwrite-including-NULL discipline (a rollback that drops the
 *      field must not pin a stale "contained" verdict).
 *   2. Roster (fleet-roster.ts): a contained seat paints attention-yellow with
 *      the note naming the state as deliberate — visible, never mistaken for
 *      quiet, and never calming a worse color (Law 12).
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
import { rosterHealth } from '../src/lib/admin/fleet-roster'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const ORG_ID = 'org-contain'
const ENTITY = 'ent-contain'
const SLUG = 'contain-co'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES (?, 'Contain Org', 'contain-org', datetime('now'), datetime('now'))`
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
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-07-24T00:00:00Z')`
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

async function readContainment(db: D1Database): Promise<unknown> {
  const row = await db
    .prepare('SELECT cron_containment FROM fleet_status WHERE customer_slug = ?')
    .bind(SLUG)
    .first<{ cron_containment: unknown }>()
  return row?.cron_containment
}

describe('POST /api/internal/heartbeat — cron_containment (ss#2276)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
  })

  it('stores 1 and 0, coercing booleans', async () => {
    await POST(heartbeatRequest({ heartbeat_ts: new Date().toISOString(), cron_containment: 1 }))
    expect(await readContainment(db)).toBe(1)
    await POST(
      heartbeatRequest({ heartbeat_ts: new Date().toISOString(), cron_containment: false })
    )
    expect(await readContainment(db)).toBe(0)
    await POST(heartbeatRequest({ heartbeat_ts: new Date().toISOString(), cron_containment: true }))
    expect(await readContainment(db)).toBe(1)
  })

  it('stores NULL for junk — never manufactures a containment verdict', async () => {
    await POST(
      heartbeatRequest({ heartbeat_ts: new Date().toISOString(), cron_containment: 'yes' })
    )
    expect(await readContainment(db)).toBeNull()
  })

  it('a beat WITHOUT the field overwrites a stored 1 back to NULL', async () => {
    // Overwrite-including-NULL: after an overlay rollback drops the field, a
    // stale pinned "contained" must not outlive the signal that produced it.
    await POST(heartbeatRequest({ heartbeat_ts: new Date().toISOString(), cron_containment: 1 }))
    expect(await readContainment(db)).toBe(1)
    await POST(heartbeatRequest({ heartbeat_ts: new Date().toISOString() }))
    expect(await readContainment(db)).toBeNull()
  })
})

describe('rosterHealth — contained seat is visible, never calming (ss#2276)', () => {
  const scheduler = (cronContainment: number | null) => ({
    ok: 1,
    maxOverdueSeconds: 0,
    connectorCheckOk: 1,
    connectorsJson: '{}',
    cronContainment,
  })

  it('paints attention-yellow with the deliberate-containment note', () => {
    const health = rosterHealth('green', 'just now', null, null, scheduler(1))
    expect(health.color).toBe('yellow')
    expect(health.note).toBe('crons contained (deliberate)')
  })

  it('never calms a red seat', () => {
    const health = rosterHealth('red', 'stale 47m', null, null, scheduler(1))
    expect(health.color).toBe('red')
  })

  it('containment note outranks the overdue note it explains', () => {
    const health = rosterHealth('green', 'just now', null, null, {
      ...scheduler(1),
      maxOverdueSeconds: 100000,
    })
    expect(health.note).toBe('crons contained (deliberate)')
  })

  it('0 participates in nothing — a reported "not contained" is the quiet state', () => {
    expect(rosterHealth('green', 'just now', null, null, scheduler(0)).color).toBe('green')
    expect(rosterHealth('green', 'just now', null, null, scheduler(0)).note).toBeNull()
  })
})

describe('rosterHealth — containment is THREE states, never two (ss#2295)', () => {
  const scheduler = (cronContainment: number | null) => ({
    ok: 1,
    maxOverdueSeconds: 0,
    connectorCheckOk: 1,
    connectorsJson: '{}',
    cronContainment,
  })

  // Before overlay#252 (which fixed ss#2291), a seat that could not read
  // /opt/data still reported a containment verdict, so NULL meant only "old
  // overlay build". After #252 the seat sends NOTHING when the volume read
  // fails, and NULL carries information: the console cannot tell whether this
  // seat's crons are contained. Rendering that as silence made an unreadable
  // seat look identical to a healthy uncontained one.
  it('NULL is visible and distinct from a reported 0', () => {
    const unknown = rosterHealth('green', 'just now', null, null, scheduler(null))
    const notContained = rosterHealth('green', 'just now', null, null, scheduler(0))
    expect(unknown.note).not.toBe(notContained.note)
    expect(unknown.note).toBe('containment state not reported')
    expect(unknown.color).toBe('yellow')
  })

  it('the three states are mutually distinct in both note and color', () => {
    const contained = rosterHealth('green', 'just now', null, null, scheduler(1))
    const notContained = rosterHealth('green', 'just now', null, null, scheduler(0))
    const unknown = rosterHealth('green', 'just now', null, null, scheduler(null))
    expect(new Set([contained.note, notContained.note, unknown.note]).size).toBe(3)
    expect(contained.note).toBe('crons contained (deliberate)')
    expect(notContained.note).toBeNull()
    expect(contained.color).toBe('yellow')
    expect(notContained.color).toBe('green')
  })

  // A seat whose heartbeat is gray reports nothing at all; every field is NULL
  // by construction. Naming containment there would blame one field for a
  // whole-seat silence AND would escalate gray to yellow, which reads as a
  // narrower fault than the one actually present (#2295: distinguish this from
  // a genuinely stale heartbeat).
  it('says nothing about containment when the seat itself has gone quiet', () => {
    const health = rosterHealth('gray', 'no heartbeat', null, null, scheduler(null))
    expect(health.color).toBe('gray')
    expect(health.note).toBeNull()
  })

  // The production shape: the seat POSTED a beat, fleet_status has a row, the
  // field simply was not in it. An omitted key must land in the same state as
  // an explicit null — anything else would make the treatment depend on how
  // the reader spelled "absent".
  it('an omitted field is the same third state as an explicit null', () => {
    const omitted = rosterHealth('green', 'just now', null, null, {
      ok: 1,
      maxOverdueSeconds: 0,
      connectorCheckOk: 1,
      connectorsJson: '{}',
    })
    expect(omitted.color).toBe('yellow')
    expect(omitted.note).toBe('containment state not reported')
  })

  // No SchedulerSignal bundle means no fleet_status row: no beat was recorded,
  // so no beat can be said to have omitted the field. Claiming otherwise would
  // manufacture an observation the console never made.
  it('says nothing when there is no fleet_status row to have carried a beat', () => {
    const health = rosterHealth('green', 'just now', null, null, null)
    expect(health.color).toBe('green')
    expect(health.note).toBeNull()
  })

  it('never calms a worse color, and never outranks an actionable note', () => {
    expect(rosterHealth('red', 'stale 47m', null, null, scheduler(null)).color).toBe('red')
    const overdue = rosterHealth('green', 'just now', null, null, {
      ...scheduler(null),
      maxOverdueSeconds: 100000,
    })
    expect(overdue.note).toBe('scheduled work overdue')
  })
})
