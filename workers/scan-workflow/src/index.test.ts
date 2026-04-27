/**
 * Tests for the ss-scan-workflow Worker's `/dispatch` endpoint (#618).
 *
 * The Worker exposes a single internal endpoint (POST /dispatch) that
 * ss-web invokes via service binding. The endpoint creates a Cloudflare
 * Workflow instance via `env.SCAN_WORKFLOW.create({ params })` and
 * returns the instance id.
 *
 * Tests are pure unit tests: we instantiate the default fetch handler
 * with a mock `env` (Workflows binding stubbed) and assert on responses.
 * The Workflow class itself is exercised in
 * `tests/diagnostic-workflow.test.ts` in the parent project.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the cross-repo workflow import so vitest doesn't need to resolve
// the entire diagnostic dependency tree (which imports cloudflare:workers
// at the top — a runtime-only module that fails resolution under Node).
vi.mock('../../../src/lib/diagnostic/workflow.js', () => ({
  ScanDiagnosticWorkflow: class StubScanDiagnosticWorkflow {},
}))

import worker, { type Env } from './index'

function makeEnv(overrides: Partial<Env> = {}): Env {
  const create = vi.fn().mockResolvedValue({ id: 'wf-test-001' })
  return {
    DB: {} as never,
    SCAN_WORKFLOW: { create },
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

describe('ss-scan-workflow Worker', () => {
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
      const res = await dispatch(env, { scanRequestId: 'scan-123' })
      expect(res.status).toBe(200)
      const body = (await res.json()) as { ok: boolean; workflow_run_id: string }
      expect(body.ok).toBe(true)
      expect(body.workflow_run_id).toBe('wf-test-001')

      const create = env.SCAN_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).toHaveBeenCalledTimes(1)
      expect(create).toHaveBeenCalledWith({ params: { scanRequestId: 'scan-123' } })
    })

    it('rejects non-POST methods', async () => {
      const env = makeEnv()
      // GET cannot have a body; bypass `dispatch()` helper.
      const res = await worker.fetch(
        new Request('https://internal/dispatch', { method: 'GET' }),
        env,
        {} as ExecutionContext
      )
      expect(res.status).toBe(405)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('method_not_allowed')
    })

    it('rejects missing scanRequestId', async () => {
      const env = makeEnv()
      const res = await dispatch(env, {})
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('missing_scan_request_id')

      const create = env.SCAN_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).not.toHaveBeenCalled()
    })

    it('rejects non-string scanRequestId', async () => {
      const env = makeEnv()
      const res = await dispatch(env, { scanRequestId: 12345 })
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('missing_scan_request_id')
    })

    it('rejects malformed JSON body', async () => {
      const env = makeEnv()
      const res = await dispatch(env, '{not valid json')
      expect(res.status).toBe(400)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('invalid_json')
    })

    it('returns 500 when SCAN_WORKFLOW binding is missing at runtime', async () => {
      // Simulate the misconfiguration #618 was filed for: binding not present.
      const env = makeEnv({ SCAN_WORKFLOW: undefined as never })
      const res = await dispatch(env, { scanRequestId: 'scan-123' })
      expect(res.status).toBe(500)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('workflow_binding_missing')
    })

    it('returns 500 when SCAN_WORKFLOW.create rejects', async () => {
      const env = makeEnv({
        SCAN_WORKFLOW: {
          create: vi.fn().mockRejectedValue(new Error('quota exceeded')),
        },
      })
      const res = await dispatch(env, { scanRequestId: 'scan-123' })
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
        { scanRequestId: 'scan-456' },
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
        { scanRequestId: 'scan-456' },
        { headers: { Authorization: 'Bearer wrong-secret' } }
      )
      expect(res.status).toBe(401)
      const body = (await res.json()) as { ok: boolean; error: string }
      expect(body.error).toBe('unauthorized')

      const create = env.SCAN_WORKFLOW.create as unknown as ReturnType<typeof vi.fn>
      expect(create).not.toHaveBeenCalled()
    })

    it('rejects when Authorization header is sent but LEAD_INGEST_API_KEY is unset', async () => {
      const env = makeEnv({ LEAD_INGEST_API_KEY: undefined })
      const res = await dispatch(
        env,
        { scanRequestId: 'scan-456' },
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
