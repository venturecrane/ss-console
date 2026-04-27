/**
 * Tests for the diagnostic anti-fabrication safeguards (#598):
 *
 *   1. Thin-footprint pre-flight gate — refuses scans that would
 *      otherwise produce a fabricated report.
 *   2. Per-section anti-fabrication renderer — never invents owner
 *      names, competitor lists, or operational claims.
 *
 * The renderer is tested directly against a seeded D1 with realistic
 * enrichment context. The gate is tested through `evaluateThinFootprintGate`
 * after seeding the same enrichment metadata the production code reads.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity, getEntity } from '../src/lib/db/entities'
import { appendContext } from '../src/lib/db/context'
import { evaluateThinFootprintGate } from '../src/lib/diagnostic'
import { renderDiagnosticReport } from '../src/lib/diagnostic/render'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG_ID = 'org-test-diagnostic'

async function freshDb(): Promise<D1Database> {
  const db = createTestD1() as unknown as D1Database
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  await db
    .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(ORG_ID, 'Test Org', 'test-org')
    .run()
  return db
}

describe('evaluateThinFootprintGate', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('refuses when no website AND no places match', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Tacos Chisco',
      area: 'Phoenix, AZ',
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, entity, 'tacoschisco.com')
    expect(r.thin).toBe(true)
    expect(r.reason).toBe('no_website_no_places')
  })

  it('refuses when no website AND <5 reviews', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Thin Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'Google Places matched.',
      metadata: { reviewCount: 2 },
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, entity, 'thinbiz.com')
    expect(r.thin).toBe(true)
    expect(r.reason).toBe('no_website_low_reviews')
  })

  it('proceeds when website is set even with low reviews', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'New HVAC',
      website: 'https://newhvac.com',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'Google Places matched.',
      metadata: { reviewCount: 2 },
    })
    const fresh = await getEntity(db, ORG_ID, entity.id)
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, fresh!, 'newhvac.com')
    expect(r.thin).toBe(false)
  })

  it('proceeds when reviews are sufficient even without a website', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Established Spa',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'Google Places matched.',
      metadata: { reviewCount: 50, rating: 4.7 },
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    // 50 reviews + no website is borderline — the gate proceeds because
    // there's a real Google footprint to read. This matches the scoping
    // doc's intent (don't gate on reviewCount alone if google_places hit,
    // and we have reviews to synthesize).
    const r = await evaluateThinFootprintGate(env, entity, 'establishedspa.com')
    expect(r.thin).toBe(false)
  })
})

describe('renderDiagnosticReport — anti-fabrication', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('omits Business Overview entirely when google_places did not match', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Unknown Biz',
      area: 'Phoenix, AZ',
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const overview = r.sections.find((s) => s.id === 'business_overview')
    expect(overview?.rendered).toBe(false)
    expect(overview?.bullets).toBeUndefined()
  })

  it('renders "not publicly identified" when no owner_name signal exists — never invents', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Anon HVAC',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'site analyzed',
      metadata: {
        owner_name: null,
        team_size: null,
        founding_year: null,
        services: [],
        quality: 'basic',
        tech_stack: { scheduling: [], crm: [], reviews: [], payments: [], communication: [] },
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    expect(profile?.rendered).toBe(true)
    const bullets = profile?.bullets ?? []
    const ownerLine = bullets.find((b) => b.toLowerCase().startsWith('owner'))
    expect(ownerLine).toBeDefined()
    expect(ownerLine).toContain('not publicly identified')
    // Must NOT contain a fabricated proper name (catches the "Hi Owner"
    // / "John Smith"-style invention pattern).
    expect(ownerLine!.split(':')[1]?.trim()).toBe('not publicly identified')
  })

  it('renders authored owner_name when website_analysis provides one', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Authored HVAC',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'site analyzed',
      metadata: {
        owner_name: 'Maria Rodriguez',
        team_size: 8,
        founding_year: 2014,
        services: ['HVAC repair'],
        quality: 'good',
        tech_stack: {
          scheduling: ['ServiceTitan'],
          crm: [],
          reviews: [],
          payments: [],
          communication: [],
        },
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    expect(profile?.rendered).toBe(true)
    const ownerLine = (profile?.bullets ?? []).find((b) => b.toLowerCase().startsWith('owner'))
    expect(ownerLine).toContain('Maria Rodriguez')
  })

  it('omits Engagement Opportunity when <2 medium+ confidence problems', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Single-Signal Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: {
        operational_problems: [
          { problem: 'scheduling chaos', confidence: 'low', evidence: 'one mention' },
        ],
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const opp = r.sections.find((s) => s.id === 'engagement_opportunity')
    expect(opp?.rendered).toBe(false)
    expect(opp?.insufficientDataNote).toBeTruthy()
  })

  it('renders Engagement Opportunity with up to 3 corroborated problems', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Multi-Signal Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: {
        operational_problems: [
          { problem: 'phone tag', confidence: 'high', evidence: '6 reviews mention waiting' },
          { problem: 'no online booking', confidence: 'medium', evidence: 'multiple requests' },
          { problem: 'manual quoting', confidence: 'high', evidence: 'review pattern' },
          { problem: 'pricing visibility', confidence: 'low', evidence: 'one review' },
        ],
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const opp = r.sections.find((s) => s.id === 'engagement_opportunity')
    expect(opp?.rendered).toBe(true)
    // Top 3 only, low-confidence problem omitted
    expect(opp?.bullets?.length).toBeLessThanOrEqual(3)
    const text = (opp?.bullets ?? []).join(' ')
    expect(text).toContain('phone tag')
    expect(text).not.toContain('pricing visibility')
  })

  it('omits Conversation Starters with <3 evidence-anchored facts', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Sparse Biz',
      area: 'Phoenix, AZ',
    })
    // Only 1 fact: review themes.
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: { top_themes: ['friendly staff'] },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const cs = r.sections.find((s) => s.id === 'conversation_starters')
    expect(cs?.rendered).toBe(false)
  })

  it('renders Conversation Starters with >=3 evidence-anchored facts', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Rich Biz',
      website: 'https://rich.com',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'matched',
      metadata: { reviewCount: 122, rating: 4.8 },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: { top_themes: ['quick response'] },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: { services: ['HVAC repair', 'plumbing', 'electrical'] },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const cs = r.sections.find((s) => s.id === 'conversation_starters')
    expect(cs?.rendered).toBe(true)
    expect((cs?.bullets ?? []).length).toBeGreaterThanOrEqual(3)
  })

  it('hasContent === false when zero sections render — falls back to thin-footprint message', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Bare Entity',
      area: 'Phoenix, AZ',
    })
    const r = await renderDiagnosticReport(db, entity, null)
    expect(r.hasContent).toBe(false)
    // None of the sections should claim to render.
    for (const s of r.sections) {
      expect(s.rendered).toBe(false)
    }
  })
})
