/**
 * The `bundles` heartbeat field (ADR 0100 W3, migration 0144).
 *
 * The release controller's bundle step writes a seat's pointer, restarts it,
 * and reads `fleet_status.bundles_json` back: the step passes only when the seat
 * reports `bundles.jobs` equal to the version it shipped, and rolls back
 * otherwise. So the column must hold exactly what the seat said on its newest
 * beat: a validated `{name: version}` map, NULL when the beat said nothing or
 * said something malformed, never an older value. Pinned at the parser and
 * through the real handler against D1, and the 0144 rebuild is pinned to carry
 * every fleet_releases row across.
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
import { BUNDLES_MAX_ENTRIES, parseBundles } from '../src/lib/operator/heartbeat-parsers'
import { POST } from '../src/pages/api/internal/heartbeat'

installWorkerdPolyfills()

const V = '202610311200-abcdef12'
const MIGRATIONS = path.resolve(__dirname, '..', 'migrations')

describe('parseBundles', () => {
  it('keeps a mounted version and the image fallback, keys sorted', () => {
    expect(parseBundles({ jobs: V })).toBe(`{"jobs":"${V}"}`)
    expect(parseBundles({ jobs: 'image' })).toBe('{"jobs":"image"}')
    expect(parseBundles({ zeta: 'image', jobs: V })).toBe(`{"jobs":"${V}","zeta":"image"}`)
    expect(parseBundles({})).toBe('{}')
  })

  it('reads anything not a map as unknown (NULL)', () => {
    for (const v of [undefined, null, 'jobs', 3, [V], true]) expect(parseBundles(v)).toBeNull()
  })

  it('NEGATIVE CONTROL: one bad entry nulls the whole map, never a partial one', () => {
    // FALSIFIER: drop an entry check and a partial map reaches the column.
    expect(parseBundles({ jobs: V, Bad: V })).toBeNull()
    expect(parseBundles({ jobs: V, other: 'v1' })).toBeNull()
    expect(parseBundles({ jobs: `${V}\n` })).toBeNull()
    expect(parseBundles({ jobs: 202610311200 })).toBeNull()
    expect(parseBundles({ jobs: '202610311200-ABCDEF12' })).toBeNull()
    expect(parseBundles({ ['j'.repeat(33)]: V })).toBeNull()
  })

  it('bounds the number of entries', () => {
    const many: Record<string, string> = Object.fromEntries(
      Array.from({ length: BUNDLES_MAX_ENTRIES + 1 }, (_, i) => [`b${i}`, V])
    )
    expect(parseBundles(many)).toBeNull()
    delete many[`b${BUNDLES_MAX_ENTRIES}`]
    expect(parseBundles(many)).not.toBeNull()
  })
})

const MACHINE_KEY = 'test-machine-heartbeat-key-32-chars'
const SLUG = 'bundle-co'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at)
       VALUES ('org-bnd', 'Bnd Org', 'bnd-org', datetime('now'), datetime('now'))`
    )
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES ('ent-bnd', 'org-bnd', ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(SLUG, SLUG)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES ('ent-bnd', 'org-bnd', ?, '1.0.0', '[]', 'sha', '2026-10-31T00:00:00Z')`
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
    body: JSON.stringify({ heartbeat_ts: '2026-10-31T00:00:00.000Z', ...body }),
  })
  const res = await POST({ request, params: {}, locals: {} } as unknown as Parameters<
    typeof POST
  >[0])
  return res.status
}

describe('bundles through the heartbeat ingest', () => {
  let db: D1Database
  const stored = async () =>
    (
      await db
        .prepare('SELECT bundles_json FROM fleet_status WHERE customer_slug = ?')
        .bind(SLUG)
        .first<{ bundles_json: string | null }>()
    )?.bundles_json

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(MIGRATIONS) })
    await seed(db)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
  })

  it('stores the reported map', async () => {
    expect(await post({ bundles: { jobs: V } })).toBe(200)
    expect(await stored()).toBe(`{"jobs":"${V}"}`)
  })

  it('an absent key overwrites to NULL (a stale version never reads back as mounted)', async () => {
    await post({ bundles: { jobs: V } })
    await post({})
    expect(await stored()).toBeNull()
  })

  it('a malformed map stores NULL and the beat still lands', async () => {
    await post({ bundles: { jobs: V } })
    expect(await post({ bundles: { jobs: 'x' } })).toBe(200)
    expect(await stored()).toBeNull()
  })
})

describe('migration 0144', () => {
  const isNew = (f: string) => /(^|\/)0144_[^/]*\.sql$/.test(f)

  it('carries every fleet_releases row across and admits kind bundle', async () => {
    const files = discoverNumericMigrations(MIGRATIONS)
    const at = files.findIndex(isNew)
    expect(at).toBeGreaterThan(0)
    const db = createTestD1()
    await runMigrations(db, { files: files.slice(0, at) })
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, image_ref, image_digest, ss_console_sha,
           volume_forward_compatible, declared_secrets, notes, created_at, completed_last_ring_at)
         VALUES ('img-202610300000-aaaaaaaa', 'image', 'registry.fly.io/hermes-fleet:x', ?, ?,
           0, '["A"]', 'n', '2026-10-30T00:00:00Z', '2026-10-30T01:00:00Z')`
      )
      .bind(`sha256:${'a'.repeat(64)}`, 'b'.repeat(40))
      .run()
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, skills_version, created_at)
         VALUES ('skl-202610300000-bbbbbbbb', 'skills', '202610300000-bbbbbbbb', '2026-10-30T00:00:00Z')`
      )
      .run()
    // FALSIFIER: the pre-0144 table refuses kind bundle.
    await expect(
      db
        .prepare(
          `INSERT INTO fleet_releases (release_id, kind, created_at) VALUES ('bnd-x', 'bundle', 'now')`
        )
        .run()
    ).rejects.toThrow()
    await runMigrations(db, { files: [files[at]] })
    const rows = await db.prepare('SELECT * FROM fleet_releases ORDER BY release_id').all()
    expect(rows.results).toEqual([
      {
        release_id: 'img-202610300000-aaaaaaaa',
        kind: 'image',
        image_ref: 'registry.fly.io/hermes-fleet:x',
        image_digest: `sha256:${'a'.repeat(64)}`,
        skills_version: null,
        ss_console_sha: 'b'.repeat(40),
        overlay_ref: null,
        volume_forward_compatible: 0,
        declared_secrets: '["A"]',
        notes: 'n',
        created_at: '2026-10-30T00:00:00Z',
        completed_last_ring_at: '2026-10-30T01:00:00Z',
        artifact_name: null,
        artifact_version: null,
      },
      {
        release_id: 'skl-202610300000-bbbbbbbb',
        kind: 'skills',
        image_ref: null,
        image_digest: null,
        skills_version: '202610300000-bbbbbbbb',
        ss_console_sha: null,
        overlay_ref: null,
        volume_forward_compatible: 1,
        declared_secrets: null,
        notes: null,
        created_at: '2026-10-30T00:00:00Z',
        completed_last_ring_at: null,
        artifact_name: null,
        artifact_version: null,
      },
    ])
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, artifact_name, artifact_version, created_at)
         VALUES ('bnd-202610310000-cccccccc', 'bundle', 'jobs', '202610310000-cccccccc', 'now')`
      )
      .run()
    await expect(
      db
        .prepare(
          `INSERT INTO fleet_releases (release_id, kind, created_at) VALUES ('x', 'other', 'now')`
        )
        .run()
    ).rejects.toThrow()
    const cols = await db.prepare(`SELECT name FROM pragma_table_info('fleet_status')`).all()
    expect(cols.results.map((r) => (r as { name: string }).name)).toContain('bundles_json')
  })
})
