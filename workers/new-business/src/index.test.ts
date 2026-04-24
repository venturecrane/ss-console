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

vi.mock('../../../src/lib/enrichment/index.js', () => ({
  enrichEntity: vi.fn().mockResolvedValue(undefined),
}))

// ---------------------------------------------------------------------------
// Mock worker-local modules
// ---------------------------------------------------------------------------

vi.mock('./soda.js', () => ({
  fetchAllPermits: vi.fn(),
}))

vi.mock('./qualify.js', () => ({
  qualifyNewBusiness: vi.fn(),
}))

vi.mock('./alert.js', () => ({
  sendFailureAlert: vi.fn().mockResolvedValue(undefined),
}))

// ---------------------------------------------------------------------------
// Imports (after mocks)
// ---------------------------------------------------------------------------

import worker from './index'
import { getGeneratorConfig, recordGeneratorRun } from '../../../src/lib/db/generators.js'
import { findOrCreateEntity } from '../../../src/lib/db/entities.js'
import { appendContext } from '../../../src/lib/db/context.js'
import { fetchAllPermits } from './soda.js'
import { qualifyNewBusiness } from './qualify.js'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    DB: makeMockD1(),
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
  return { enabled: false, config: {} }
}

function makeEnabledConfig() {
  return {
    enabled: true,
    config: {
      soda_sources: [
        { city: 'phoenix', enabled: true },
        { city: 'scottsdale', enabled: true },
      ],
    },
  }
}

function makePermit(overrides = {}) {
  return {
    permit_number: 'PERMIT-001',
    business_name: 'Desert Bloom Florist',
    entity_type: 'Commercial',
    permit_type: 'New Commercial',
    filing_date: '2026-04-01',
    address: '123 Main St, Phoenix, AZ',
    source: 'phoenix_permit' as const,
    ...overrides,
  }
}

function makeQualification(overrides = {}) {
  return {
    qualified: true,
    outreach_timing: 'immediate' as const,
    business_name: 'Desert Bloom Florist',
    area: 'Phoenix',
    entity_type: 'retail' as const,
    source: 'new_commercial_permit' as const,
    notes: 'New retail florist, likely needs operational setup.',
    outreach_angle: 'Help them build their ops from day one.',
    vertical_match: 'retail',
    size_estimate: '1-5',
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Tests: fetch handler auth
// ---------------------------------------------------------------------------

describe('new-business fetch handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchAllPermits).mockResolvedValue([])
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert Bloom Florist' },
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

  it('returns 401 when Authorization header has no Bearer prefix', async () => {
    const res = await worker.fetch(makeRequest('sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(401)
  })

  it('returns 200 JSON summary on valid auth', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body).toMatchObject({
      totalPermits: expect.any(Number),
      written: expect.any(Number),
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: disabled generator
// ---------------------------------------------------------------------------

describe('new-business disabled generator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeDisabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
  })

  it('skips run when disabled', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.written).toBe(0)
    expect(fetchAllPermits).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: happy path
// ---------------------------------------------------------------------------

describe('new-business happy path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchAllPermits).mockResolvedValue([makePermit()])
    vi.mocked(qualifyNewBusiness).mockResolvedValue(makeQualification() as never)
    vi.mocked(findOrCreateEntity).mockResolvedValue({
      entity: { id: 'entity-001', name: 'Desert Bloom Florist' },
    } as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
  })

  it('qualifies a permit and writes to D1', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.qualified).toBe(1)
    expect(body.written).toBe(1)
    expect(appendContext).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// Tests: not_recommended qualification
// ---------------------------------------------------------------------------

describe('new-business not_recommended qualification', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchAllPermits).mockResolvedValue([makePermit()])
    vi.mocked(qualifyNewBusiness).mockResolvedValue(
      makeQualification({ outreach_timing: 'not_recommended' }) as never
    )
  })

  it('disqualifies and skips D1 write', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.disqualified).toBe(1)
    expect(body.written).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: Claude failure
// ---------------------------------------------------------------------------

describe('new-business Claude failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchAllPermits).mockResolvedValue([makePermit()])
    vi.mocked(qualifyNewBusiness).mockResolvedValue(null)
  })

  it('records error and skips D1 write', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    const body = (await res.json()) as Record<string, unknown>
    expect(body.errors).toBeGreaterThan(0)
    expect(body.written).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: scheduled handler
// ---------------------------------------------------------------------------

describe('new-business scheduled handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(fetchAllPermits).mockResolvedValue([])
  })

  it('runs without error on scheduled trigger', async () => {
    await expect(
      worker.scheduled({} as ScheduledController, makeEnv(), makeCtx())
    ).resolves.toBeUndefined()
  })
})
