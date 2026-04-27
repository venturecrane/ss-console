import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Env } from './index'

// ---------------------------------------------------------------------------
// Mock parent-repo imports
// ---------------------------------------------------------------------------

vi.mock('../../../src/lib/constants.js', () => ({
  ORG_ID: 'org-test-001',
}))

vi.mock('../../../src/lib/db/entities.js', () => ({
  findOrCreateEntity: vi.fn(),
}))

vi.mock('../../../src/lib/db/context.js', () => ({
  appendContext: vi.fn(),
}))

vi.mock('../../../src/lib/db/generators.js', () => ({
  getGeneratorConfig: vi.fn(),
  recordGeneratorRun: vi.fn(),
}))

vi.mock('../../../src/lib/db/pipeline-settings.js', () => ({
  getPipelineSettings: vi.fn(),
}))

vi.mock('../../../src/lib/enrichment/index.js', () => ({
  enrichEntity: vi.fn().mockResolvedValue(undefined),
}))

// ---------------------------------------------------------------------------
// Mock worker-local modules
// ---------------------------------------------------------------------------

vi.mock('./outscraper.js', () => ({
  discoverBusinesses: vi.fn(),
  fetchReviews: vi.fn(),
}))

vi.mock('./qualify.js', () => ({
  scoreReviews: vi.fn(),
}))

vi.mock('./alert.js', () => ({
  sendFailureAlert: vi.fn().mockResolvedValue(undefined),
}))

// ---------------------------------------------------------------------------
// Imports (after mocks)
// ---------------------------------------------------------------------------

import worker from './index'
import { getGeneratorConfig, recordGeneratorRun } from '../../../src/lib/db/generators.js'
import { getPipelineSettings } from '../../../src/lib/db/pipeline-settings.js'
import { findOrCreateEntity } from '../../../src/lib/db/entities.js'
import { appendContext } from '../../../src/lib/db/context.js'
import { discoverBusinesses, fetchReviews } from './outscraper.js'
import { scoreReviews } from './qualify.js'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    DB: makeMockD1(),
    GOOGLE_PLACES_API_KEY: 'sk-test-places',
    OUTSCRAPER_API_KEY: 'sk-test-outscraper',
    ANTHROPIC_API_KEY: 'sk-test-anthropic',
    RESEND_API_KEY: 'sk-test-resend',
    LEAD_INGEST_API_KEY: 'sk-test-ingest-key',
    ...overrides,
  }
}

function makeSettings(
  overrides: Partial<{
    pain_threshold: number
    max_review_checks: number
    outscraper_budget_usd_per_run: number
  }> = {}
) {
  return {
    pain_threshold: 7,
    max_review_checks: 200,
    outscraper_budget_usd_per_run: 1.0,
    ...overrides,
  }
}

function makeRequest(authHeader?: string): Request {
  return new Request('https://worker.example.com/run', {
    method: 'POST',
    headers: authHeader ? { Authorization: authHeader } : {},
  })
}

function makeMockD1(): D1Database {
  return {
    prepare: vi.fn().mockReturnValue({
      bind: vi.fn().mockReturnValue({
        first: vi.fn().mockResolvedValue(null),
        run: vi.fn().mockResolvedValue({ success: true }),
        all: vi.fn().mockResolvedValue({ results: [] }),
      }),
    }),
    exec: vi.fn(),
    batch: vi.fn(),
    dump: vi.fn(),
  } as unknown as D1Database
}

function makeCtx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  } as unknown as ExecutionContext
}

function makeDisabledConfig() {
  return { enabled: false, config: {} }
}

function makeEnabledConfig() {
  return {
    enabled: true,
    config: {
      discovery_queries: ['HVAC Phoenix', 'plumber Phoenix'],
      geo_center: { lat: 33.4484, lng: -112.074 },
      geo_radius_km: 50,
    },
  }
}

function makeDiscoveredBusiness(overrides = {}) {
  return {
    place_id: 'place-abc-001',
    name: 'Desert HVAC',
    address: '456 Main St, Phoenix, AZ',
    rating: 3.8,
    total_reviews: 42,
    category: 'HVAC contractor',
    phone: '602-555-0100',
    website: 'https://deserthvac.com',
    ...overrides,
  }
}

function makeBusinessWithReviews(overrides = {}) {
  return {
    ...makeDiscoveredBusiness(),
    area: 'Phoenix',
    reviews: [
      {
        author: 'Jane D',
        rating: 2,
        text: 'Technician never showed up on time. Owner does everything himself.',
        date: '2026-04-01',
      },
    ],
    ...overrides,
  }
}

