/**
 * Authored client-facing content tests for quotes (#377).
 *
 * Exercises:
 *   - parseSchedule / parseDeliverables: null, empty, malformed, populated
 *   - getMissingAuthoredContent: which fields are still empty
 *   - createQuote / updateQuote: round-tripping the new fields
 *   - updateQuoteStatus: send-gating when authored content is missing
 *   - portal page: re-introduces "How we'll work" only when schedule is
 *     populated; falls back to line-items deliverables only when authored
 *     deliverables are missing
 *   - admin page: surfaces authoring UI + send-gate banner
 *   - SOW templateProps: requires authored overview and primary contact name
 *
 * Uses @venturecrane/crane-test-harness for the DAL/round-trip tests and
 * source-string assertions for the render-side checks (matching the pattern
 * in tests/portal-quotes.test.ts).
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import { existsSync, readFileSync } from 'fs'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity, type EntityStage } from '../src/lib/db/entities'
import {
  createQuote,
  getQuote,
  updateQuote,
  updateQuoteStatus,
  parseSchedule,
  parseDeliverables,
  getMissingAuthoredContent,
} from '../src/lib/db/quotes'
import type { Quote } from '../src/lib/db/quotes'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_ID = 'org-test'

function makeQuote(overrides: Partial<Quote> = {}): Quote {
  return {
    id: 'q1',
    org_id: ORG_ID,
    entity_id: 'e1',
    assessment_id: 'a1',
    meeting_id: 'a1',
    version: 1,
    parent_quote_id: null,
    line_items: '[]',
    total_hours: 0,
    rate: 0,
    total_price: 0,
    deposit_pct: 0.5,
    deposit_amount: 0,
    status: 'draft',
    sent_at: null,
    expires_at: null,
    accepted_at: null,
    schedule: null,
    deliverables: null,
    engagement_overview: null,
    milestone_label: null,
    originating_signal_id: null,
    created_at: '2026-04-15T00:00:00.000Z',
    updated_at: '2026-04-15T00:00:00.000Z',
    ...overrides,
  }
}

describe('parseSchedule', () => {
  it('returns empty array when schedule is null', () => {
    expect(parseSchedule(makeQuote({ schedule: null }))).toEqual([])
  })

  it('returns empty array when schedule is empty string', () => {
    expect(parseSchedule(makeQuote({ schedule: '' }))).toEqual([])
  })

  it('returns empty array when schedule is not a JSON array', () => {
    expect(parseSchedule(makeQuote({ schedule: '{"label":"x","body":"y"}' }))).toEqual([])
  })

  it('returns empty array when JSON is malformed', () => {
    expect(parseSchedule(makeQuote({ schedule: 'not json' }))).toEqual([])
  })

  it('returns parsed rows when JSON is a valid array', () => {
    const json = JSON.stringify([
      { label: 'Discovery', body: 'We listen and learn.' },
      { label: 'Build', body: 'We design and ship.' },
    ])
    expect(parseSchedule(makeQuote({ schedule: json }))).toEqual([
      { label: 'Discovery', body: 'We listen and learn.' },
      { label: 'Build', body: 'We design and ship.' },
    ])
  })

  it('drops rows missing a label or body field', () => {
    const json = JSON.stringify([
      { label: 'Good', body: 'ok' },
      { label: 'No body' },
      { body: 'No label' },
      null,
    ])
    expect(parseSchedule(makeQuote({ schedule: json }))).toEqual([{ label: 'Good', body: 'ok' }])
  })
})

describe('parseDeliverables', () => {
  it('returns empty array when deliverables is null', () => {
    expect(parseDeliverables(makeQuote({ deliverables: null }))).toEqual([])
  })

  it('returns empty array when JSON is malformed', () => {
    expect(parseDeliverables(makeQuote({ deliverables: 'not json' }))).toEqual([])
  })

  it('returns parsed rows when JSON is a valid array', () => {
    const json = JSON.stringify([
      { title: 'New CRM', body: 'HubSpot, configured.' },
      { title: 'SOPs', body: 'Documented.' },
    ])
    expect(parseDeliverables(makeQuote({ deliverables: json }))).toEqual([
      { title: 'New CRM', body: 'HubSpot, configured.' },
      { title: 'SOPs', body: 'Documented.' },
    ])
  })

  it('drops rows missing a title or body field', () => {
    const json = JSON.stringify([
      { title: 'Good', body: 'ok' },
      { title: 'No body' },
      { body: 'No title' },
    ])
    expect(parseDeliverables(makeQuote({ deliverables: json }))).toEqual([
      { title: 'Good', body: 'ok' },
    ])
  })
})

describe('getMissingAuthoredContent', () => {
  it('reports both fields missing when null', () => {
    expect(getMissingAuthoredContent(makeQuote())).toEqual(['schedule', 'deliverables'])
  })

  it('reports only schedule missing when deliverables are populated', () => {
    expect(
      getMissingAuthoredContent(
        makeQuote({
          deliverables: JSON.stringify([{ title: 'x', body: 'y' }]),
        })
      )
    ).toEqual(['schedule'])
  })

  it('returns empty array when both fields are populated', () => {
    expect(
      getMissingAuthoredContent(
        makeQuote({
          schedule: JSON.stringify([{ label: 'x', body: 'y' }]),
          deliverables: JSON.stringify([{ title: 'x', body: 'y' }]),
        })
      )
    ).toEqual([])
  })

  it('treats an empty array as missing', () => {
    expect(
      getMissingAuthoredContent(
        makeQuote({
          schedule: '[]',
          deliverables: '[]',
        })
      )
    ).toEqual(['schedule', 'deliverables'])
  })
})

describe('quotes DAL: authored content round-trip', () => {
  let db: D1Database
  let entityId: string

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Test Org', 'test-org')
      .run()

    const entity = await createEntity(db, ORG_ID, {
      name: 'Authored Content Biz',
      stage: 'proposing' as EntityStage,
    })
    entityId = entity.id

    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
      )
      .bind('assess-authored', ORG_ID, entityId)
      .run()
  })

  it('createQuote leaves authored fields null by default', async () => {
    const quote = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-authored',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
    })
    expect(quote.schedule).toBeNull()
    expect(quote.deliverables).toBeNull()
    expect(quote.engagement_overview).toBeNull()
    expect(quote.milestone_label).toBeNull()
  })

  it('createQuote persists authored fields when provided', async () => {
    const quote = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-authored',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      schedule: [{ label: 'Phase 1', body: 'Discovery.' }],
      deliverables: [{ title: 'Audit report', body: 'A written summary.' }],
      engagementOverview: 'Operations cleanup for Authored Content Biz.',
      milestoneLabel: 'pilot rollout',
    })
    expect(parseSchedule(quote)).toEqual([{ label: 'Phase 1', body: 'Discovery.' }])
    expect(parseDeliverables(quote)).toEqual([
      { title: 'Audit report', body: 'A written summary.' },
    ])
    expect(quote.engagement_overview).toBe('Operations cleanup for Authored Content Biz.')
    expect(quote.milestone_label).toBe('pilot rollout')
  })

  it('updateQuote persists schedule and deliverables', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-authored',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
    })

    const updated = await updateQuote(db, ORG_ID, created.id, {
      schedule: [{ label: 'Week 1', body: 'Real authored copy.' }],
      deliverables: [{ title: 'Real deliverable', body: 'Authored body.' }],
      engagementOverview: 'Authored overview.',
      milestoneLabel: 'first review',
    })
    expect(updated).not.toBeNull()
    expect(parseSchedule(updated!)).toEqual([{ label: 'Week 1', body: 'Real authored copy.' }])
    expect(parseDeliverables(updated!)).toEqual([
      { title: 'Real deliverable', body: 'Authored body.' },
    ])
    expect(updated!.engagement_overview).toBe('Authored overview.')
    expect(updated!.milestone_label).toBe('first review')
  })

  it('updateQuote clears authored fields when an empty array is passed', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-authored',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      schedule: [{ label: 'a', body: 'b' }],
      deliverables: [{ title: 'a', body: 'b' }],
    })

    const cleared = await updateQuote(db, ORG_ID, created.id, {
      schedule: [],
      deliverables: [],
    })
    expect(cleared!.schedule).toBeNull()
    expect(cleared!.deliverables).toBeNull()
  })

  it('updateQuote bumps version when authored content changes', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-authored',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
    })
    expect(created.version).toBe(1)

    const updated = await updateQuote(db, ORG_ID, created.id, {
      schedule: [{ label: 'a', body: 'b' }],
    })
    expect(updated!.version).toBe(2)
  })
})

describe('updateQuoteStatus: send-gating on authored content', () => {
  let db: D1Database
  let entityId: string

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Test Org', 'test-org')
      .run()

    const entity = await createEntity(db, ORG_ID, {
      name: 'Send Gate Biz',
      stage: 'proposing' as EntityStage,
    })
    entityId = entity.id

    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
      )
      .bind('assess-gate', ORG_ID, entityId)
      .run()
  })

  it('blocks transition to sent when schedule is missing', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      deliverables: [{ title: 'a', body: 'b' }],
    })

    await expect(updateQuoteStatus(db, ORG_ID, created.id, 'sent')).rejects.toThrow(
      /missing authored client-facing content.*schedule/
    )
  })

  it('blocks transition to sent when deliverables are missing', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      schedule: [{ label: 'a', body: 'b' }],
    })

    await expect(updateQuoteStatus(db, ORG_ID, created.id, 'sent')).rejects.toThrow(
      /missing authored client-facing content.*deliverables/
    )
  })

  it('lists both fields when both are missing', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
    })

    await expect(updateQuoteStatus(db, ORG_ID, created.id, 'sent')).rejects.toThrow(
      /schedule, deliverables/
    )
  })

  it('allows transition to sent once both fields are authored', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      schedule: [{ label: 'a', body: 'b' }],
      deliverables: [{ title: 'a', body: 'b' }],
    })

    const sent = await updateQuoteStatus(db, ORG_ID, created.id, 'sent')
    expect(sent).not.toBeNull()
    expect(sent!.status).toBe('sent')
    expect(sent!.sent_at).not.toBeNull()
    expect(sent!.expires_at).not.toBeNull()
  })

  it('does not gate the superseded transition (no client-facing surface)', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
    })

    const superseded = await updateQuoteStatus(db, ORG_ID, created.id, 'superseded')
    expect(superseded).not.toBeNull()
    expect(superseded!.status).toBe('superseded')
  })

  it('rejects send when authored arrays were cleared back to empty', async () => {
    const created = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: 'assess-gate',
      lineItems: [{ problem: 'X', description: 'Y', estimated_hours: 5 }],
      rate: 200,
      schedule: [{ label: 'a', body: 'b' }],
      deliverables: [{ title: 'a', body: 'b' }],
    })

    const refreshed = await getQuote(db, ORG_ID, created.id)
    expect(parseSchedule(refreshed!).length).toBeGreaterThan(0)

    await updateQuote(db, ORG_ID, created.id, { schedule: [], deliverables: [] })
    await expect(updateQuoteStatus(db, ORG_ID, created.id, 'sent')).rejects.toThrow(
      /schedule, deliverables/
    )
  })
})

describe('portal proposal page: render gating', () => {
  const source = () => readFileSync(resolve('src/pages/portal/quotes/[id].astro'), 'utf-8')

  it('imports parseSchedule and parseDeliverables', () => {
    const code = source()
    expect(code).toContain('parseSchedule')
    expect(code).toContain('parseDeliverables')
  })

  it('reintroduces the "How we\'ll work" section gated on schedule.length', () => {
    const code = source()
    expect(code).toContain("How we'll work")
    expect(code).toContain('schedule.length > 0')
  })

  it('does not contain the pre-#378 hardcoded weekly schedule', () => {
    const code = source()
    expect(code).not.toContain('We shadow and observe')
    expect(code).not.toContain('We redesign together')
    expect(code).not.toMatch(/Week 1.*Week 2.*Week 3/s)
  })

  it('never derives deliverables from line items (#398)', () => {
    const code = source()
    // Deliverables come exclusively from parseDeliverables(quote). The page
    // must not construct deliverable rows from line_items — line items are
    // pricing/problem data, not authored client-facing content.
    expect(code).not.toMatch(/lineItems\.map\(\s*\(?\s*item/)
    expect(code).not.toContain('authoredDeliverables')
    expect(code).not.toContain('getProblemLabel')
  })

  it('gates the deliverables section on deliverables.length > 0', () => {
    const code = source()
    // Parallel to the schedule section gate — empty = render nothing, not
    // an empty section header.
    expect(code).toContain('deliverables.length > 0')
  })

  it('iterates schedule rows by label and body, not hardcoded week numbers', () => {
    const code = source()
    expect(code).toContain('schedule.map')
    expect(code).toContain('row.label')
    expect(code).toContain('row.body')
  })

  it('never synthesizes "Kickoff next: {scope_summary}" next-step copy (#398)', () => {
    const code = source()
    // scope_summary is an internal operations field, not authored next-step
    // language. The previous synthesis turned it into a client-facing
    // commitment. Only authored next_touchpoint_label feeds nextStepText.
    expect(code).not.toContain('Kickoff next:')
    expect(code).not.toMatch(/engagement\.scope_summary\s*\?/)
  })
})

describe('admin quote builder: authoring UI + send gate', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/entities/[id]/quotes/[quoteId].astro'), 'utf-8')

  it('imports parsing helpers', () => {
    const code = source()
    expect(code).toContain('parseSchedule')
    expect(code).toContain('parseDeliverables')
    expect(code).toContain('getMissingAuthoredContent')
  })

  it('renders schedule row editor', () => {
    const code = source()
    expect(code).toContain('add-schedule-row-btn')
    expect(code).toContain('schedule-label')
    expect(code).toContain('schedule-body')
  })

  it('renders deliverable row editor', () => {
    const code = source()
    expect(code).toContain('add-deliverable-row-btn')
    expect(code).toContain('deliverable-title')
    expect(code).toContain('deliverable-body')
  })

  it('renders engagement-overview textarea', () => {
    const code = source()
    expect(code).toContain('engagement-overview-input')
    expect(code).toContain('Engagement overview')
  })

  it('renders milestone-label input behind isThreeMilestone gate', () => {
    const code = source()
    expect(code).toContain('milestone-label-input')
    expect(code).toContain('isThreeMilestone &&')
  })

  it('shows missing-authored-content banner when fields are empty', () => {
    const code = source()
    expect(code).toContain('missingAuthored')
    expect(code).toContain('Required to send')
  })

  it('hidden fields submit schedule, deliverables, overview, milestone_label', () => {
    const code = source()
    expect(code).toContain('save-schedule')
    expect(code).toContain('save-deliverables')
    expect(code).toContain('save-engagement-overview')
    expect(code).toContain('save-milestone-label')
  })

  it('serializes authored rows into hidden inputs before submit', () => {
    const code = source()
    expect(code).toContain('syncAuthoredContentInputs')
    expect(code).toContain('getScheduleRows')
    expect(code).toContain('getDeliverableRows')
  })
})

describe('SOW templateProps: blocks fabricated fallbacks (#377)', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/quotes/[id].ts'), 'utf-8')

  it('does not contain the "Operations cleanup engagement" hardcoded overview', () => {
    expect(source()).not.toContain('Operations cleanup engagement as discussed during assessment')
  })

  it('does not contain the "Business Owner" contact-name fallback', () => {
    expect(source()).not.toContain("primaryContact?.name ?? 'Business Owner'")
  })

  it('does not contain the "mid-engagement milestone" hardcoded label', () => {
    const code = source()
    // The literal string appears only as a comparison in a test-style guard,
    // never as a default assigned to milestoneLabel.
    expect(code).not.toMatch(/milestoneLabel: ['"]mid-engagement milestone['"]/)
  })

  it('reads engagement_overview from the persisted quote row', () => {
    const code = source()
    expect(code).toContain('existing.engagement_overview')
  })

  it('reads milestone_label from the persisted quote row', () => {
    const code = source()
    expect(code).toContain('existing.milestone_label')
  })

  it('uses authored deliverables for SOW items when present', () => {
    const code = source()
    expect(code).toContain('parseDeliverables')
    expect(code).toContain('authoredDeliverables')
  })

  it('blocks SOW generation when engagement_overview is not authored', () => {
    const code = source()
    expect(code).toContain('Cannot generate SOW: author the engagement overview')
  })

  it('blocks SOW generation when no primary contact name exists', () => {
    const code = source()
    expect(code).toContain('Cannot generate SOW: add a primary contact')
  })
})

describe('sow service: send-gating on signature send', () => {
  const source = () => readFileSync(resolve('src/lib/sow/service.ts'), 'utf-8')

  it('imports getMissingAuthoredContent', () => {
    expect(source()).toContain('getMissingAuthoredContent')
  })

  it('blocks authorizeAndSendSOW when authored content is missing', () => {
    const code = source()
    expect(code).toContain(
      'Cannot send quote for signature: missing authored client-facing content'
    )
  })
})

describe('migration 0021: authored content columns', () => {
  const path = resolve('migrations/0021_quotes_authored_content.sql')

  it('migration file exists', () => {
    expect(existsSync(path)).toBe(true)
  })

  it('adds the four required columns to quotes', () => {
    const sql = readFileSync(path, 'utf-8')
    expect(sql).toContain('ALTER TABLE quotes ADD COLUMN schedule TEXT')
    expect(sql).toContain('ALTER TABLE quotes ADD COLUMN deliverables TEXT')
    expect(sql).toContain('ALTER TABLE quotes ADD COLUMN engagement_overview TEXT')
    expect(sql).toContain('ALTER TABLE quotes ADD COLUMN milestone_label TEXT')
  })

  it('runs cleanly against the test harness D1', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    // Verify each new column appears in the table schema.
    const schema = await db
      .prepare("SELECT sql FROM sqlite_master WHERE type='table' AND name='quotes'")
      .first<{ sql: string }>()
    expect(schema?.sql ?? '').toContain('schedule')
    expect(schema?.sql ?? '').toContain('deliverables')
    expect(schema?.sql ?? '').toContain('engagement_overview')
    expect(schema?.sql ?? '').toContain('milestone_label')
  })
})

describe('backfill decision doc', () => {
  it('exists at the expected path', () => {
    expect(existsSync(resolve('docs/decisions/quotes-authored-content-backfill.md'))).toBe(true)
  })

  it('lists all three options and gives a recommendation', () => {
    const md = readFileSync(resolve('docs/decisions/quotes-authored-content-backfill.md'), 'utf-8')
    expect(md).toMatch(/Option A/)
    expect(md).toMatch(/Option B/)
    expect(md).toMatch(/Option C/)
    expect(md).toMatch(/Recommendation/)
  })
})
