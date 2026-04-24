import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { Env } from './index'

// ---------------------------------------------------------------------------
// Mock parent-repo imports
// ---------------------------------------------------------------------------

vi.mock('../../../src/lib/constants.js', () => ({
  ORG_ID: 'org-test-001',
  SYSTEM_ENTITY_ID: 'system-entity-001',
}))

vi.mock('../../../src/lib/db/context.js', () => ({
  appendContext: vi.fn(),
}))

vi.mock('../../../src/lib/db/generators.js', () => ({
  getGeneratorConfig: vi.fn(),
  recordGeneratorRun: vi.fn(),
}))

// ---------------------------------------------------------------------------
// Mock global fetch (Reddit OAuth + search + Resend)
// ---------------------------------------------------------------------------

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// ---------------------------------------------------------------------------
// Imports (after mocks)
// ---------------------------------------------------------------------------

import worker from './index'
import { getGeneratorConfig, recordGeneratorRun } from '../../../src/lib/db/generators.js'
import { appendContext } from '../../../src/lib/db/context.js'

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function makeEnv(overrides: Partial<Env> = {}): Env {
  return {
    DB: makeMockD1(),
    REDDIT_CLIENT_ID: 'test-reddit-client-id',
    REDDIT_CLIENT_SECRET: 'test-reddit-client-secret',
    REDDIT_USERNAME: 'test-reddit-user',
    REDDIT_PASSWORD: 'test-reddit-pass',
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
      search_queries: ['small business owner overwhelmed', 'need operations help Phoenix'],
    },
  }
}

function makeRedditTokenResponse() {
  return new Response(JSON.stringify({ access_token: 'test-reddit-token' }), { status: 200 })
}

function makeRedditSearchResponse(posts = makeRedditPosts()) {
  return new Response(
    JSON.stringify({
      data: {
        children: posts,
      },
    }),
    { status: 200 }
  )
}

function makeRedditPosts() {
  return [
    {
      data: {
        id: 'post-abc-001',
        title: 'I cannot keep up with my business anymore',
        subreddit: 'smallbusiness',
        permalink: '/r/smallbusiness/comments/post-abc-001',
        selftext: 'I run a 10-person plumbing company and I am drowning in admin work.',
        score: 15,
        created_utc: 1745000000,
      },
    },
  ]
}

function makeResendOkResponse() {
  return new Response(JSON.stringify({ id: 'email-001' }), { status: 200 })
}

// ---------------------------------------------------------------------------
// Tests: fetch handler auth
// ---------------------------------------------------------------------------

describe('social-listening fetch handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
    // 2 queries both returning empty lists
    mockFetch
      .mockResolvedValueOnce(makeRedditTokenResponse())
      .mockResolvedValueOnce(makeRedditSearchResponse([]))
      .mockResolvedValueOnce(makeRedditSearchResponse([]))
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
      totalPosts: expect.any(Number),
      stored: expect.any(Number),
    })
  })
})

// ---------------------------------------------------------------------------
// Tests: disabled generator
// ---------------------------------------------------------------------------

describe('social-listening disabled generator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeDisabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
  })

  it('skips run when disabled without calling Reddit', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.stored).toBe(0)
    expect(mockFetch).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: happy path — new posts discovered and stored
// ---------------------------------------------------------------------------

describe('social-listening happy path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
    mockFetch
      .mockResolvedValueOnce(makeRedditTokenResponse())
      .mockResolvedValueOnce(makeRedditSearchResponse()) // query 1: 1 post
      .mockResolvedValueOnce(makeRedditSearchResponse([])) // query 2: same post ID would dedup, so empty
      .mockResolvedValueOnce(makeResendOkResponse()) // Resend digest
  })

  it('stores new posts and sends digest', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.stored).toBe(1)
    expect(body.newPosts).toBe(1)
    expect(appendContext).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// Tests: Reddit OAuth failure
// ---------------------------------------------------------------------------

describe('social-listening Reddit auth failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    mockFetch.mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401, statusText: 'Unauthorized' })
    )
  })

  it('records auth error and returns degraded summary without crashing', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.errors).toBeGreaterThan(0)
    expect(body.stored).toBe(0)
    expect(appendContext).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Tests: Reddit search failure (after successful auth)
// ---------------------------------------------------------------------------

describe('social-listening Reddit search failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
    mockFetch
      .mockResolvedValueOnce(makeRedditTokenResponse())
      .mockResolvedValueOnce(
        new Response('Service Unavailable', { status: 503, statusText: 'Service Unavailable' })
      )
      .mockResolvedValueOnce(
        new Response('Service Unavailable', { status: 503, statusText: 'Service Unavailable' })
      )
  })

  it('records search errors and returns degraded summary', async () => {
    const res = await worker.fetch(makeRequest('Bearer sk-test-ingest-key'), makeEnv(), makeCtx())
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.errors).toBeGreaterThan(0)
    expect(body.stored).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// Tests: scheduled handler
// ---------------------------------------------------------------------------

describe('social-listening scheduled handler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    vi.mocked(getGeneratorConfig).mockResolvedValue(makeEnabledConfig() as never)
    vi.mocked(recordGeneratorRun).mockResolvedValue(undefined as never)
    vi.mocked(appendContext).mockResolvedValue(undefined as never)
    mockFetch
      .mockResolvedValueOnce(makeRedditTokenResponse())
      .mockResolvedValueOnce(makeRedditSearchResponse([]))
      .mockResolvedValueOnce(makeRedditSearchResponse([]))
  })

  it('runs without error on scheduled trigger', async () => {
    await expect(
      worker.scheduled({} as ScheduledController, makeEnv(), makeCtx())
    ).resolves.toBeUndefined()
  })
})
