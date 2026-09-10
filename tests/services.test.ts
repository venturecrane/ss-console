import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import path from 'node:path'
import {
  createService,
  getService,
  listServices,
  projectConsultingStatus,
  projectOperatorStatus,
  findSpineDrift,
  hasSpineDrift,
  setOperatorPrice,
  setOperatorPaymentMethod,
  operatorPaymentMethod,
  isOperatorPaymentMethod,
  getOperatorServiceForEntity,
} from '../src/lib/db/services'
import { createEngagement } from '../src/lib/db/engagements'
import type { Service } from '../src/lib/db/services'
import { createQuote } from '../src/lib/db/quotes'

const migrationsDir = path.resolve(__dirname, '../migrations')
const ORG = 'org-test'

/** Direct read of an entity's services, newest first. */
async function servicesForEntity(db: D1Database, entityId: string): Promise<Service[]> {
  const result = await db
    .prepare('SELECT * FROM services WHERE org_id = ? AND entity_id = ? ORDER BY created_at DESC')
    .bind(ORG, entityId)
    .all<Service>()
  return result.results
}

async function seed(db: D1Database) {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, 'T', 't', datetime('now'), datetime('now'))`
    )
    .bind(ORG)
    .run()
  for (const e of ['ent-1', 'ent-2']) {
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'engaged', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(e, ORG, e, e)
      .run()
  }
}

/** Real assessment+quote so an engagement can satisfy its FK to quotes(id). */
async function seedQuote(db: D1Database, entityId: string): Promise<string> {
  const assessmentId = `mtg-${entityId}`
  await db
    .prepare(
      `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at) VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
    )
    .bind(assessmentId, ORG, entityId, null)
    .run()
  const quote = await createQuote(db, ORG, {
    entityId,
    assessmentId,
    lineItems: [],
    rate: 175,
  })
  return quote.id
}

describe('services DAL', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('createService writes a svc_-prefixed row scoped to the org', async () => {
    const svc = await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'consulting',
      cadence: 'one_time',
      quote_id: 'q1',
      status: 'active',
    })
    expect(svc.id.startsWith('svc_')).toBe(true)
    expect(svc.type).toBe('consulting')
    expect(svc.status).toBe('active')
    expect(svc.recurring_price).toBeNull()

    const fetched = await getService(db, ORG, svc.id)
    expect(fetched?.id).toBe(svc.id)
    // org isolation
    expect(await getService(db, 'other-org', svc.id)).toBeNull()
  })

  it('createService stores an operator recurring price', async () => {
    const svc = await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'operator',
      cadence: 'recurring',
      recurring_price: 1200,
      status: 'active',
    })
    expect(svc.cadence).toBe('recurring')
    expect(svc.recurring_price).toBe(1200)
  })

  it('the operator partial-unique index allows one operator + many consulting per entity', async () => {
    await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'operator',
      cadence: 'recurring',
      status: 'active',
    })
    // a second operator for the same entity must fail
    await expect(
      createService(db, ORG, {
        entity_id: 'ent-1',
        type: 'operator',
        cadence: 'recurring',
        status: 'active',
      })
    ).rejects.toThrow()
    // multiple consulting services for the same entity are fine
    await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'consulting',
      cadence: 'one_time',
      status: 'active',
    })
    await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'consulting',
      cadence: 'one_time',
      status: 'completed',
    })
    const all = await servicesForEntity(db, 'ent-1')
    expect(all.filter((s) => s.type === 'consulting')).toHaveLength(2)
    expect(all.filter((s) => s.type === 'operator')).toHaveLength(1)
  })

  it('listServices filters by type and status', async () => {
    await createService(db, ORG, {
      entity_id: 'ent-1',
      type: 'consulting',
      cadence: 'one_time',
      status: 'active',
    })
    await createService(db, ORG, {
      entity_id: 'ent-2',
      type: 'consulting',
      cadence: 'one_time',
      status: 'completed',
    })
    expect(await listServices(db, ORG, { type: 'consulting' })).toHaveLength(2)
    expect(await listServices(db, ORG, { status: 'active' })).toHaveLength(1)
    expect(await listServices(db, ORG, { type: 'operator' })).toHaveLength(0)
  })
})

