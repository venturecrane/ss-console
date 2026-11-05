/**
 * Migration 0148: `fleet_releases.merged_at`, the start of the merge-to-seat
 * clock the admin Fleet page reads. Applied through the real migration chain,
 * a release written with a merge time reads it back, and an older-shaped
 * insert that omits it reads NULL (never a default the page could mistake for
 * a real time).
 */

import { describe, it, expect } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'

const MIGRATIONS = path.resolve(__dirname, '..', 'migrations')

describe('migration 0148', () => {
  const isNew = (f: string) => /(^|\/)0148_[^/]*\.sql$/.test(f)

  it('adds merged_at: written values read back, omitted values read NULL', async () => {
    const files = discoverNumericMigrations(MIGRATIONS)
    const at = files.findIndex(isNew)
    expect(at).toBeGreaterThan(0)
    const db = createTestD1()
    await runMigrations(db, { files: files.slice(0, at) })
    // FALSIFIER: before 0148 the column does not exist (the harness throws at
    // prepare, D1 at run; an async wrapper catches either).
    await expect(
      (async () =>
        db
          .prepare(
            `INSERT INTO fleet_releases (release_id, kind, created_at, merged_at)
             VALUES ('img-x', 'image', 'now', 'then')`
          )
          .run())()
    ).rejects.toThrow(/merged_at/)
    await runMigrations(db, { files: files.slice(at, at + 1) })
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, image_ref, created_at, merged_at)
         VALUES ('img-202611050000-aaaaaaaa', 'image', 'registry.fly.io/hermes-fleet:x',
           '2026-11-05T00:10:00Z', '2026-11-05T00:00:00Z')`
      )
      .run()
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, skills_version, created_at)
         VALUES ('skl-202611050000-bbbbbbbb', 'skills', '202611050000-bbbbbbbb',
           '2026-11-05T00:00:00Z')`
      )
      .run()
    const rows = await db
      .prepare('SELECT release_id, merged_at FROM fleet_releases ORDER BY release_id')
      .all()
    expect(rows.results).toEqual([
      { release_id: 'img-202611050000-aaaaaaaa', merged_at: '2026-11-05T00:00:00Z' },
      { release_id: 'skl-202611050000-bbbbbbbb', merged_at: null },
    ])
  })
})
