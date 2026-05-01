/**
 * Tests for the EnrichmentWorkflow class (#631).
 *
 * Strategy mirrors `tests/diagnostic-workflow.test.ts`: instantiate the
 * Workflow directly with a mock env and a mock `step` whose `do` invokes
 * the callback immediately. We never spin up the real Cloudflare Workflows
 * engine — that's what staging is for.
 *
 * Coverage:
 *   - Idempotency (existing intelligence_brief → init returns skip)
 *   - Entity reload per step (tier1-places mutates D1, tier1-website
 *     sees the fresh state)
 *   - Reviews-and-news mode runs only the subset
 *   - Outreach instrumentation (failure produces a failed
 *     `enrichment_runs` row)
 *   - Force=no-skip semantics (no skip-succeeded shortcut; every step runs
 *     even when prior succeeded rows exist for the same modules — the
 *     `init`-level idempotency on `intelligence_brief` is the only short-
 *     circuit)
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

// Mock external module-wrapper dependencies. The workflow imports module
// wrappers from src/lib/enrichment/index.ts, which in turn import these
// modules. Mocking at this lower level lets each `try*` wrapper run its
// real instrumentModule + appendContext path so we can assert on D1 state.
vi.mock('../src/lib/enrichment/google-places', () => ({
  lookupGooglePlaces: vi.fn(),
}))
vi.mock('../src/lib/enrichment/outscraper', () => ({
  lookupOutscraper: vi.fn(),
}))
vi.mock('../src/lib/enrichment/website-analyzer', () => ({
  analyzeWebsite: vi.fn(),
}))
vi.mock('../src/lib/enrichment/acc', () => ({
  lookupAcc: vi.fn().mockResolvedValue(null),
}))
vi.mock('../src/lib/enrichment/roc', () => ({
  lookupRoc: vi.fn().mockResolvedValue(null),
}))
vi.mock('../src/lib/enrichment/review-analysis', () => ({
  analyzeReviewPatterns: vi.fn(),
}))
vi.mock('../src/lib/enrichment/competitors', () => ({
  benchmarkCompetitors: vi.fn(),
}))
vi.mock('../src/lib/enrichment/news', () => ({
  searchNews: vi.fn(),
}))
vi.mock('../src/lib/enrichment/deep-website', () => ({
  deepWebsiteAnalysis: vi.fn(),
}))
vi.mock('../src/lib/enrichment/review-synthesis', () => ({
  synthesizeReviews: vi.fn(),
}))
vi.mock('../src/lib/enrichment/linkedin', () => ({
  lookupLinkedIn: vi.fn(),
}))
vi.mock('../src/lib/enrichment/dossier', () => ({
  generateDossier: vi.fn(),
}))
vi.mock('../src/lib/claude/outreach', () => ({
  generateOutreachDraft: vi.fn(),
}))

import { lookupGooglePlaces } from '../src/lib/enrichment/google-places'
import { analyzeWebsite } from '../src/lib/enrichment/website-analyzer'
import { analyzeReviewPatterns } from '../src/lib/enrichment/review-analysis'
import { synthesizeReviews } from '../src/lib/enrichment/review-synthesis'
import { searchNews } from '../src/lib/enrichment/news'
import { generateDossier } from '../src/lib/enrichment/dossier'
import { generateOutreachDraft } from '../src/lib/claude/outreach'
import {
  EnrichmentWorkflow,
  type EnrichmentWorkflowBindings,
  type EnrichmentWorkflowParams,
} from '../src/lib/enrichment/workflow'
import { createEntity, getEntity } from '../src/lib/db/entities'
import { appendContext } from '../src/lib/db/context'
import { ORG_ID } from '../src/lib/constants'
import type { WorkflowEvent } from 'cloudflare:workers'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1() as unknown as D1Database
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

interface StepStats {
  callsByStep: Map<string, number>
}

/**
 * Test step builder. Mirrors `tests/diagnostic-workflow.test.ts` — invokes
 * each callback immediately, no checkpointing, tracks call counts per
 * step name.
 */
