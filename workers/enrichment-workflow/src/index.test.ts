/**
 * Tests for the ss-enrichment-workflow Worker's `/dispatch` endpoint (#631).
 *
 * Same shape as ss-scan-workflow's tests: instantiate the default fetch
 * handler with a mock `env` (Workflows binding stubbed) and assert on
 * responses. The Workflow class itself is exercised in
 * `tests/enrichment-workflow.test.ts` in the parent project.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the cross-repo workflow import so vitest doesn't need to resolve
// the entire enrichment dependency tree (which imports cloudflare:workers
// at the top — a runtime-only module that fails resolution under Node).
vi.mock('../../../src/lib/enrichment/workflow.js', () => ({
  EnrichmentWorkflow: class StubEnrichmentWorkflow {},
}))

import worker, { type Env } from './index'

function makeEnv(overrides: Partial<Env> = {}): Env {
  const create = vi.fn().mockResolvedValue({ id: 'wf-test-001' })
  return {
    DB: {} as never,
    ENRICHMENT_WORKFLOW: { create },
    LEAD_INGEST_API_KEY: 'test-secret',
    ...overrides,
  } as Env
}

async function dispatch(
  env: Env,
  body: unknown,
  init: { method?: string; headers?: Record<string, string> } = {}
): Promise<Response> {
  return worker.fetch(
    new Request('https://internal/dispatch', {
      method: init.method ?? 'POST',
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
      body: typeof body === 'string' ? body : JSON.stringify(body),
    }),
    env,
    {} as ExecutionContext
  )
}

describe('ss-enrichment-workflow Worker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('GET /health', () => {
    it('returns 200 ok without auth', async () => {
      const env = makeEnv()
      const res = await worker.fetch(
        new Request('https://internal/health'),
        env,
        {} as ExecutionContext
      )
      expect(res.status).toBe(200)
      const body = (await res.json()) as { ok: boolean }
      expect(body.ok).toBe(true)
    })

    it('treats / the same as /health', async () => {
      const env = makeEnv()
      const res = await worker.fetch(new Request('https://internal/'), env, {} as ExecutionContext)
      expect(res.status).toBe(200)
    })
  })

  describe('POST /dispatch (service-binding path — no Authorization header)', () => {
    it('creates a Workflow instance and returns the id', async () => {
      const env = makeEnv()
      const res = await dispatch(env, {
        entityId: 'ent-123',
        orgId: 'org-1',
        mode: 'full',
        triggered_by: 'cron:new-business',
      })
      expect(res.status).toBe(200)
      const body = (await res.json()) as { ok: boolean; workflow_run_id: string }
      expect(body.ok).toBe(true)
      expect(body.workflow_run_id).toBe('wf-test-001')

      const create = env.ENRICHMENT_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).toHaveBeenCalledTimes(1)
      expect(create).toHaveBeenCalledWith({
        params: {
          entityId: 'ent-123',
          orgId: 'org-1',
          mode: 'full',
          triggered_by: 'cron:new-business',
        },
      })
    })

    it('accepts mode=reviews-and-news', async () => {
      const env = makeEnv()
      const res = await dispatch(env, {
        entityId: 'ent-123',
        orgId: 'org-1',
        mode: 'reviews-and-news',
        triggered_by: 'admin:re-enrich',
      })
      expect(res.status).toBe(200)
      const create = env.ENRICHMENT_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).toHaveBeenCalledWith({
        params: expect.objectContaining({ mode: 'reviews-and-news' }),
      })
    })

    it('defaults invalid mode to full', async () => {
      const env = makeEnv()
      const res = await dispatch(env, {
        entityId: 'ent-123',
        orgId: 'org-1',
        mode: 'bogus',
        triggered_by: 'test',
      })
      expect(res.status).toBe(200)
      const create = env.ENRICHMENT_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).toHaveBeenCalledWith({
        params: expect.objectContaining({ mode: 'full' }),
      })
    })

    it('rejects non-POST methods', async () => {
      const env = makeEnv()
      const res = await worker.fetch(
        new Request('https://internal/dispatch', { method: 'GET' }),
        env,
        {} as ExecutionContext
      )
      expect(res.status).toBe(405)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('method_not_allowed')
    })

    it('rejects missing entityId', async () => {
      const env = makeEnv()
      const res = await dispatch(env, { orgId: 'org-1', mode: 'full', triggered_by: 'test' })
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('missing_entity_id')

      const create = env.ENRICHMENT_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).not.toHaveBeenCalled()
    })

    it('rejects missing orgId', async () => {
      const env = makeEnv()
      const res = await dispatch(env, {
        entityId: 'ent-123',
        mode: 'full',
        triggered_by: 'test',
      })
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('missing_org_id')
    })

    it('rejects malformed JSON body', async () => {
      const env = makeEnv()
      const res = await dispatch(env, '{not valid json')
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('invalid_json')
    })

    it('returns 500 when ENRICHMENT_WORKFLOW binding is missing at runtime', async () => {
      const env = makeEnv({ ENRICHMENT_WORKFLOW: undefined as never })
      const res = await dispatch(env, {
        entityId: 'ent-123',
        orgId: 'org-1',
        mode: 'full',
        triggered_by: 'test',
      })
      expect(res.status).toBe(500)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('workflow_binding_missing')
    })

    it('returns 500 when ENRICHMENT_WORKFLOW.create rejects', async () => {
      const env = makeEnv({
        ENRICHMENT_WORKFLOW: {
          create: vi.fn().mockRejectedValue(new Error('quota exceeded')),
        },
      })
      const res = await dispatch(env, {
        entityId: 'ent-123',
        orgId: 'org-1',
        mode: 'full',
        triggered_by: 'test',
      })
      expect(res.status).toBe(500)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toMatch(/dispatch_failed/)
      expect(body.error).toContain('quota exceeded')
    })
  })

  describe('POST /dispatch (operator path — Authorization header present)', () => {
    it('accepts a matching bearer token', async () => {
      const env = makeEnv()
      const res = await dispatch(
        env,
        { entityId: 'ent-456', orgId: 'org-1', mode: 'full', triggered_by: 'manual' },
        { headers: { Authorization: 'Bearer test-secret' } }
      )
      expect(res.status).toBe(200)
      const body = (await res.json()) as { ok: boolean; workflow_run_id: string }
      expect(body.workflow_run_id).toBe('wf-test-001')
    })

    it('rejects a mismatched bearer token', async () => {
      const env = makeEnv()
      const res = await dispatch(
        env,
        { entityId: 'ent-456', orgId: 'org-1', mode: 'full', triggered_by: 'manual' },
        { headers: { Authorization: 'Bearer wrong-secret' } }
      )
      expect(res.status).toBe(401)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('unauthorized')
    })

    it('rejects when Authorization header is sent but LEAD_INGEST_API_KEY is unset', async () => {
      const env = makeEnv({ LEAD_INGEST_API_KEY: undefined })
      const res = await dispatch(
        env,
        { entityId: 'ent-456', orgId: 'org-1', mode: 'full', triggered_by: 'manual' },
        { headers: { Authorization: 'Bearer anything' } }
      )
      expect(res.status).toBe(401)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('auth_not_configured')
    })
  })

  describe('unknown paths', () => {
    it('returns 404 for unknown routes', async () => {
      const env = makeEnv()
      const res = await worker.fetch(
        new Request('https://internal/nope', { method: 'POST' }),
        env,
        {} as ExecutionContext
      )
      expect(res.status).toBe(404)
    })
  })
})
