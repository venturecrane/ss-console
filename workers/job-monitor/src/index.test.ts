import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Env } from './index'

// ---------------------------------------------------------------------------
// Mock all parent-repo imports (D1 helpers, enrichment, constants)
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

vi.mock('../../../src/lib/enrichment/dispatch.js', () => ({
  dispatchEnrichmentWorkflow: vi
    .fn()
    .mockResolvedValue({ workflowRunId: 'wf-test', alreadyEnriched: false, dispatched: true }),
}))

// ---------------------------------------------------------------------------
// Mock worker-local modules
// ---------------------------------------------------------------------------

vi.mock('./serpapi.js', () => ({
  searchJobs: vi.fn(),
}))

vi.mock('./qualify.js', () => ({
  qualifyJob: vi.fn(),
  derivePainScore: vi.fn().mockReturnValue(8),
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
import { searchJobs } from './serpapi.js'
import { qualifyJob, derivePainScore } from './qualify.js'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    DB: makeMockD1(),
    SERPAPI_API_KEY: 'sk-test-serpapi',
    ANTHROPIC_API_KEY: 'sk-test-anthropic',
    RESEND_API_KEY: 'sk-test-resend',
    LEAD_INGEST_API_KEY: 'sk-test-ingest-key',
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
  return {
    enabled: false,
    config: {},
  }
}

function makeEnabledConfig() {
  return {
    enabled: true,
    config: {
      search_queries: ['operations manager Phoenix', 'office manager Phoenix'],
    },
  }
}

function makeSerpJob(overrides = {}) {
  return {
    title: 'Operations Manager',
    company_name: 'Acme Plumbing',
    location: 'Phoenix, AZ',
    description: 'Looking for someone to own all operations.',
    job_id: 'job-abc-123',
    apply_options: [{ title: 'Apply', link: 'https://example.com/apply' }],
    company_url: 'https://acmeplumbing.com',
    ...overrides,
  }
}

function makeQualification(overrides = {}) {
  return {
    qualified: true,
    company: 'Acme Plumbing',
    confidence: 'high' as const,
    evidence: 'Owner doing everything themselves.',
    outreach_angle: 'Help them build ops structure.',
    problems_signaled: ['founder_ceiling', 'operational_drag'],
    company_size_estimate: '5-15',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests: fetch handler auth
// ---------------------------------------------------------------------------

describe('job-monitor fetch handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(8)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockResolvedValue([])
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Acme Plumbing' },
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

  it('returns 401 when Authorization header has wrong format', async () => {
    const res = await worker.fetch(makeRequest('sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(401)
  })

  it('returns 200 JSON summary on valid auth with no results', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body).toMatchObject({
      queries: expect.any(Number),
      totalResults: expect.any(Number),
      written: expect.any(Number),
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: disabled generator
// ---------------------------------------------------------------------------

describe('job-monitor disabled generator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeDisabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
  })

  it('skips run and returns zero summary when generator is disabled', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.written).toBe(0)
    expect(body.qualified).toBe(0)
    expect(searchJobs).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: happy path with qualified job
// ---------------------------------------------------------------------------

describe('job-monitor happy path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(8)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockResolvedValue([makeSerpJob()])
    vi.mocked(qualifyJob).mockResolvedValue(makeQualification() as never)
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Acme Plumbing' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('qualifies and writes a job to D1', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    // Both queries return the same job_id, so dedup yields 1 unique job
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
    expect(appendContext).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: SerpAPI failure
// ---------------------------------------------------------------------------

describe('job-monitor SerpAPI failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(8)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockRejectedValue(new Error('SerpAPI: 401 Unauthorized'))
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
// Tests: Claude qualification failure
// ---------------------------------------------------------------------------

describe('job-monitor Claude failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(8)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockResolvedValue([makeSerpJob()])
    vi.mocked(qualifyJob).mockResolvedValue(null)
  })

  it('skips writing when Claude returns null', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.written).toBe(0)
    expect(body.errors).toBeGreaterThan(0)
    expect(appendContext).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: scheduled handler
// ---------------------------------------------------------------------------

describe('job-monitor scheduled handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(8)
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockResolvedValue([])
  })

  it('runs without error on scheduled trigger', async () => {
    await expect(
      worker.scheduled({} as ScheduledController, makeEnv(), makeCtx())
    ).resolves.toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Tests: pain_threshold setting (issue #595)
// ---------------------------------------------------------------------------

describe('job-monitor pain_threshold from settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(searchJobs).mockResolvedValue([makeSerpJob()])
    vi.mocked(qualifyJob).mockResolvedValue(makeQualification() as never)
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Acme Plumbing' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('skips a job with derived pain score below threshold', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 9 } as never)
    vi.mocked(derivePainScore).mockReturnValue(7)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.belowThreshold).toBe(1)
    expect(body.qualified).toBe(0)
    expect(body.written).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })

  it('writes a low-confidence job when admin lowers threshold', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 5 } as never)
    vi.mocked(derivePainScore).mockReturnValue(5)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
  })

  it('uses default threshold of 7 from DAL', async () => {
    vi.mocked(getPipelineSettings).mockResolvedValue({ pain_threshold: 7 } as never)
    vi.mocked(derivePainScore).mockReturnValue(7)
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    // pain_score=7 with threshold=7 should pass (>=)
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
  })
})
