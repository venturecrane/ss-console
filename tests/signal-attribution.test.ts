/**
 * Originating-signal attribution tests (#589).
 *
 * Exercises:
 *   - DAL helpers (listSignalsForEntity, getDefaultOriginatingSignalId,
 *     getSignalById, getEngagementsBySourcePipeline)
 *   - createMeeting / createQuote / createEngagement default-resolution
 *     behavior (undefined → most recent signal, null → unattributed,
 *     explicit string → stored as-is)
 *   - update paths persist the new column
 *   - Roll-up query groups attributed engagements by source pipeline
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
import { createMeeting } from '../src/lib/db/meetings'
import { createQuote } from '../src/lib/db/quotes'
import { createEngagement, updateEngagement } from '../src/lib/db/engagements'
import {
  listSignalsForEntity,
  getDefaultOriginatingSignalId,
  getSignalById,
  getEngagementsBySourcePipeline,
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
  const c = await createEntity(db, ORG, { name: 'Entity C', source_pipeline: 'new_business' })

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

describe('signal attribution: createMeeting/createQuote/createEngagement (#589)', () => {
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

  it('createMeeting defaults to most-recent signal when omitted', async () => {
    const sigId = await seedSignal(s.entityA, 'review_mining')
    const meeting = await createMeeting(s.db, ORG, s.entityA, {})
    expect(meeting.originating_signal_id).toBe(sigId)
  })

  it('createMeeting honors explicit null (unattributed)', async () => {
    await seedSignal(s.entityA, 'review_mining')
    const meeting = await createMeeting(s.db, ORG, s.entityA, { originating_signal_id: null })
    expect(meeting.originating_signal_id).toBeNull()
  })

  it('createMeeting stores explicit signal id', async () => {
    const sigId = await seedSignal(s.entityA, 'review_mining')
    // Add a newer signal to prove the explicit override wins over the default.
    await seedSignal(s.entityA, 'job_monitor')
    const meeting = await createMeeting(s.db, ORG, s.entityA, { originating_signal_id: sigId })
    expect(meeting.originating_signal_id).toBe(sigId)
  })

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

describe('signal attribution: getEngagementsBySourcePipeline roll-up (#589)', () => {
  let s: Setup
  beforeEach(async () => {
    s = await bootstrap()
  })

  it('groups engagements by the source pipeline of their attributed signal', async () => {
    // Two engagements from review_mining, one from job_monitor, one
    // unattributed (must be excluded from the buckets).
    const make = async (entityId: string, source: string, hours: number) => {
      const sig = await appendContext(s.db, ORG, {
        entity_id: entityId,
        type: 'signal',
        content: 'sig',
        source,
      })
      const aid = `mtg-${entityId}-${source}-${hours}`
      await s.db
        .prepare(
          `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
           VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
        )
        .bind(aid, ORG, entityId, null)
        .run()
      const q = await createQuote(s.db, ORG, {
        entityId,
        assessmentId: aid,
        lineItems: [],
        rate: 175,
        originatingSignalId: sig.id,
      })
      await createEngagement(s.db, ORG, {
        entity_id: entityId,
        quote_id: q.id,
        estimated_hours: hours,
        originating_signal_id: sig.id,
      })
    }

    await make(s.entityA, 'review_mining', 10)
    await make(s.entityB, 'review_mining', 20)
    await make(s.entityC, 'job_monitor', 5)

    // Unattributed engagement — must not appear in any bucket.
    await s.db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind('mtg-orphan', ORG, s.entityA, null)
      .run()
    const qOrphan = await createQuote(s.db, ORG, {
      entityId: s.entityA,
      assessmentId: 'mtg-orphan',
      lineItems: [],
      rate: 175,
      originatingSignalId: null,
    })
    await createEngagement(s.db, ORG, {
      entity_id: s.entityA,
      quote_id: qOrphan.id,
      estimated_hours: 99,
      originating_signal_id: null,
    })

    const rows = await getEngagementsBySourcePipeline(s.db, ORG)
    const byPipeline = Object.fromEntries(rows.map((r) => [r.source_pipeline, r]))

    expect(byPipeline.review_mining?.engagement_count).toBe(2)
    expect(byPipeline.review_mining?.total_estimated_hours).toBe(30)
    expect(byPipeline.job_monitor?.engagement_count).toBe(1)
    expect(byPipeline.job_monitor?.total_estimated_hours).toBe(5)
    expect(byPipeline.new_business).toBeUndefined()
  })

  it('is org-scoped — engagements from a different org never appear', async () => {
    // Seed in ORG, query ORG_OTHER. Result must be empty.
    const sig = await appendContext(s.db, ORG, {
      entity_id: s.entityA,
      type: 'signal',
      content: 'sig',
      source: 'review_mining',
    })
    await s.db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind('mtg-iso', ORG, s.entityA, null)
      .run()
    const q = await createQuote(s.db, ORG, {
      entityId: s.entityA,
      assessmentId: 'mtg-iso',
      lineItems: [],
      rate: 175,
      originatingSignalId: sig.id,
    })
    await createEngagement(s.db, ORG, {
      entity_id: s.entityA,
      quote_id: q.id,
      originating_signal_id: sig.id,
    })

    const rows = await getEngagementsBySourcePipeline(s.db, ORG_OTHER)
    expect(rows).toEqual([])
  })
})
