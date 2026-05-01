/**
 * Tests for the /api/scan/verify endpoint's service-binding dispatch
 * path (#618; original test scope #614).
 *
 * The original #614 implementation called `env.SCAN_WORKFLOW.create(...)`
 * directly. Per #618 the Workflow now lives on a separate Worker
 * (`workers/scan-workflow/`) and ss-web invokes it through a service
 * binding (`env.SCAN_WORKFLOW_SERVICE.fetch(...)`). The test suite below
 * mirrors that change: every assertion runs against the new service-
 * binding-shaped mock, not the old direct-create mock.
 *
 * Asserts:
 *
 *   - When SCAN_WORKFLOW_SERVICE is bound and the dispatch endpoint
 *     returns `{ ok: true, workflow_run_id }`, verify persists the
 *     returned id to scan_requests.workflow_run_id
 *   - When SCAN_WORKFLOW_SERVICE is not bound (dev / test), verify falls
 *     back to the legacy ctx.waitUntil path so local development still
 *     works
 *   - When the dispatch fetch throws, verify falls back to ctx.waitUntil
 *     so the scan is never lost
 *   - When the dispatch returns a non-2xx response, verify falls back
 *   - When the dispatch returns 2xx but a malformed payload, verify
 *     falls back
 *   - The public response shape is unchanged (uniform { ok, status })
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

// The legacy fallback path imports runDiagnosticScan; mock it so the
// fallback short-circuits without touching enrichment modules.
vi.mock('../src/lib/diagnostic', async () => {
  const actual =
    await vi.importActual<typeof import('../src/lib/diagnostic')>('../src/lib/diagnostic')
  return {
    ...actual,
    runDiagnosticScan: vi.fn().mockResolvedValue({
      scan_request_id: 'fallback',
      status: 'completed',
      entity_id: null,
      thin_footprint_skipped: false,
      modules_ran: [],
      email_sent: false,
    }),
  }
})

import { env as testEnv } from 'cloudflare:workers'
import { GET, POST } from '../src/pages/api/scan/verify'
import { createScanRequest, getScanRequest } from '../src/lib/db/scan-requests'
import { generateScanToken } from '../src/lib/scan/tokens'
import { runDiagnosticScan } from '../src/lib/diagnostic'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1() as unknown as D1Database
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

function resetEnv() {
  const e = testEnv as unknown as Record<string, unknown>
  for (const k of Object.keys(e)) delete e[k]
}

/**
 * Build a service-binding-shaped mock whose `fetch` returns a JSON
 * response. The verify endpoint expects either a 2xx with
 * `{ ok: true, workflow_run_id: '...' }` (success) or anything else
 * (treated as failure → fallback).
 */
function makeServiceBinding(handler: (req: Request) => Promise<Response> | Response) {
  return {
    fetch: vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const req =
        input instanceof Request
          ? input
          : new Request(typeof input === 'string' ? input : input.toString(), init)
      return handler(req)
    }),
  }
}

