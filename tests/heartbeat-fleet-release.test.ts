/**
 * The fleet-release heartbeat fields (seat fleet scaling, migration 0129).
 *
 * These six are the only heartbeat fields that tell ABSENT from null. Every
 * seat on an older overlay omits all six, and `fleet release` reads
 * `image_ref` / `skills_version` back after a deploy and the busy gate reads
 * the three counts, so an omission erasing a known value would read as
 * "unknown" fleet-wide; and a stale count outliving the seat's own explicit
 * "unknown" would let the gate deploy into a running job. Both directions are
 * pinned here, at the parser and through the real handler against D1.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import { seedMachineCredential } from './helpers/machine-credential'
import { parseFleetRelease } from '../src/lib/operator/heartbeat-parsers'
import { POST } from '../src/pages/api/internal/heartbeat'

installWorkerdPolyfills()

const beat = (extra: Record<string, unknown>) => parseFleetRelease({ heartbeat_ts: 't', ...extra })

describe('parseFleetRelease', () => {
  it('keeps good values', () => {
    const r = beat({
      image_ref: 'registry.fly.io/hermes-fleet:rel-abcd1234-ef567890',
      skills_version: '2026-10-19.1',
      config_digest: 'sha256:0123abcd',
      jobs_running: 0,
      jobs_queued: 3,
      wakes_pending: 1,
    })
    expect(r.imageRef).toEqual({
      reported: true,
      value: 'registry.fly.io/hermes-fleet:rel-abcd1234-ef567890',
    })
    expect(r.skillsVersion).toEqual({ reported: true, value: '2026-10-19.1' })
    expect(r.configDigest).toEqual({ reported: true, value: 'sha256:0123abcd' })
    expect(r.jobsRunning).toEqual({ reported: true, value: 0 })
    expect(r.jobsQueued).toEqual({ reported: true, value: 3 })
    expect(r.wakesPending).toEqual({ reported: true, value: 1 })
  })

  it('reports an absent key as not reported', () => {
    const r = beat({})
    for (const f of Object.values(r)) expect(f).toEqual({ reported: false })
  })

  it('reports an explicit null as a reported NULL', () => {
    const r = beat({
      image_ref: null,
      skills_version: null,
      config_digest: null,
      jobs_running: null,
      jobs_queued: null,
      wakes_pending: null,
    })
    for (const f of Object.values(r)) expect(f).toEqual({ reported: true, value: null })
  })

  it('stores NULL for junk, never a cast', () => {
    const r = beat({
      image_ref: 'x'.repeat(301),
      skills_version: 'v1\nInjected: line',
      config_digest: 42,
      jobs_running: -1,
      jobs_queued: 1.5,
      wakes_pending: '3',
    })
    for (const f of Object.values(r)) expect(f).toEqual({ reported: true, value: null })
    expect(beat({ image_ref: '' }).imageRef).toEqual({ reported: true, value: null })
    expect(beat({ image_ref: 'x'.repeat(300) }).imageRef).toEqual({
      reported: true,
      value: 'x'.repeat(300),
    })
  })
})

const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const SLUG = 'release-co'
const COLUMNS = [
  'image_ref',
  'skills_version',
  'config_digest',
  'jobs_running',
  'jobs_queued',
  'wakes_pending',
] as const

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES ('org-rel', 'Rel Org', 'rel-org', datetime('now'), datetime('now'))`
    )
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES ('ent-rel', 'org-rel', ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(SLUG, SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES ('ent-rel', 'org-rel', ?, '1.0.0', '[]', 'sha', '2026-10-19T00:00:00Z')`
    )
    .bind(SLUG)
    .run()
}

async function post(body: Record<string, unknown>): Promise<number> {
  const request = new Request('http://test.local/api/internal/heartbeat', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${MACHINE_KEY}`,
      'X-Tenant-Slug': SLUG,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ heartbeat_ts: '2026-10-19T00:00:00.000Z', ...body }),
  })
  const res = await POST({ request, params: {}, locals: {} } as unknown as Parameters<
    typeof POST
  >[0])
  return res.status
}

describe('fleet-release fields through the heartbeat ingest', () => {
  let db: D1Database

  const row = () =>
    db
      .prepare(`SELECT ${COLUMNS.join(', ')} FROM fleet_status WHERE customer_slug = ?`)
      .bind(SLUG)
      .first<Record<string, unknown>>()

  const full = {
    image_ref: 'registry.fly.io/hermes-fleet:rel-1',
    skills_version: 'sk-1',
    config_digest: 'sha256:aa',
    jobs_running: 2,
    jobs_queued: 0,
    wakes_pending: 1,
  }

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(path.resolve(__dirname, '..', 'migrations')),
    })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
  })

  it('a first beat stores what it reported and NULL for the rest', async () => {
    expect(await post({ image_ref: 'img-a', jobs_running: 0 })).toBe(200)
    expect(await row()).toEqual({
      image_ref: 'img-a',
      skills_version: null,
      config_digest: null,
      jobs_running: 0,
      jobs_queued: null,
      wakes_pending: null,
    })
  })

  it('an absent key keeps the stored value (an older overlay erases nothing)', async () => {
    await post(full)
    await post({})
    expect(await row()).toEqual(full)
  })

  it('an explicit null overwrites to NULL', async () => {
    await post(full)
    await post(Object.fromEntries(COLUMNS.map((c) => [c, null])))
    expect(await row()).toEqual(Object.fromEntries(COLUMNS.map((c) => [c, null])))
  })

  it('a partial beat overwrites only what it reported', async () => {
    await post(full)
    await post({ image_ref: 'registry.fly.io/hermes-fleet:rel-2', jobs_running: null })
    expect(await row()).toEqual({
      ...full,
      image_ref: 'registry.fly.io/hermes-fleet:rel-2',
      jobs_running: null,
    })
  })

  it('NEGATIVE CONTROL: junk is reported, so it stores NULL rather than holding', async () => {
    await post(full)
    await post({ jobs_running: -5, image_ref: 'bad\u0000ref' })
    const r = await row()
    expect(r?.jobs_running).toBeNull()
    expect(r?.image_ref).toBeNull()
    expect(r?.skills_version).toBe('sk-1')
  })
})
