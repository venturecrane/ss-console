/**
 * Behavioral coverage for the quote revenue path (2026-08-14 code review,
 * Testing #1): the money math (total_hours, total_price, deposit_amount)
 * and the VALID_TRANSITIONS state machine executed against a real migrated
 * D1 — not source-text matched. tests/quotes.test.ts remains the
 * architecture-guard suite; this file is the one that fails when the
 * computation itself regresses.
 *
 * Send-gating on authored content and the SignWell acceptance guard are
 * covered in tests/quotes-authored-content.test.ts and
 * tests/lifecycle-guards.test.ts; not duplicated here.
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
import {
  createQuote,
  updateQuote,
  updateQuoteStatus,
  parseLineItems,
  VALID_TRANSITIONS,
  type Quote,
  type QuoteStatus,
} from '../src/lib/db/quotes'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG_ID = 'org-money-math'
const ASSESSMENT_ID = 'assess-money-math'

const AUTHORED = {
  schedule: [{ label: 'Phase 1', body: 'Discovery.' }],
  deliverables: [{ title: 'Report', body: 'A written summary.' }],
}

describe('quote money math (real D1)', () => {
  let db: D1Database
  let entityId: string

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Money Math Org', 'money-math-org')
      .run()
    const entity = await createEntity(db, ORG_ID, { name: 'Money Math Biz', stage: 'proposing' })
    entityId = entity.id
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
      )
      .bind(ASSESSMENT_ID, ORG_ID, entityId)
      .run()
  })

  function baseQuote(extra: Partial<Parameters<typeof createQuote>[2]> = {}) {
    return createQuote(db, ORG_ID, {
      entityId,
      assessmentId: ASSESSMENT_ID,
      lineItems: [
        { problem: 'Scheduling chaos', description: 'Design dispatch flow', estimated_hours: 10 },
        { problem: 'Lead follow-up', description: 'CRM configuration', estimated_hours: 15 },
        { problem: 'Reporting', description: 'Weekly numbers view', estimated_hours: 5 },
      ],
      rate: 175,
      originatingSignalId: null,
      ...extra,
    })
  }

  it('createQuote: total_hours sums line items, total_price = hours x rate, deposit defaults to 50%', async () => {
    const q = await baseQuote()
    expect(q.total_hours).toBe(30)
    expect(q.total_price).toBe(30 * 175) // 5250
    expect(q.deposit_pct).toBe(0.5)
    expect(q.deposit_amount).toBe(2625)
    expect(q.status).toBe('draft')
    expect(q.version).toBe(1)
  })

  it('createQuote: explicit depositPct is applied to the computed total', async () => {
    const q = await baseQuote({ depositPct: 0.4 })
    expect(q.deposit_pct).toBe(0.4)
    expect(q.deposit_amount).toBe(5250 * 0.4) // 2100
  })

  it('createQuote: empty line items produce zero totals, not NaN', async () => {
    const q = await baseQuote({ lineItems: [] })
    expect(q.total_hours).toBe(0)
    expect(q.total_price).toBe(0)
    expect(q.deposit_amount).toBe(0)
  })

  it('updateQuote: changing line items alone recomputes totals with the stored rate and pct', async () => {
    const q = await baseQuote()
    const updated = await updateQuote(db, ORG_ID, q.id, {
      lineItems: [{ problem: 'One thing', description: 'Just this', estimated_hours: 8 }],
    })
    expect(updated?.total_hours).toBe(8)
    expect(updated?.total_price).toBe(8 * 175) // 1400
    expect(updated?.deposit_amount).toBe(700) // 50% of new total
    expect(updated?.rate).toBe(175)
    expect(updated?.version).toBe(2) // pricing change bumps version
  })

  it('updateQuote: changing rate alone recomputes against the STORED line items', async () => {
    const q = await baseQuote()
    const updated = await updateQuote(db, ORG_ID, q.id, { rate: 200 })
    expect(updated?.total_hours).toBe(30)
    expect(updated?.total_price).toBe(6000)
    expect(updated?.deposit_amount).toBe(3000)
    expect(parseLineItems(updated!.line_items)).toHaveLength(3)
  })

  it('updateQuote: changing depositPct alone recomputes deposit from the EXISTING total', async () => {
    const q = await baseQuote()
    const updated = await updateQuote(db, ORG_ID, q.id, { depositPct: 0.25 })
    expect(updated?.total_price).toBe(5250) // unchanged
    expect(updated?.deposit_pct).toBe(0.25)
    expect(updated?.deposit_amount).toBe(1312.5)
  })

  it('updateQuote: items + rate + pct together compute from the new values', async () => {
    const q = await baseQuote()
    const updated = await updateQuote(db, ORG_ID, q.id, {
      lineItems: [{ problem: 'P', description: 'D', estimated_hours: 20 }],
      rate: 250,
      depositPct: 0.3,
    })
    expect(updated?.total_hours).toBe(20)
    expect(updated?.total_price).toBe(5000)
    expect(updated?.deposit_amount).toBe(1500)
  })

  it('updateQuote: attribution-only edit changes no totals and does not bump version', async () => {
    const q = await baseQuote()
    const updated = await updateQuote(db, ORG_ID, q.id, { originatingSignalId: null })
    expect(updated?.version).toBe(1)
    expect(updated?.total_price).toBe(5250)
  })

  it('updateQuote: unknown quote id returns null', async () => {
    expect(await updateQuote(db, ORG_ID, 'nope', { rate: 200 })).toBeNull()
  })
})

describe('quote status state machine (real D1)', () => {
  let db: D1Database
  let entityId: string

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Money Math Org', 'money-math-org')
      .run()
    const entity = await createEntity(db, ORG_ID, { name: 'Transitions Biz', stage: 'proposing' })
    entityId = entity.id
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
      )
      .bind(ASSESSMENT_ID, ORG_ID, entityId)
      .run()
  })

  /** A sendable draft: authored content present so the send gate passes. */
  function sendableDraft(): Promise<Quote> {
    return createQuote(db, ORG_ID, {
      entityId,
      assessmentId: ASSESSMENT_ID,
      lineItems: [{ problem: 'P', description: 'D', estimated_hours: 10 }],
      rate: 175,
      originatingSignalId: null,
      ...AUTHORED,
    })
  }

  it('draft -> sent stamps sent_at and expires_at exactly 5 days later', async () => {
    const q = await sendableDraft()
    const before = Date.now()
    const sent = await updateQuoteStatus(db, ORG_ID, q.id, 'sent')
    expect(sent?.status).toBe('sent')
    expect(sent?.sent_at).toBeTruthy()
    expect(sent?.expires_at).toBeTruthy()
    const sentAt = new Date(sent!.sent_at!).getTime()
    expect(sentAt).toBeGreaterThanOrEqual(before - 1000)
    expect(new Date(sent!.expires_at!).getTime() - sentAt).toBe(5 * 24 * 60 * 60 * 1000)
  })

  it('draft -> superseded and sent -> declined/expired are permitted', async () => {
    const a = await sendableDraft()
    expect((await updateQuoteStatus(db, ORG_ID, a.id, 'superseded'))?.status).toBe('superseded')

    const b = await sendableDraft()
    await updateQuoteStatus(db, ORG_ID, b.id, 'sent')
    expect((await updateQuoteStatus(db, ORG_ID, b.id, 'declined'))?.status).toBe('declined')

    const c = await sendableDraft()
    await updateQuoteStatus(db, ORG_ID, c.id, 'sent')
    expect((await updateQuoteStatus(db, ORG_ID, c.id, 'expired'))?.status).toBe('expired')
  })

  it('draft -> accepted is rejected (must go through sent)', async () => {
    const q = await sendableDraft()
    await expect(updateQuoteStatus(db, ORG_ID, q.id, 'accepted')).rejects.toThrow(
      /Invalid status transition: draft -> accepted/
    )
  })

  it('terminal states reject every outbound transition', async () => {
    // Exercise the runtime guard for each terminal status against each
    // target the map declares invalid — the matrix itself, not its source.
    const terminals: QuoteStatus[] = ['declined', 'expired', 'superseded']
    for (const terminal of terminals) {
      const q = await sendableDraft()
      if (terminal === 'superseded') {
        await updateQuoteStatus(db, ORG_ID, q.id, 'superseded')
      } else {
        await updateQuoteStatus(db, ORG_ID, q.id, 'sent')
        await updateQuoteStatus(db, ORG_ID, q.id, terminal)
      }
      expect(VALID_TRANSITIONS[terminal]).toEqual([])
      for (const target of ['draft', 'sent', 'accepted'] as QuoteStatus[]) {
        await expect(updateQuoteStatus(db, ORG_ID, q.id, target)).rejects.toThrow(
          /Invalid status transition/
        )
      }
    }
  })

  it('unknown quote id returns null instead of throwing', async () => {
    expect(await updateQuoteStatus(db, ORG_ID, 'missing', 'sent')).toBeNull()
  })

  it('org scoping: a quote is not transitionable through another org id', async () => {
    const q = await sendableDraft()
    expect(await updateQuoteStatus(db, 'org-other', q.id, 'sent')).toBeNull()
    // And the real row is untouched.
    const still = await db
      .prepare('SELECT status FROM quotes WHERE id = ?')
      .bind(q.id)
      .first<{ status: string }>()
    expect(still?.status).toBe('draft')
  })
})