describe('/api/scan/verify — service-binding dispatch (#618)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
    resetEnv()
    Object.assign(testEnv, { DB: db })
    vi.clearAllMocks()
  })

  it('dispatches via service binding and persists workflow_run_id when bound', async () => {
    const binding = makeServiceBinding(
      async () =>
        new Response(JSON.stringify({ ok: true, workflow_run_id: 'wf-12345' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    Object.assign(testEnv, { SCAN_WORKFLOW_SERVICE: binding })

    const { token, hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })

    const url = new URL(`https://smd.services/api/scan/verify?token=${token}`)
    const response = await GET({
      url,
      locals: { session: null },
    } as never)
    expect(response.status).toBe(200)
    const body = (await response.json()) as { ok: boolean; status: string; domain: string }
    expect(body.ok).toBe(true)
    expect(body.status).toBe('verified')
    expect(body.domain).toBe('example.com')

    // The dispatch endpoint received exactly one call with the right body.
    expect(binding.fetch).toHaveBeenCalledTimes(1)
    const [reqUrl, reqInit] = binding.fetch.mock.calls[0]
    expect(reqUrl).toBe('https://internal/dispatch')
    expect(reqInit?.method).toBe('POST')
    expect(JSON.parse(String(reqInit?.body))).toEqual({ scanRequestId: row.id })

    // Workflow id was persisted.
    const updated = await getScanRequest(db, row.id)
    expect(updated?.workflow_run_id).toBe('wf-12345')
    expect(updated?.scan_status).toBe('verified')

    // Fallback path was NOT invoked.
    expect(vi.mocked(runDiagnosticScan)).not.toHaveBeenCalled()
  })

  it('falls back to ctx.waitUntil when SCAN_WORKFLOW_SERVICE is not bound', async () => {
    // No SCAN_WORKFLOW_SERVICE in env.
    const { token, hash } = await generateScanToken()
    await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })

    const waitUntil = vi.fn()
    const response = await POST({
      request: new Request('https://smd.services/api/scan/verify', {
        method: 'POST',
        body: JSON.stringify({ token }),
      }),
      locals: { session: null, cfContext: { waitUntil } as never },
    } as never)
    expect(response.status).toBe(200)
    const body = (await response.json()) as { ok: boolean; status: string }
    expect(body.ok).toBe(true)
    expect(body.status).toBe('verified')

    // ctx.waitUntil received the fallback promise.
    expect(waitUntil).toHaveBeenCalledTimes(1)
    expect(vi.mocked(runDiagnosticScan)).toHaveBeenCalledTimes(1)
  })

  // PR-2a contract change: when SCAN_WORKFLOW_SERVICE is BOUND but the
  // dispatch fails (throw / 5xx / malformed), the verify-handler must
  // mark the scan as failed instead of falling back to runDiagnosticScan.
  // The previous "fall back to ctx.waitUntil" behavior re-created the
  // original 30s-isolate-budget bug (#614) on any transient binding
  // failure. New contract: dispatch failure → scan_status='failed', no
  // legacy fallback call, no waitUntil invocation.
  it('marks scan failed when service binding fetch throws (no runDiagnosticScan fallback)', async () => {
    const binding = makeServiceBinding(async () => {
      throw new Error('binding offline')
    })
    Object.assign(testEnv, { SCAN_WORKFLOW_SERVICE: binding })

    const { token, hash } = await generateScanToken()
    const created = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })

    const waitUntil = vi.fn()
    const url = new URL(`https://smd.services/api/scan/verify?token=${token}`)
    const response = await GET({
      url,
      locals: { session: null, cfContext: { waitUntil } as never },
    } as never)
    expect(response.status).toBe(400)
    const body = (await response.json()) as { ok: boolean; status: string; domain: string }
    expect(body.ok).toBe(false)
    expect(body.status).toBe('failed')
    expect(body.domain).toBe('example.com')

    // Dispatch was attempted; legacy fallback path was NOT invoked.
    expect(binding.fetch).toHaveBeenCalledTimes(1)
    expect(waitUntil).not.toHaveBeenCalled()
    expect(vi.mocked(runDiagnosticScan)).not.toHaveBeenCalled()

    // The scan_request row carries the failure detail.
    const updated = await getScanRequest(db, created.id)
    expect(updated?.scan_status).toBe('failed')
    expect(updated?.error_message).toMatch(/dispatch_failed: binding offline/)
  })

  it('marks scan failed when dispatch returns non-2xx (no runDiagnosticScan fallback)', async () => {
    const binding = makeServiceBinding(
      async () =>
        new Response(JSON.stringify({ ok: false, error: 'workflow_binding_missing' }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    Object.assign(testEnv, { SCAN_WORKFLOW_SERVICE: binding })

    const { token, hash } = await generateScanToken()
    const created = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })

    const waitUntil = vi.fn()
    const url = new URL(`https://smd.services/api/scan/verify?token=${token}`)
    const response = await GET({
      url,
      locals: { session: null, cfContext: { waitUntil } as never },
    } as never)
    expect(response.status).toBe(400)
    const body = (await response.json()) as { ok: boolean; status: string }
    expect(body.ok).toBe(false)
    expect(body.status).toBe('failed')

    expect(binding.fetch).toHaveBeenCalledTimes(1)
    expect(waitUntil).not.toHaveBeenCalled()
    expect(vi.mocked(runDiagnosticScan)).not.toHaveBeenCalled()

    const updated = await getScanRequest(db, created.id)
    expect(updated?.scan_status).toBe('failed')
    expect(updated?.error_message).toMatch(/dispatch_failed:.*500/)
  })

  it('marks scan failed when dispatch returns 2xx but malformed payload (no fallback)', async () => {
    const binding = makeServiceBinding(
      async () =>
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    )
    Object.assign(testEnv, { SCAN_WORKFLOW_SERVICE: binding })

    const { token, hash } = await generateScanToken()
    const created = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })

    const waitUntil = vi.fn()
    const url = new URL(`https://smd.services/api/scan/verify?token=${token}`)
    const response = await GET({
      url,
      locals: { session: null, cfContext: { waitUntil } as never },
    } as never)
    expect(response.status).toBe(400)
    const body = (await response.json()) as { ok: boolean; status: string }
    expect(body.status).toBe('failed')

    expect(binding.fetch).toHaveBeenCalledTimes(1)
    expect(waitUntil).not.toHaveBeenCalled()
    expect(vi.mocked(runDiagnosticScan)).not.toHaveBeenCalled()

    const updated = await getScanRequest(db, created.id)
    expect(updated?.scan_status).toBe('failed')
    expect(updated?.error_message).toMatch(/dispatch_failed:.*workflow_run_id/)
  })

  it('returns invalid_token without invoking the workflow on a bad token', async () => {
    const binding = makeServiceBinding(
      async () => new Response(JSON.stringify({ ok: true }), { status: 200 })
    )
    Object.assign(testEnv, { SCAN_WORKFLOW_SERVICE: binding })

    const url = new URL('https://smd.services/api/scan/verify?token=not-a-real-token')
    const response = await GET({
      url,
      locals: { session: null },
    } as never)
    expect(response.status).toBe(400)
    const body = (await response.json()) as { ok: boolean; status: string }
    expect(body.ok).toBe(false)
    expect(body.status).toBe('invalid_token')
    expect(body).not.toHaveProperty('domain')
    expect(binding.fetch).not.toHaveBeenCalled()
  })
})