function makeStep(): {
  step: {
    do<T>(name: string, fn: () => Promise<T>): Promise<T>
    do<T>(name: string, config: unknown, fn: () => Promise<T>): Promise<T>
  }
  stats: StepStats
} {
  const stats: StepStats = { callsByStep: new Map() }
  const step = {
    async do<T>(name: string, configOrFn: unknown, maybeFn?: () => Promise<T>): Promise<T> {
      const fn = (maybeFn ?? configOrFn) as () => Promise<T>
      stats.callsByStep.set(name, (stats.callsByStep.get(name) ?? 0) + 1)
      return fn()
    },
  }
  return { step, stats }
}

async function runWorkflow(
  bindings: EnrichmentWorkflowBindings,
  params: EnrichmentWorkflowParams
): Promise<{ stats: StepStats }> {
  const wf = new EnrichmentWorkflow({} as never, bindings as never)
  const { step, stats } = makeStep()
  const event: WorkflowEvent<EnrichmentWorkflowParams> = {
    payload: params,
    timestamp: new Date(),
    instanceId: 'test-instance',
  }
  await wf.run(event, step as never)
  return { stats }
}

async function seedEntity(
  db: D1Database,
  overrides: Partial<{ name: string; phone: string | null; website: string | null }> = {}
): Promise<string> {
  const entity = await createEntity(db, ORG_ID, {
    name: overrides.name ?? 'Acme Plumbing',
    area: 'Phoenix',
    phone: overrides.phone ?? null,
    website: overrides.website ?? null,
    source_pipeline: 'test',
  })
  return entity.id
}

function bindings(db: D1Database): EnrichmentWorkflowBindings {
  return {
    DB: db,
    ANTHROPIC_API_KEY: 'test',
    GOOGLE_PLACES_API_KEY: 'test',
    OUTSCRAPER_API_KEY: 'test',
    SERPAPI_API_KEY: 'test',
    PROXYCURL_API_KEY: undefined,
  }
}

// ===========================================================================

describe('EnrichmentWorkflow — idempotency', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
    vi.clearAllMocks()
  })

  it('init step returns skip when intelligence_brief context entry exists', async () => {
    const entityId = await seedEntity(db, { website: 'https://example.com' })

    // Pre-seed an existing intelligence_brief context entry — simulates a
    // previously-completed full enrichment.
    await appendContext(db, ORG_ID, {
      entity_id: entityId,
      type: 'enrichment',
      content: 'Pre-existing brief.',
      source: 'intelligence_brief',
    })

    const { stats } = await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })

    // init ran once.
    expect(stats.callsByStep.get('init')).toBe(1)
    // No tier-1 / tier-2 / tier-3 / outreach / finalize steps ran.
    expect(stats.callsByStep.get('tier1-places')).toBeUndefined()
    expect(stats.callsByStep.get('tier3-intelligence-brief')).toBeUndefined()
    expect(stats.callsByStep.get('outreach')).toBeUndefined()
    expect(stats.callsByStep.get('finalize')).toBeUndefined()

    // No Anthropic calls.
    expect(vi.mocked(generateDossier)).not.toHaveBeenCalled()
    expect(vi.mocked(generateOutreachDraft)).not.toHaveBeenCalled()
  })

  it('init step does NOT skip on reviews-and-news mode even with existing brief', async () => {
    const entityId = await seedEntity(db, { website: 'https://example.com' })
    await appendContext(db, ORG_ID, {
      entity_id: entityId,
      type: 'enrichment',
      content: 'Pre-existing brief.',
      source: 'intelligence_brief',
    })

    vi.mocked(analyzeReviewPatterns).mockResolvedValue({
      response_pattern: 'responsive',
      engagement_level: 'high',
      owner_accessible: true,
      insights: 'engaged',
    } as unknown as Awaited<ReturnType<typeof analyzeReviewPatterns>>)
    vi.mocked(synthesizeReviews).mockResolvedValue({
      customer_sentiment: 'positive',
      sentiment_trend: 'stable',
      top_themes: ['speed'],
      operational_problems: [],
    } as unknown as Awaited<ReturnType<typeof synthesizeReviews>>)
    vi.mocked(searchNews).mockResolvedValue({
      summary: 'recent local press',
      mentions: [],
    } as unknown as Awaited<ReturnType<typeof searchNews>>)
    vi.mocked(generateOutreachDraft).mockResolvedValue('Hi there')

    const { stats } = await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'reviews-and-news',
      triggered_by: 'test',
    })

    // Reviews-and-news subset ran.
    expect(stats.callsByStep.get('init')).toBe(1)
    expect(stats.callsByStep.get('tier2-review-analysis')).toBe(1)
    expect(stats.callsByStep.get('tier3-review-synthesis')).toBe(1)
    expect(stats.callsByStep.get('tier2-news')).toBe(1)
    // Brief step ran but skipped (existing brief).
    expect(stats.callsByStep.get('tier3-intelligence-brief')).toBe(1)
    expect(vi.mocked(generateDossier)).not.toHaveBeenCalled()
    // Outreach + finalize ran.
    expect(stats.callsByStep.get('outreach')).toBe(1)
    expect(stats.callsByStep.get('finalize')).toBe(1)
    // Tier-1 modules NOT in reviews-and-news.
    expect(stats.callsByStep.get('tier1-places')).toBeUndefined()
  })

  it('init step returns skip when entity does not exist', async () => {
    const { stats } = await runWorkflow(bindings(db), {
      entityId: 'nonexistent-id',
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })
    expect(stats.callsByStep.get('init')).toBe(1)
    expect(stats.callsByStep.get('tier1-places')).toBeUndefined()
  })
})