describe('status projection (must match the backfill SQL CASE in 0069/0070)', () => {
  it('consulting: completed→completed, cancelled→churned, else→active', () => {
    expect(projectConsultingStatus('completed')).toBe('completed')
    expect(projectConsultingStatus('cancelled')).toBe('churned')
    for (const s of ['scheduled', 'active', 'handoff', 'safety_net']) {
      expect(projectConsultingStatus(s)).toBe('active')
    }
  })
  it('operator: cancelled→churned, else→active', () => {
    expect(projectOperatorStatus('cancelled')).toBe('churned')
    for (const s of ['provisioning', 'active', 'paused']) {
      expect(projectOperatorStatus(s)).toBe('active')
    }
  })
})

describe('createEngagement spawns a linked service (ADR 0046 Stage 1b)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('creates a consulting service parent and links the engagement 1:1', async () => {
    const quoteId = await seedQuote(db, 'ent-1')
    const eng = await createEngagement(db, ORG, { entity_id: 'ent-1', quote_id: quoteId })

    expect(eng.service_id).toBeTruthy()
    expect(eng.service_id?.startsWith('svc_')).toBe(true)

    const services = await servicesForEntity(db, 'ent-1')
    expect(services).toHaveLength(1)
    expect(services[0].id).toBe(eng.service_id)
    expect(services[0].type).toBe('consulting')
    expect(services[0].cadence).toBe('one_time')
    // born active — projectConsultingStatus('scheduled') === 'active'
    expect(services[0].status).toBe('active')
    expect(services[0].quote_id).toBe(quoteId)
  })
})

describe('findSpineDrift — consulting classes (ADR 0046)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('returns empty when every engagement has its service parent', async () => {
    const quoteId = await seedQuote(db, 'ent-1')
    await createEngagement(db, ORG, { entity_id: 'ent-1', quote_id: quoteId })

    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(false)
    expect(drift.orphanEngagements).toHaveLength(0)
    expect(drift.childlessServices).toHaveLength(0)
  })

  it('flags an in-flight engagement with no service parent and a childless service', async () => {
    const quoteId = await seedQuote(db, 'ent-1')
    // orphan engagement: in-flight, service_id NULL (the pre-fix corruption shape)
    await db
      .prepare(
        `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, created_at, updated_at) VALUES ('eng-orphan', ?, 'ent-1', ?, 'active', datetime('now'), datetime('now'))`
      )
      .bind(ORG, quoteId)
      .run()
    // childless service: active consulting with no engagement pointing back
    const childless = await createService(db, ORG, {
      entity_id: 'ent-2',
      type: 'consulting',
      cadence: 'one_time',
      status: 'active',
    })

    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(true)
    expect(drift.orphanEngagements.map((e) => e.id)).toContain('eng-orphan')
    expect(drift.childlessServices.map((s) => s.id)).toContain(childless.id)
  })

  it('does not flag a completed engagement or a completed service', async () => {
    const quoteId = await seedQuote(db, 'ent-1')
    // completed engagement with no service_id — terminal, not in-flight, so ignored
    await db
      .prepare(
        `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, created_at, updated_at) VALUES ('eng-done', ?, 'ent-1', ?, 'completed', datetime('now'), datetime('now'))`
      )
      .bind(ORG, quoteId)
      .run()
    // completed consulting service with no child — only 'active' counts as childless drift
    await createService(db, ORG, {
      entity_id: 'ent-2',
      type: 'consulting',
      cadence: 'one_time',
      status: 'completed',
    })

    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(false)
  })
})

/** Seed a live operator (`customer_configs` row) for an entity. */
async function seedConfig(db: D1Database, entityId: string, slug: string) {
  await db
    .prepare(
      `INSERT INTO customer_configs (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1', '[]', 'abc', datetime('now'))`
    )
    .bind(entityId, ORG, slug)
    .run()
}

