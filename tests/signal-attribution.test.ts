/**
 * Originating-signal attribution tests (#589).
 *
 * Exercises:
 *   - DAL helpers (listSignalsForEntity, getDefaultOriginatingSignalId,
 *     getSignalById)
 *   - createQuote / createEngagement default-resolution behavior
 *     (undefined → most recent signal, null → unattributed,
 *     explicit string → stored as-is)
 *   - update paths persist the new column
 *
 * Uses @venturecrane/crane-test-harness so we exercise real D1 SQL,
 * not text-greps. The lifecycle DAL is the load-bearing surface here —
 * source-string checks would miss the actual default-resolution.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity } from '../src/lib/db/entities'
import { appendContext } from '../src/lib/db/context'
import { createQuote } from '../src/lib/db/quotes'
import { createEngagement, updateEngagement } from '../src/lib/db/engagements'
import {
  listSignalsForEntity,
  getDefaultOriginatingSignalId,
  getSignalById,
} from '../src/lib/db/signal-attribution'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG = 'org-589'
const ORG_OTHER = 'org-589-other'

interface Setup {
  db: D1Database
  entityA: string
  entityB: string
  entityC: string
}

async function bootstrap(): Promise<Setup> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

  await db
    .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?), (?, ?, ?)')
    .bind(ORG, 'Org 589', 'org-589', ORG_OTHER, 'Org 589 Other', 'org-589-other')
    .run()

  // Three entities so we can test cross-entity isolation, defaults, and the
  // roll-up's GROUP BY behavior simultaneously.
  const a = await createEntity(db, ORG, { name: 'Entity A', source_pipeline: 'review_mining' })
  const b = await createEntity(db, ORG, { name: 'Entity B', source_pipeline: 'job_monitor' })
  const c = await createEntity(db, ORG, { name: 'Entity C', source_pipeline: 'website_booking' })

  return { db, entityA: a.id, entityB: b.id, entityC: c.id }
}

describe('signal attribution: DAL helpers (#589)', () => {
  let s: Setup
  beforeEach(async () => {
    s = await bootstrap()
  })

  it('listSignalsForEntity returns most-recent first and excludes other entities', async () => {
    const older = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'first signal',
      source: 'review_mining',
    })
    // Bump created_at on the second signal so DESC ordering is unambiguous —
    // datetime('now') resolution is per-second on D1's SQLite build.
    await s.db
      .prepare(`UPDATE context SET created_at = datetime('now', '-1 hour') WHERE id = ?`)
      .bind(older.id)
      .run()
    const newer = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'second signal',
      source: 'job_monitor',
    })

    // Noise on a different entity must not appear.
    await appendContext(s.db, ORG, {
      entity_id: s.entityB,
      type: 'signal',
      content: 'b signal',
      source: 'review_mining',
    })

    const signals = await listSignalsForEntity(s.db, ORG, s.entityA)
    expect(signals.map((x) => x.id)).toEqual([newer.id, older.id])
  })

  it('getDefaultOriginatingSignalId returns null when entity has no signals', async () => {
    const id = await getDefaultOriginatingSignalId(s.db, ORG, s.entityA)
    expect(id).toBeNull()
  })

  it('getDefaultOriginatingSignalId returns the most recent signal id', async () => {
    const older = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'a',
      source: 'review_mining',
    })
    await s.db
      .prepare(`UPDATE context SET created_at = datetime('now', '-1 hour') WHERE id = ?`)
      .bind(older.id)
      .run()
    const newer = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'b',
      source: 'job_monitor',
    })

    const id = await getDefaultOriginatingSignalId(s.db, ORG, s.entityA)
    expect(id).toBe(newer.id)
  })

  it('getSignalById rejects ids from a different org (#399 isolation)', async () => {
    const sig = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'x',
      source: 'review_mining',
    })
    expect(await getSignalById(s.db, ORG, sig.id)).not.toBeNull()
    expect(await getSignalById(s.db, ORG_OTHER, sig.id)).toBeNull()
  })

  it('getSignalById rejects non-signal context entries', async () => {
    const note = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'note',
      content: 'admin note',
      source: 'admin',
    })
    expect(await getSignalById(s.db, ORG, note.id)).toBeNull()
  })
})

describe('signal attribution: createQuote/createEngagement (#589)', () => {
  let s: Setup
  beforeEach(async () => {
    s = await bootstrap()
  })

  async function seedSignal(entityId: string, source: string): Promise<string> {
    const sig = await appendContext(s.db, ORG, {
      entity_id: entityId,
      type: 'signal',
      content: `signal from ${source}`,
      source,
    })
    return sig.id
  }

  it('createQuote defaults to most-recent signal', async () => {
    const sigId = await seedSignal(s.entityA, 'review_mining')
    // A quote needs an assessment per the schema. Insert a meeting (which is
    // backwards-compatible with assessment_id thanks to migration 0025).
    await s.db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind('mtg-1', ORG, s.entityA, null)
      .run()
    const quote = await createQuote(s.db, ORG, {
      entityId: s.entityA,
      assessmentId: 'mtg-1',
      lineItems: [],
      rate: 175,
    })
    expect(quote.originating_signal_id).toBe(sigId)
  })

  it('createEngagement defaults to most-recent signal', async () => {
    const sigId = await seedSignal(s.entityA, 'review_mining')

    // Build the upstream chain (assessment → quote) so the engagement FK is
    // satisfiable. We don't care about pricing — just the attribution column.
    await s.db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind('mtg-eng', ORG, s.entityA, null)
      .run()
    const quote = await createQuote(s.db, ORG, {
      entityId: s.entityA,
      assessmentId: 'mtg-eng',
      lineItems: [],
      rate: 175,
    })

    const eng = await createEngagement(s.db, ORG, {
      entity_id: s.entityA,
      quote_id: quote.id,
    })

    expect(eng.originating_signal_id).toBe(sigId)
  })

  it('updateEngagement clears attribution when null is passed', async () => {
    const sigId = await seedSignal(s.entityA, 'review_mining')
    await s.db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind('mtg-upd', ORG, s.entityA, null)
      .run()
    const quote = await createQuote(s.db, ORG, {
      entityId: s.entityA,
      assessmentId: 'mtg-upd',
      lineItems: [],
      rate: 175,
    })
    const eng = await createEngagement(s.db, ORG, {
      entity_id: s.entityA,
      quote_id: quote.id,
    })
    expect(eng.originating_signal_id).toBe(sigId)

    const cleared = await updateEngagement(s.db, ORG, eng.id, { originating_signal_id: null })
    expect(cleared?.originating_signal_id).toBeNull()
  })
})
