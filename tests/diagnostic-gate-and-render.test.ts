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
import {
  renderDiagnosticReport,
  resolveDisplayName,
  isLikelyBusinessName,
  humanizeProblemId,
  humanizeThemeKey,
} from '../src/lib/diagnostic/render'
import {
  guardPlacesByDomain,
  isStrictDomainMatch,
  normalizeHost,
} from '../src/lib/diagnostic/places-guard'
import type { PlacesEnrichment } from '../src/lib/enrichment/google-places'

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

  it('renders "Insufficient data" placeholder when no owner_name signal exists — never invents (#616)', async () => {
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
    // #616 issue 2 — explicit "Insufficient data" wording, not the
    // older "not publicly identified" phrasing.
    expect(ownerLine).toContain('Insufficient data')
    expect(ownerLine).toContain("we'll surface this in conversation")
    // Must NOT contain a fabricated proper name (catches the "Hi Owner"
    // / "John Smith"-style invention pattern).
    expect(ownerLine).not.toMatch(/[A-Z][a-z]+\s+[A-Z][a-z]+/)
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
    // Certifications from deep_website provide the third evidence-anchored
    // fact (issue #616 removed the duplicated rating from this section, so
    // facts now come from synthesis themes + services + certs / specialties).
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'deep_website',
      content: 'deep',
      metadata: { business_profile: { certifications: ['NATE-certified'] } },
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

// ---------------------------------------------------------------------------
// #612 — strict domain-match guard
//
// The bug exemplar: submitting `venturecrane.com` returned Phoenix-area
// "Sunrise Crane" (sunrisecrane.com) from Google Places. Without a guard,
// that wrong business's phone + website wrote into the entity row, and
// every downstream module ran against sunrisecrane.com. We test:
//
//   - normalizeHost / isStrictDomainMatch units (the comparator)
//   - guardPlacesByDomain returns null on a wrong-match Places result
//   - guardPlacesByDomain passes a strict match through
//   - guardPlacesByDomain treats www-subdomain as a match
//   - guardPlacesByDomain treats multi-level subdomain as NOT a match
//   - the gate trips with reason='no_strict_places_match' when the
//     orchestrator passes placesStrictMatched=false
// ---------------------------------------------------------------------------

describe('normalizeHost', () => {
  it('returns lowercased host for a bare domain', () => {
    expect(normalizeHost('Example.com')).toBe('example.com')
  })

  it('strips https:// and trailing path/slash', () => {
    expect(normalizeHost('https://Example.com/path/?q=1')).toBe('example.com')
    expect(normalizeHost('http://example.com/')).toBe('example.com')
  })

  it('strips www. prefix only at the leftmost label', () => {
    expect(normalizeHost('www.example.com')).toBe('example.com')
    // wwwfoo is part of the host label, not a leading 'www.' prefix
    expect(normalizeHost('wwwfoo.example.com')).toBe('wwwfoo.example.com')
  })

  it('preserves multi-level subdomains', () => {
    expect(normalizeHost('scan.example.com')).toBe('scan.example.com')
    expect(normalizeHost('https://www.app.example.com')).toBe('app.example.com')
  })

  it('returns null for invalid or empty input', () => {
    expect(normalizeHost('')).toBeNull()
    expect(normalizeHost(null)).toBeNull()
    expect(normalizeHost(undefined)).toBeNull()
    // Single-label "host" — not a real domain
    expect(normalizeHost('localhost')).toBeNull()
  })
})

describe('isStrictDomainMatch', () => {
  it('matches identical domains', () => {
    expect(isStrictDomainMatch('venturecrane.com', 'venturecrane.com')).toBe(true)
    expect(isStrictDomainMatch('venturecrane.com', 'https://venturecrane.com/')).toBe(true)
  })

  it('matches www-prefixed candidate against bare domain', () => {
    expect(isStrictDomainMatch('venturecrane.com', 'https://www.venturecrane.com')).toBe(true)
    expect(isStrictDomainMatch('www.venturecrane.com', 'https://venturecrane.com')).toBe(true)
  })

  it('does NOT match a different second-level domain', () => {
    // The bug exemplar: submitted venturecrane.com, Places returned sunrisecrane.com
    expect(isStrictDomainMatch('venturecrane.com', 'https://sunrisecrane.com')).toBe(false)
  })

  it('does NOT match multi-level subdomain against apex', () => {
    expect(isStrictDomainMatch('venturecrane.com', 'https://scan.venturecrane.com')).toBe(false)
    expect(isStrictDomainMatch('scan.venturecrane.com', 'https://venturecrane.com')).toBe(false)
  })

  it('returns false when either side is missing', () => {
    expect(isStrictDomainMatch('venturecrane.com', null)).toBe(false)
    expect(isStrictDomainMatch('venturecrane.com', '')).toBe(false)
    expect(isStrictDomainMatch('', 'https://venturecrane.com')).toBe(false)
    expect(isStrictDomainMatch(null, null)).toBe(false)
  })
})

describe('guardPlacesByDomain', () => {
  function makePlaces(website: string | null): PlacesEnrichment {
    return {
      phone: '+1 555 0001',
      website,
      rating: 4.5,
      reviewCount: 27,
      businessStatus: 'OPERATIONAL',
      address: '123 Main St, Phoenix, AZ',
    }
  }

  it('returns null when Places returned null (no result)', () => {
    expect(guardPlacesByDomain(null, 'venturecrane.com')).toBeNull()
  })

  it('returns null when Places result has no website', () => {
    expect(guardPlacesByDomain(makePlaces(null), 'venturecrane.com')).toBeNull()
  })

  it('returns null when Places returned a different second-level domain', () => {
    // The 2026-04-27 bug exemplar
    const places = makePlaces('https://sunrisecrane.com/')
    const guarded = guardPlacesByDomain(places, 'venturecrane.com')
    expect(guarded).toBeNull()
  })

  it('passes a strict domain match through unchanged', () => {
    const places = makePlaces('https://venturecrane.com/')
    const guarded = guardPlacesByDomain(places, 'venturecrane.com')
    expect(guarded).toBe(places)
  })

  it('passes a www-prefixed candidate against bare submitted domain', () => {
    const places = makePlaces('https://www.acme-plumbing.com')
    const guarded = guardPlacesByDomain(places, 'acme-plumbing.com')
    expect(guarded).toBe(places)
  })

  it('rejects a multi-level subdomain match', () => {
    const places = makePlaces('https://scan.acme-plumbing.com')
    const guarded = guardPlacesByDomain(places, 'acme-plumbing.com')
    expect(guarded).toBeNull()
  })
})

describe('evaluateThinFootprintGate — #612 strict domain-match enforcement', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it("trips with 'no_strict_places_match' when orchestrator passes placesStrictMatched=false", async () => {
    // Realistic shape: the placeholder website is set at entity-create time
    // (`https://${submittedDomain}`), but Places didn't strictly identify
    // the business by that domain. Even with a website on the entity row,
    // the gate trips — the website is a placeholder, not a verified
    // public footprint.
    const entity = await createEntity(db, ORG_ID, {
      name: 'Venturecrane',
      website: 'https://venturecrane.com',
      area: 'Phoenix, AZ',
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, entity, 'venturecrane.com', {
      placesStrictMatched: false,
    })
    expect(r.thin).toBe(true)
    expect(r.reason).toBe('no_strict_places_match')
  })

  it("does not trip with 'no_strict_places_match' when placesStrictMatched=true", async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Real Biz',
      website: 'https://realbiz.com',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'matched',
      metadata: { reviewCount: 25, rating: 4.5 },
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, entity, 'realbiz.com', {
      placesStrictMatched: true,
    })
    expect(r.thin).toBe(false)
  })

  it('preserves pre-#612 behavior when placesStrictMatched is unspecified', async () => {
    // Tests that don't pass the option fall back to detecting Places via
    // the enrichment row presence. This keeps the existing test suite
    // (and any callers from before #612) working without changes.
    const entity = await createEntity(db, ORG_ID, {
      name: 'Legacy Caller',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'matched',
      metadata: { reviewCount: 50 },
    })
    const env = { DB: db } as Parameters<typeof evaluateThinFootprintGate>[0]
    const r = await evaluateThinFootprintGate(env, entity, 'legacycaller.com')
    expect(r.thin).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// #616 — /scan diagnostic report rendering quality + anti-fab
//
// Bug exemplar: smoke test against `phoenixanimalexterminator.com` on
// 2026-04-27 produced a real report with six distinct rendering issues.
// Each test below locks one of the fixes from issue #616.
// ---------------------------------------------------------------------------

describe('#616 — title / displayName resolution', () => {
  it('prefers Outscraper canonical name over the placeholder entity name', () => {
    // entity.name at scan-start is the humanized domain placeholder.
    const entity = {
      id: 'e1',
      name: 'Phoenixanimalexterminator',
      area: 'Phoenix, AZ',
    } as Parameters<typeof resolveDisplayName>[0]
    const meta = new Map<string, Record<string, unknown> | null>([
      ['outscraper', { name: 'Phoenix Animal Exterminator' }],
    ])
    expect(resolveDisplayName(entity, meta)).toBe('Phoenix Animal Exterminator')
  })

  it('falls back to entity.name when Outscraper is missing', () => {
    const entity = {
      id: 'e1',
      name: 'Acme Co',
      area: 'Phoenix, AZ',
    } as Parameters<typeof resolveDisplayName>[0]
    const meta = new Map<string, Record<string, unknown> | null>()
    expect(resolveDisplayName(entity, meta)).toBe('Acme Co')
  })

  it('falls back to entity.name when Outscraper has no name field', () => {
    const entity = {
      id: 'e1',
      name: 'Acme Co',
      area: 'Phoenix, AZ',
    } as Parameters<typeof resolveDisplayName>[0]
    const meta = new Map<string, Record<string, unknown> | null>([['outscraper', { phone: '555' }]])
    expect(resolveDisplayName(entity, meta)).toBe('Acme Co')
  })
})

describe('#616 — owner section anti-fab (P1)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('omits owner_name when Outscraper returns the BUSINESS name as owner_name (Pattern A/B)', async () => {
    // The 2026-04-27 phoenixanimalexterminator.com bug exemplar.
    // Outscraper's owner_title field returned the listing-claim title
    // ("Phoenix Animal Exterminator"), which is the business itself —
    // not a person. The renderer must NOT label that value as the owner.
    const entity = await createEntity(db, ORG_ID, {
      name: 'Phoenixanimalexterminator',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: 'matched',
      metadata: {
        name: 'Phoenix Animal Exterminator',
        owner_name: 'Phoenix Animal Exterminator',
        verified: true,
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    expect(profile?.rendered).toBe(true)
    const ownerLine = (profile?.bullets ?? []).find((b) => b.toLowerCase().startsWith('owner'))
    expect(ownerLine).toBeDefined()
    // Critical: the line must NOT contain the business name.
    expect(ownerLine).not.toContain('Phoenix Animal Exterminator')
    // Renders the explicit "Insufficient data" placeholder per #616.
    expect(ownerLine).toContain('Insufficient data')
    expect(ownerLine).toContain("we'll surface this in conversation")
  })

  it("uses the new 'Insufficient data — surface in conversation' wording", async () => {
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
    const ownerLine = (profile?.bullets ?? []).find((b) => b.toLowerCase().startsWith('owner'))
    // Per #616 issue 2 — explicit Insufficient data wording.
    expect(ownerLine).toContain('Insufficient data')
    expect(ownerLine).toContain("we'll surface this in conversation")
  })

  it('rejects business-name-as-owner even with corporate suffix variants', async () => {
    // Outscraper sometimes returns "Acme HVAC LLC" as owner_title when
    // the entity's canonical name is "Acme HVAC". Strip suffixes before
    // comparing.
    const entity = await createEntity(db, ORG_ID, {
      name: 'Acme HVAC',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: 'matched',
      metadata: {
        name: 'Acme HVAC',
        owner_name: 'Acme HVAC LLC',
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    const ownerLine = (profile?.bullets ?? []).find((b) => b.toLowerCase().startsWith('owner'))
    expect(ownerLine).not.toContain('Acme HVAC LLC')
    expect(ownerLine).toContain('Insufficient data')
  })

  it('still renders a real human owner name when sources provide one', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Authored HVAC',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: 'matched',
      metadata: {
        name: 'Authored HVAC',
        owner_name: 'Maria Rodriguez',
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    const ownerLine = (profile?.bullets ?? []).find((b) => b.toLowerCase().startsWith('owner'))
    expect(ownerLine).toContain('Maria Rodriguez')
    expect(ownerLine).not.toContain('Insufficient data')
  })
})

describe('#616 — isLikelyBusinessName helper (anti-fab predicate)', () => {
  it('matches identical strings ignoring case + non-alphanumerics', () => {
    expect(isLikelyBusinessName('Phoenix Animal Exterminator', 'Phoenixanimalexterminator')).toBe(
      true
    )
    expect(isLikelyBusinessName('phoenix-animal-exterminator', 'Phoenix Animal Exterminator')).toBe(
      true
    )
  })

  it('matches across corporate suffix variants (LLC, Inc, etc.)', () => {
    expect(isLikelyBusinessName('Acme HVAC LLC', 'Acme HVAC')).toBe(true)
    expect(isLikelyBusinessName('Acme HVAC, Inc.', 'Acme HVAC')).toBe(true)
    expect(isLikelyBusinessName('Acme HVAC', 'Acme HVAC Co')).toBe(true)
  })

  it('does NOT match a real human name against the business name', () => {
    expect(isLikelyBusinessName('Maria Rodriguez', 'Acme HVAC')).toBe(false)
    expect(isLikelyBusinessName('John Smith', 'Acme HVAC LLC')).toBe(false)
  })

  it('handles empty/missing business names safely', () => {
    expect(isLikelyBusinessName('Maria Rodriguez', null, undefined, '')).toBe(false)
  })
})

describe('#616 — taxonomy + theme humanization (issue 3)', () => {
  it('maps 5-cat observation IDs through PROBLEM_LABELS', () => {
    expect(humanizeProblemId('process_design')).toBe('Process design')
    expect(humanizeProblemId('tool_systems')).toBe('Tools & systems')
    expect(humanizeProblemId('data_visibility')).toBe('Data & visibility')
    expect(humanizeProblemId('customer_pipeline')).toBe('Customer pipeline')
    expect(humanizeProblemId('team_operations')).toBe('Team operations')
  })

  it('passes through free-form problem strings unchanged', () => {
    expect(humanizeProblemId('phone tag')).toBe('phone tag')
    expect(humanizeProblemId('manual quoting')).toBe('manual quoting')
  })

  it('humanizes underscored review-theme keys', () => {
    expect(humanizeThemeKey('limited_online_presence')).toBe('Limited online presence')
    expect(humanizeThemeKey('quick_response')).toBe('Quick response')
  })

  it('preserves already-humanized themes', () => {
    expect(humanizeThemeKey('Friendly staff')).toBe('Friendly staff')
    expect(humanizeThemeKey('quick response')).toBe('Quick response')
  })

  it('renders engagement opportunity bullets with humanized labels (no raw IDs)', async () => {
    const db = await freshDb()
    const entity = await createEntity(db, ORG_ID, {
      name: 'Taxo Test Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: {
        operational_problems: [
          {
            problem: 'data_visibility',
            confidence: 'high',
            evidence: 'Only 8 reviews across platforms',
          },
          {
            problem: 'tool_systems',
            confidence: 'medium',
            evidence: "Website described as 'dated'",
          },
        ],
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const opp = r.sections.find((s) => s.id === 'engagement_opportunity')
    expect(opp?.rendered).toBe(true)
    const text = (opp?.bullets ?? []).join('\n')
    // Lock the human labels.
    expect(text).toContain('Data & visibility')
    expect(text).toContain('Tools & systems')
    // And lock that the raw IDs no longer leak.
    expect(text).not.toContain('data_visibility')
    expect(text).not.toContain('tool_systems')
  })

  it('renders conversation starters with humanized review themes', async () => {
    const db = await freshDb()
    const entity = await createEntity(db, ORG_ID, {
      name: 'Themes Biz',
      website: 'https://themes.example',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: { top_themes: ['limited_online_presence'] },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: { services: ['exterminator'] },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'deep_website',
      content: 'deep',
      metadata: { business_profile: { certifications: ['NPMA'] } },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const cs = r.sections.find((s) => s.id === 'conversation_starters')
    expect(cs?.rendered).toBe(true)
    const text = (cs?.bullets ?? []).join('\n')
    expect(text).toContain('Limited online presence')
    expect(text).not.toContain('limited_online_presence')
  })
})

describe('#616 — internal contradiction guard (issue 4)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('drops "no public scheduling" from gaps when Outscraper has a booking link', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Booking Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: {
        services: ['HVAC'],
        quality: 'good',
        tech_stack: { scheduling: [], crm: [], reviews: [], payments: [], communication: [] },
      },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: 'matched',
      metadata: {
        name: 'Booking Biz',
        booking_link: 'https://book.example/biz',
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const tech = r.sections.find((s) => s.id === 'tech_ops')
    expect(tech?.rendered).toBe(true)
    const text = (tech?.bullets ?? []).join('\n')
    // The booking-visible signal must still appear …
    expect(text).toContain('Online booking visible')
    // … and the gap claim must NOT include scheduling.
    const gapsLine = (tech?.bullets ?? []).find((b) => b.startsWith('Apparent gaps'))
    if (gapsLine) {
      expect(gapsLine).not.toContain('scheduling')
    }
  })

  it('still flags scheduling as a gap when no booking link exists', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'No Booking Biz',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: {
        services: ['HVAC'],
        quality: 'basic',
        tech_stack: { scheduling: [], crm: [], reviews: [], payments: [], communication: [] },
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const tech = r.sections.find((s) => s.id === 'tech_ops')
    const gapsLine = (tech?.bullets ?? []).find((b) => b.startsWith('Apparent gaps'))
    expect(gapsLine).toBeDefined()
    expect(gapsLine).toContain('scheduling')
  })
})

describe('#616 — duplicated rating + founded year provenance (issues 5 + 6)', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('does NOT re-render rating + review count in Conversation Starters', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Rich Biz',
      website: 'https://rich.example',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'google_places',
      content: 'matched',
      metadata: { reviewCount: 8, rating: 5 },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'review_synthesis',
      content: 'synthesized',
      metadata: { top_themes: ['friendly staff'] },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: {
        services: ['exterminator', 'pest control', 'inspections'],
        quality: 'basic',
      },
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'deep_website',
      content: 'deep',
      metadata: { business_profile: { certifications: ['NPMA'] } },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const overview = r.sections.find((s) => s.id === 'business_overview')
    const cs = r.sections.find((s) => s.id === 'conversation_starters')
    // Business Overview keeps the rating.
    const overviewText = (overview?.bullets ?? []).join('\n')
    expect(overviewText).toContain('5 stars')
    expect(overviewText).toContain('8 reviews')
    // Conversation Starters must NOT restate the rating.
    const csText = (cs?.bullets ?? []).join('\n')
    expect(csText).not.toMatch(/\d+\s+(Google\s+)?reviews?\s+averaging/i)
    expect(csText).not.toContain('averaging 5')
  })

  it('only renders founded year when website_analysis sourced it', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Founded Biz',
      website: 'https://founded.example',
      area: 'Phoenix, AZ',
    })
    // deep_website provides a founding_year via heuristic — must NOT
    // be surfaced (no provenance citation).
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'deep_website',
      content: 'deep',
      metadata: {
        business_profile: { founding_year: 2009 },
        owner_profile: { name: null, title: null, background: null },
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    const text = (profile?.bullets ?? []).join('\n')
    expect(text).not.toMatch(/Founded:\s*2009/)
  })

  it('renders founded year with provenance when website_analysis sourced it', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Sourced Biz',
      website: 'https://sourced.example',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'website_analysis',
      content: 'analyzed',
      metadata: {
        owner_name: 'Pat Doe',
        team_size: 5,
        founding_year: 2014,
        services: ['x'],
        quality: 'good',
        tech_stack: { scheduling: [], crm: [], reviews: [], payments: [], communication: [] },
      },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    const profile = r.sections.find((s) => s.id === 'owner_profile')
    const text = (profile?.bullets ?? []).join('\n')
    expect(text).toMatch(/Founded:\s*2014/)
    // Provenance hint reassures the reader where the year came from.
    expect(text).toContain('from website')
  })
})

describe('#616 — RenderedReport.displayName populated', () => {
  it('exposes the resolved displayName for the email title', async () => {
    const db = await freshDb()
    const entity = await createEntity(db, ORG_ID, {
      name: 'Phoenixanimalexterminator',
      area: 'Phoenix, AZ',
    })
    await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'enrichment',
      source: 'outscraper',
      content: 'matched',
      metadata: { name: 'Phoenix Animal Exterminator' },
    })
    const r = await renderDiagnosticReport(db, entity, null)
    expect(r.displayName).toBe('Phoenix Animal Exterminator')
  })
})
