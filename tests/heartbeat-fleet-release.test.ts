/**
 * The fleet-release heartbeat fields (seat fleet scaling, migration 0129).
 *
 * `fleet release` reads `image_ref` / `skills_version` back after a deploy and
 * the busy gate reads the three counts. The overlay OMITS a value it does not
 * know (its tests pin "omit None"; it never sends JSON null), so an absent key
 * must store NULL = unknown. Keeping the old column instead would let a stale
 * image ref read back as a deploy that never landed, or a stale job count let
 * the gate trust a number nobody reported. Pinned at the parser and through
 * the real handler against D1.
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

const ALL_NULL = {
  imageRef: null,
  skillsVersion: null,
  configDigest: null,
  jobsRunning: null,
  jobsQueued: null,
  wakesPending: null,
  bundlesJson: null,
}

describe('parseFleetRelease', () => {
  it('keeps good values', () => {
    expect(
      beat({
        image_ref: 'registry.fly.io/hermes-fleet:rel-abcd1234-ef567890',
        skills_version: '2026-10-19.1',
        config_digest: 'sha256:0123abcd',
        jobs_running: 0,
        jobs_queued: 3,
        wakes_pending: 1,
      })
    ).toEqual({
      imageRef: 'registry.fly.io/hermes-fleet:rel-abcd1234-ef567890',
      skillsVersion: '2026-10-19.1',
      configDigest: 'sha256:0123abcd',
      jobsRunning: 0,
      jobsQueued: 3,
      wakesPending: 1,
      bundlesJson: null,
    })
  })

  it('reads an absent key as unknown (NULL)', () => {
    expect(beat({})).toEqual(ALL_NULL)
  })

  it('reads an explicit null as unknown (NULL)', () => {
    expect(
      beat({
        image_ref: null,
        skills_version: null,
        config_digest: null,
        jobs_running: null,
        jobs_queued: null,
        wakes_pending: null,
      })
    ).toEqual(ALL_NULL)
  })

  it('stores NULL for junk, never a cast', () => {
    expect(
      beat({
        image_ref: 'x'.repeat(301),
        skills_version: 'v1\nInjected: line',
        config_digest: 42,
        jobs_running: -1,
        jobs_queued: 1.5,
        wakes_pending: '3',
      })
    ).toEqual(ALL_NULL)
    expect(beat({ image_ref: '' }).imageRef).toBeNull()
    expect(beat({ image_ref: 'x'.repeat(300) }).imageRef).toBe('x'.repeat(300))
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
const NULL_ROW = Object.fromEntries(COLUMNS.map((c) => [c, null]))

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

  it('stores every reported value', async () => {
    expect(await post(full)).toBe(200)
    expect(await row()).toEqual(full)
  })

  it('an absent key overwrites to NULL (no stale image ref or job count survives)', async () => {
    await post(full)
    await post({})
    expect(await row()).toEqual(NULL_ROW)
  })

  it('a partial beat stores what it reported and NULL for the rest', async () => {
    await post(full)
    await post({ image_ref: 'registry.fly.io/hermes-fleet:rel-2', jobs_running: 0 })
    expect(await row()).toEqual({
      ...NULL_ROW,
      image_ref: 'registry.fly.io/hermes-fleet:rel-2',
      jobs_running: 0,
    })
  })

  it('NEGATIVE CONTROL: junk stores NULL rather than holding the old value', async () => {
    await post(full)
    await post({ ...full, jobs_running: -5, image_ref: 'bad\u0000ref' })
    const r = await row()
    expect(r?.jobs_running).toBeNull()
    expect(r?.image_ref).toBeNull()
    expect(r?.skills_version).toBe('sk-1')
  })
})