// ===========================================================================

describe('EnrichmentWorkflow — entity reload per step', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
    vi.clearAllMocks()
  })

  it('tier1-website sees the website written by tier1-places', async () => {
    const entityId = await seedEntity(db, { website: null, phone: null })

    // Places returns a website — `tryPlaces` writes it via updateEntity.
    vi.mocked(lookupGooglePlaces).mockResolvedValue({
      phone: '+1 555 0123',
      website: 'https://acmeplumbing.com',
      rating: 4.5,
      reviewCount: 12,
      businessStatus: 'OPERATIONAL',
      address: 'Phoenix, AZ',
    } as unknown as Awaited<ReturnType<typeof lookupGooglePlaces>>)

    // analyzeWebsite is the tier1-website body. It receives the entity
    // re-loaded from D1 — must see the website Places wrote.
    vi.mocked(analyzeWebsite).mockImplementation(async (websiteUrl: string) => {
      // The implementation receives the URL string from `entity.website`.
      // Assert it matches what Places wrote, proving the step re-loaded.
      expect(websiteUrl).toBe('https://acmeplumbing.com')
      return {
        pages_analyzed: ['/'],
        quality: 'medium',
        services: [],
        tech_stack: {
          scheduling: [],
          crm: [],
          reviews: [],
          payments: [],
          communication: [],
          platform: [],
        },
      } as unknown as Awaited<ReturnType<typeof analyzeWebsite>>
    })

    // Stub the rest so the workflow completes.
    vi.mocked(generateDossier).mockResolvedValue('# Brief')
    vi.mocked(generateOutreachDraft).mockResolvedValue('Hi')

    await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })

    // Verify the entity row was actually updated.
    const refreshed = await getEntity(db, ORG_ID, entityId)
    expect(refreshed?.website).toBe('https://acmeplumbing.com')
    expect(refreshed?.phone).toBe('+1 555 0123')

    // analyzeWebsite was invoked (tier1-website ran with the post-Places URL).
    expect(vi.mocked(analyzeWebsite)).toHaveBeenCalled()
  })
})

// ===========================================================================

describe('EnrichmentWorkflow — outreach instrumentation', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
    vi.clearAllMocks()
  })

  it('a thrown error inside generateOutreachDraft writes a failed enrichment_runs row', async () => {
    const entityId = await seedEntity(db, { website: 'https://example.com' })
    // Seed at least one context entry so assembleEntityContext (called
    // inside tryOutreach) returns non-null and we reach generateOutreachDraft.
    await appendContext(db, ORG_ID, {
      entity_id: entityId,
      type: 'signal',
      content: 'Original signal context.',
      source: 'test',
    })

    // Stub Places + Outscraper so we don't write phone/website mutations
    // (irrelevant to this test). Returning null = no_data outcome.
    vi.mocked(lookupGooglePlaces).mockResolvedValue(null)
    vi.mocked(analyzeWebsite).mockResolvedValue(null)
    vi.mocked(analyzeReviewPatterns).mockResolvedValue(null)
    vi.mocked(synthesizeReviews).mockResolvedValue(null)
    vi.mocked(searchNews).mockResolvedValue(null)
    vi.mocked(generateDossier).mockResolvedValue('# Brief')
    // Outreach throws.
    vi.mocked(generateOutreachDraft).mockRejectedValue(new Error('Anthropic 529'))

    await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })

    // The failed outreach run should be visible in enrichment_runs.
    const result = await db
      .prepare(
        `SELECT module, status, error_message FROM enrichment_runs
         WHERE entity_id = ? AND module = 'outreach_draft'`
      )
      .bind(entityId)
      .all()
    const rows = (result.results ?? []) as Array<{
      module: string
      status: string
      error_message: string | null
    }>
    expect(rows.length).toBe(1)
    expect(rows[0].status).toBe('failed')
    expect(rows[0].error_message).toContain('Anthropic 529')
  })
})