function makeScoring(overrides = {}) {
  return {
    business_name: 'Desert HVAC',
    place_id: 'place-abc-001',
    pain_score: 8,
    top_problems: ['scheduling', 'owner_bottleneck'],
    outreach_angle: 'Help them fix scheduling chaos.',
    signals: [{ problem_id: 'scheduling', quote: 'Never showed up on time.' }],
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests: fetch handler auth
// ---------------------------------------------------------------------------

describe('review-mining fetch handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([])
    vi.mocked(fetchReviews).mockResolvedValue([])
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert HVAC' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('returns 401 when Authorization header is missing', async () => {
    const res = await worker.fetch(makeRequest(), makeEnv(), makeCtx())
    expect(res.status).toBe(401)
  })

  it('returns 401 when wrong bearer key is provided', async () => {
    const res = await worker.fetch(makeRequest('Bearer wrong-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(401)
  })

  it('returns 401 when Authorization has no Bearer prefix', async () => {
    const res = await worker.fetch(makeRequest('sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(401)
  })

  it('returns 200 JSON summary on valid auth', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body).toMatchObject({
      queries: expect.any(Number),
      discovered: expect.any(Number),
      written: expect.any(Number),
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: disabled generator
// ---------------------------------------------------------------------------

describe('review-mining disabled generator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeDisabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
  })

  it('skips run when disabled', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.written).toBe(0)
    expect(discoverBusinesses).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: happy path
// ---------------------------------------------------------------------------

describe('review-mining happy path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([makeDiscoveredBusiness()])
    vi.mocked(fetchReviews).mockResolvedValue([makeBusinessWithReviews()])
    vi.mocked(scoreReviews).mockResolvedValue(makeScoring() as never)
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert HVAC' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('scores a business and writes to D1 when pain_score >= 7', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
    expect(appendContext).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// Tests: below pain threshold
// ---------------------------------------------------------------------------

describe('review-mining below threshold', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([makeDiscoveredBusiness()])
    vi.mocked(fetchReviews).mockResolvedValue([makeBusinessWithReviews()])
    vi.mocked(scoreReviews).mockResolvedValue(makeScoring({ pain_score: 5 }) as never)
  })

  it('skips D1 write when pain_score is below threshold', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.belowThreshold).toBe(1)
    expect(body.written).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: Outscraper failure
// ---------------------------------------------------------------------------

describe('review-mining Outscraper failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([makeDiscoveredBusiness()])
    vi.mocked(fetchReviews).mockRejectedValue(new Error('Outscraper: 503 Service Unavailable'))
  })

  it('records error and returns degraded summary without crashing', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.errors).toBeGreaterThan(0)
    expect(body.written).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Tests: Google Places discovery failure
// ---------------------------------------------------------------------------

describe('review-mining discovery failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockRejectedValue(new Error('Google Places: 403'))
    vi.mocked(fetchReviews).mockResolvedValue([])
  })

  it('records error and continues with zero businesses', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.errors).toBeGreaterThan(0)
    expect(body.discovered).toBe(0)
    expect(body.written).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Tests: scheduled handler
// ---------------------------------------------------------------------------

describe('review-mining scheduled handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([])
    vi.mocked(fetchReviews).mockResolvedValue([])
  })

  it('runs without error on scheduled trigger', async () => {
    await expect(
      worker.scheduled({} as ScheduledController, makeEnv(), makeCtx())
    ).resolves.toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Tests: per-run cap + Outscraper budget guard
// (settings-driven since #595 — was env-var-driven under #592)
// ---------------------------------------------------------------------------

describe('review-mining cap and budget guard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchReviews).mockResolvedValue([])
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert HVAC' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('respects max_review_checks setting (caps at 5 of 12 discovered)', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue(
      makeSettings({ max_review_checks: 5 }) as never
    )
    const businesses = Array.from({ length: 12 }, (_, i) =>
      makeDiscoveredBusiness({ place_id: `p-${i}` })
    )
    vi.mocked(discoverBusinesses).mockResolvedValue(businesses)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.discovered).toBe(12)
    expect(body.reviewChecksAttempted).toBe(5)
    // 5 places x $0.003 = $0.015
    expect(body.outscraperSpendUsd).toBeCloseTo(0.015, 5)
    expect(body.budgetGuardTripped).toBe(false)
  })

  it('stops early when the Outscraper budget guard setting would be exceeded', async () => {
    // 30 discovered, batch size 10, $0.003 each. Budget $0.04 allows
    // at most 1 batch ($0.030); the 2nd batch would push to $0.060 > $0.040.
    vi.mocked(getPipelineSettings).mockResolvedValue(
      makeSettings({ outscraper_budget_usd_per_run: 0.04 }) as never
    )
    const businesses = Array.from({ length: 30 }, (_, i) =>
      makeDiscoveredBusiness({ place_id: `p-${i}` })
    )
    vi.mocked(discoverBusinesses).mockResolvedValue(businesses)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.budgetGuardTripped).toBe(true)
    expect(body.reviewChecksAttempted).toBe(10)
    expect(body.outscraperSpendUsd).toBeCloseTo(0.03, 5)
  })

  it('uses the 200 default when settings are unset (defaults from DAL)', async () => {
    const businesses = Array.from({ length: 250 }, (_, i) =>
      makeDiscoveredBusiness({ place_id: `p-${i}` })
    )
    vi.mocked(discoverBusinesses).mockResolvedValue(businesses)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.discovered).toBe(250)
    expect(body.reviewChecksAttempted).toBe(200)
    expect(body.outscraperSpendUsd).toBeCloseTo(0.6, 5)
    expect(body.budgetGuardTripped).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Tests: pain_threshold setting (issue #595)
// ---------------------------------------------------------------------------

describe('review-mining pain_threshold from settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(discoverBusinesses).mockResolvedValue([makeDiscoveredBusiness()])
    vi.mocked(fetchReviews).mockResolvedValue([makeBusinessWithReviews()])
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert HVAC' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('writes a pain=6 business when admin lowers threshold to 5', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings({ pain_threshold: 5 }) as never)
    vi.mocked(scoreReviews).mockResolvedValue(makeScoring({ pain_score: 6 }) as never)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
  })

  it('skips a pain=8 business when admin raises threshold to 9', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings({ pain_threshold: 9 }) as never)
    vi.mocked(scoreReviews).mockResolvedValue(makeScoring({ pain_score: 8 }) as never)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.belowThreshold).toBe(1)
    expect(body.written).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })

  it('uses default 7 when settings table returns DAL defaults', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue(makeSettings() as never)
    vi.mocked(scoreReviews).mockResolvedValue(makeScoring({ pain_score: 7 }) as never)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    // pain_score=7 with threshold=7 should pass (>=)
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
  })
})
