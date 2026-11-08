/**
 * Migration 0150: `fleet_releases.volume_epoch`, the seat-volume layout an image
 * release's boot expects. The rollout refuses to put a seat on an image of a
 * lower epoch (operator/bin/lib/fleet/volume_epoch.py). Applied through the
 * real migration chain, an image release written with an epoch reads it back,
 * and a row that omits it reads NULL (which the rollout counts as epoch 1,
 * never a default that could pass for a newer layout).
 */

import { describe, it, expect } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'

const MIGRATIONS = path.resolve(__dirname, '..', 'migrations')

describe('migration 0150', () => {
  const isNew = (f: string) => /(^|\/)0150_[^/]*\.sql$/.test(f)

  it('adds volume_epoch: written values read back, omitted values read NULL', async () => {
    const files = discoverNumericMigrations(MIGRATIONS)
    const at = files.findIndex(isNew)
    expect(at).toBeGreaterThan(0)
    const db = createTestD1()
    await runMigrations(db, { files: files.slice(0, at) })
    // FALSIFIER: before 0150 the column does not exist.
    await expect(
      (async () =>
        db
          .prepare(
            `INSERT INTO fleet_releases (release_id, kind, created_at, volume_epoch)
             VALUES ('img-x', 'image', 'now', 2)`
          )
          .run())()
    ).rejects.toThrow(/volume_epoch/)
    await runMigrations(db, { files: files.slice(at, at + 1) })
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, image_ref, created_at, volume_epoch)
         VALUES ('img-202611070000-aaaaaaaa', 'image', 'registry.fly.io/hermes-fleet:x',
           '2026-11-07T00:00:00Z', 2)`
      )
      .run()
    await db
      .prepare(
        `INSERT INTO fleet_releases (release_id, kind, image_ref, created_at)
         VALUES ('img-202611060000-bbbbbbbb', 'image', 'registry.fly.io/hermes-fleet:y',
           '2026-11-06T00:00:00Z')`
      )
      .run()
    const rows = await db
      .prepare('SELECT release_id, volume_epoch FROM fleet_releases ORDER BY release_id')
      .all()
    expect(rows.results).toEqual([
      { release_id: 'img-202611060000-bbbbbbbb', volume_epoch: null },
      { release_id: 'img-202611070000-aaaaaaaa', volume_epoch: 2 },
    ])
  })
})