// ===========================================================================

describe('EnrichmentWorkflow — no skip-succeeded semantics', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
    vi.clearAllMocks()
  })

  it('every full-mode step runs even when prior succeeded rows exist', async () => {
    const entityId = await seedEntity(db, { website: 'https://example.com' })

    // tryReviewAnalysis short-circuits when there is no signal context
    // (skipped:no_signal_context). Seed one so the module body runs and
    // we can observe analyzeReviewPatterns being called.
    await appendContext(db, ORG_ID, {
      entity_id: entityId,
      type: 'signal',
      content: 'Original signal.',
      source: 'test',
    })

    // Seed a `succeeded` enrichment_runs row for review_analysis (legacy
    // skip-succeeded would have skipped this module on a force re-run).
    await db
      .prepare(
        `INSERT INTO enrichment_runs
           (id, org_id, entity_id, module, status, started_at, completed_at, triggered_by, mode)
         VALUES (?, ?, ?, 'review_analysis', 'succeeded',
                 datetime('now', '-1 day'),
                 datetime('now', '-1 day'),
                 'prior-run', 'full')`
      )
      .bind('prior-run-id', ORG_ID, entityId)
      .run()

    vi.mocked(lookupGooglePlaces).mockResolvedValue(null)
    vi.mocked(analyzeWebsite).mockResolvedValue(null)
    vi.mocked(analyzeReviewPatterns).mockResolvedValue({
      response_pattern: 'responsive',
      engagement_level: 'high',
      owner_accessible: true,
      insights: 'engaged',
    } as unknown as Awaited<ReturnType<typeof analyzeReviewPatterns>>)
    vi.mocked(synthesizeReviews).mockResolvedValue(null)
    vi.mocked(searchNews).mockResolvedValue(null)
    vi.mocked(generateDossier).mockResolvedValue('# Brief')
    vi.mocked(generateOutreachDraft).mockResolvedValue('Hi')

    const { stats } = await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })

    // tier2-review-analysis step ran (no skip-succeeded shortcut).
    expect(stats.callsByStep.get('tier2-review-analysis')).toBe(1)
    expect(vi.mocked(analyzeReviewPatterns)).toHaveBeenCalled()
  })
})

// ===========================================================================

describe('EnrichmentWorkflow — finalize sets next_action', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
    vi.clearAllMocks()
  })

  it('finalize step writes next_action when blank', async () => {
    const entityId = await seedEntity(db)

    // All wrappers return null → succeeded:false outcomes; the brief
    // succeeds so init's idempotency-guard accepts on next run, but we're
    // asserting on finalize behavior of THIS run.
    vi.mocked(lookupGooglePlaces).mockResolvedValue(null)
    vi.mocked(analyzeWebsite).mockResolvedValue(null)
    vi.mocked(analyzeReviewPatterns).mockResolvedValue(null)
    vi.mocked(synthesizeReviews).mockResolvedValue(null)
    vi.mocked(searchNews).mockResolvedValue(null)
    vi.mocked(generateDossier).mockResolvedValue('# Brief')
    vi.mocked(generateOutreachDraft).mockResolvedValue('Hi')

    const beforeRun = await getEntity(db, ORG_ID, entityId)
    expect(beforeRun?.next_action).toBeFalsy()

    await runWorkflow(bindings(db), {
      entityId,
      orgId: ORG_ID,
      mode: 'full',
      triggered_by: 'test',
    })

    const afterRun = await getEntity(db, ORG_ID, entityId)
    expect(afterRun?.next_action).toBe('Review enrichment and send outreach email')
  })
})
