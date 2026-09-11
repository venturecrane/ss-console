/**
 * The retainer price and the payment rail land together or not at all
 * (code review 2026-09-10, Architecture 8).
 *
 * The client-hub form posts both fields at once. Written as two
 * read-modify-writes, a failure between them left the row with the new price
 * and the old rail, and the rail is what decides whether the 3% card fee line
 * is added to checkout and to every monthly invoice. The route now calls one
 * function that issues ONE statement.
 *
 * What would make these red: the write going back to two statements (the
 * statement count assertion), a rail write landing without its price or the
 * reverse (the injected-failure case), or `method = null` clobbering an
 * authored rail (the COALESCE case).
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import path from 'node:path'
import {
  getOperatorServiceForEntity,
  setOperatorPriceAndPaymentMethod,
} from '../src/lib/db/services'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const ORG = 'org-atomic'
const ENTITY = 'ent-atomic'

async function seed(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (?, 'T', 't-atomic', datetime('now'), datetime('now'))`
    )
    .bind(ORG)
    .run()
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'engaged', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ENTITY, ORG, ENTITY, ENTITY)
    .run()
}

/**
 * A D1 wrapper that counts every statement touching `services` and, when
 * armed, makes the next one fail before it runs. The row is then read back
 * from the REAL database to prove nothing half-landed.
 */
function instrument(db: D1Database): {
  db: D1Database
  writes: () => string[]
  failNextServicesWrite: () => void
} {
  const seen: string[] = []
  let fail = false
  const wrapped = new Proxy(db, {
    get(target, prop, receiver) {
      if (prop !== 'prepare') return Reflect.get(target, prop, receiver)
      return (sql: string) => {
        const isWrite = /^\s*(UPDATE|INSERT)\s+(INTO\s+)?services\b/i.test(sql)
        if (isWrite) seen.push(sql.trim().split(/\s+/).slice(0, 2).join(' '))
        if (isWrite && fail) {
          fail = false
          throw new Error('injected: services write refused')
        }
        return target.prepare(sql)
      }
    },
  })
  return {
    db: wrapped,
    writes: () => seen,
    failNextServicesWrite: () => {
      fail = true
    },
  }
}

describe('setOperatorPriceAndPaymentMethod', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(db)
  })

  it('creates the operator service with both fields in one INSERT', async () => {
    const probe = instrument(db)
    const row = await setOperatorPriceAndPaymentMethod(probe.db, ORG, ENTITY, 5000, 'card')
    expect(row.recurring_price).toBe(5000)
    expect(row.payment_method).toBe('card')
    expect(probe.writes()).toEqual(['INSERT INTO'])
  })

  it('updates both fields in ONE statement, never two', async () => {
    await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, 5000, 'ach')
    const probe = instrument(db)
    const row = await setOperatorPriceAndPaymentMethod(probe.db, ORG, ENTITY, 6000, 'card')
    expect(row.recurring_price).toBe(6000)
    expect(row.payment_method).toBe('card')
    expect(probe.writes()).toEqual(['UPDATE services'])
  })

  it('a failed write leaves BOTH fields exactly as they were', async () => {
    await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, 5000, 'ach')
    const probe = instrument(db)
    probe.failNextServicesWrite()
    await expect(
      setOperatorPriceAndPaymentMethod(probe.db, ORG, ENTITY, 6000, 'card')
    ).rejects.toThrow('injected')
    const row = await getOperatorServiceForEntity(db, ORG, ENTITY)
    expect(row?.recurring_price).toBe(5000)
    expect(row?.payment_method).toBe('ach')
  })

  it('a form without a rail rewrites the price and keeps the authored rail', async () => {
    await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, 5000, 'card')
    const row = await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, 4500, null)
    expect(row.recurring_price).toBe(4500)
    expect(row.payment_method).toBe('card')
  })

  it('clearing the price (null) keeps the rail', async () => {
    await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, 5000, 'card')
    const row = await setOperatorPriceAndPaymentMethod(db, ORG, ENTITY, null, null)
    expect(row.recurring_price).toBeNull()
    expect(row.payment_method).toBe('card')
  })
})

describe('the admin route uses the single-statement writer', () => {
  it('calls setOperatorPriceAndPaymentMethod and neither of the two old writers', async () => {
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(
      path.resolve(__dirname, '../src/pages/api/admin/clients/[id]/operator-price.ts'),
      'utf8'
    )
    expect(source).toContain('setOperatorPriceAndPaymentMethod(')
    expect(source).not.toMatch(/\bsetOperatorPrice\(/)
    expect(source).not.toMatch(/\bsetOperatorPaymentMethod\(/)
  })
})