describe('setOperatorPrice (ADR 0046 operator arc)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('creates an active operator service when none exists', async () => {
    const svc = await setOperatorPrice(db, ORG, 'ent-1', 1200)
    expect(svc.type).toBe('operator')
    expect(svc.cadence).toBe('recurring')
    expect(svc.status).toBe('active')
    expect(svc.recurring_price).toBe(1200)
    expect(svc.id.startsWith('svc_')).toBe(true)

    const fetched = await getOperatorServiceForEntity(db, ORG, 'ent-1')
    expect(fetched?.id).toBe(svc.id)
  })

  it('updates the price on an existing operator service (no second row)', async () => {
    const first = await setOperatorPrice(db, ORG, 'ent-1', 1000)
    const second = await setOperatorPrice(db, ORG, 'ent-1', 1500)
    expect(second.id).toBe(first.id) // same row, no duplicate
    expect(second.recurring_price).toBe(1500)
    const all = await servicesForEntity(db, 'ent-1')
    expect(all.filter((s) => s.type === 'operator')).toHaveLength(1)
  })

  it('clears the price to null (unpriced) without deleting the service', async () => {
    await setOperatorPrice(db, ORG, 'ent-1', 900)
    const cleared = await setOperatorPrice(db, ORG, 'ent-1', null)
    expect(cleared.recurring_price).toBeNull()
    expect(cleared.status).toBe('active')
  })
})

describe('setOperatorPaymentMethod (migration 0113, agreement §3.8)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('a priced service is born on ACH (no fee) and reads as such', async () => {
    const svc = await setOperatorPrice(db, ORG, 'ent-1', 5000)
    expect(svc.payment_method).toBe('ach')
    expect(operatorPaymentMethod(svc)).toBe('ach')
    expect(operatorPaymentMethod(null)).toBe('ach')
  })

  it('authors card on the same row the price lives on, and back to ach', async () => {
    const priced = await setOperatorPrice(db, ORG, 'ent-1', 5000)
    const card = await setOperatorPaymentMethod(db, ORG, 'ent-1', 'card')
    expect(card.id).toBe(priced.id)
    expect(card.payment_method).toBe('card')
    expect(card.recurring_price).toBe(5000)
    expect(operatorPaymentMethod(card)).toBe('card')
    const ach = await setOperatorPaymentMethod(db, ORG, 'ent-1', 'ach')
    expect(ach.payment_method).toBe('ach')
    expect(
      (await servicesForEntity(db, 'ent-1')).filter((s) => s.type === 'operator')
    ).toHaveLength(1)
  })

  it('a rail authored before any price creates the (unpriced) operator service', async () => {
    const svc = await setOperatorPaymentMethod(db, ORG, 'ent-2', 'card')
    expect(svc.type).toBe('operator')
    expect(svc.recurring_price).toBeNull()
    expect(svc.payment_method).toBe('card')
    const priced = await setOperatorPrice(db, ORG, 'ent-2', 4000)
    expect(priced.id).toBe(svc.id)
    expect(priced.payment_method).toBe('card')
  })

  it('anything unrecognised in the column reads as ach', () => {
    expect(operatorPaymentMethod({ payment_method: 'crypto' })).toBe('ach')
    expect(isOperatorPaymentMethod('card')).toBe(true)
    expect(isOperatorPaymentMethod('CARD')).toBe(false)
  })
})

describe('findSpineDrift — operator classes (ADR 0046)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('is clean when a live operator has a commercial service', async () => {
    await seedConfig(db, 'ent-1', 'ent-1')
    await setOperatorPrice(db, ORG, 'ent-1', 1200)
    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(false)
  })

  it('flags a live operator (config) with no commercial service', async () => {
    await seedConfig(db, 'ent-1', 'acme')
    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(true)
    expect(drift.configsWithoutService.map((c) => c.customer_slug)).toContain('acme')
  })

  it('flags an active operator service whose entity has no config', async () => {
    const svc = await setOperatorPrice(db, ORG, 'ent-1', 1200) // creates service, no config seeded
    const drift = await findSpineDrift(db, ORG)
    expect(hasSpineDrift(drift)).toBe(true)
    expect(drift.operatorServicesWithoutConfig.map((s) => s.id)).toContain(svc.id)
  })
})
