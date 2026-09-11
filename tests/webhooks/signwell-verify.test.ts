/**
 * Behavioral tests for the SignWell webhook HMAC verification path.
 *
 * SignWell signs the string "{event_type}@{event_time}" using the webhook
 * ID as the HMAC-SHA256 key and embeds the hex digest in the JSON body
 * at `event.hash`. Ref: https://developers.signwell.com/reference/event-hash-verification
 *
 * The verification function (`verifyEventHash`) is file-private to
 * src/pages/api/webhooks/signwell.ts, so we exercise it through the route
 * handler. We pick a non-dispatching event type (`document_viewed`) so
 * the route returns 200 immediately after verification — no D1, R2, or
 * downstream-handler seeding required for what is fundamentally a
 * crypto-boundary test.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { installWorkerdPolyfills } from '@venturecrane/crane-test-harness'
import { env as testEnv } from 'cloudflare:workers'
import { POST } from '../../src/pages/api/webhooks/signwell'

installWorkerdPolyfills()

const SECRET = 'test-signwell-webhook-secret'
const WRONG_SECRET = 'wrong-secret-totally-different'

// ---------------------------------------------------------------------------
// HMAC helper — must match what SignWell does on its end:
//   signed_string = `${type}@${time}`
//   hash = HMAC_SHA256(secret, signed_string).hex()
// ---------------------------------------------------------------------------

async function computeSignWellHash(type: string, time: number, secret: string): Promise<string> {
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, enc.encode(`${type}@${time}`))
  return Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000)
}

interface BuildPayloadOpts {
  type?: string
  time?: number
  hash?: string | null
  /** When `hash` is undefined we compute one with `secret`. */
  secret?: string
}

async function buildPayload(opts: BuildPayloadOpts = {}): Promise<Record<string, unknown>> {
  const type = opts.type ?? 'document_viewed'
  const time = opts.time ?? nowSeconds()
  const hash =
    opts.hash !== undefined
      ? opts.hash
      : await computeSignWellHash(type, time, opts.secret ?? SECRET)
  // Spread `null` / undefined so we can test the missing-hash case.
  const event: Record<string, unknown> = { type, time }
  if (hash !== null) event.hash = hash
  return {
    event,
    data: {
      object: { id: 'sw-doc-test', name: 'Test', status: 'viewed', completed_at: null },
      account_id: 'test-account',
    },
  }
}

async function parseJson<T>(res: Response): Promise<T> {
  return res.json()
}

function buildContext(body: unknown) {
  const request = new Request('http://test.local/api/webhooks/signwell', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return {
    request,
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof POST>[0]
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('POST /api/webhooks/signwell — HMAC verification', () => {
  beforeEach(() => {
    Object.assign(testEnv, { SIGNWELL_WEBHOOK_SECRET: SECRET })
    // Negative paths in this handler log via console.error before returning
    // 4xx/500. Those logs are intentional but produce noise during test runs;
    // suppress them so the report stays focused on assertion results.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[k]
    }
    vi.restoreAllMocks()
  })

  it('accepts a payload with a valid hash + fresh timestamp (returns 200)', async () => {
    const payload = await buildPayload()
    const res = await POST(buildContext(payload))
    expect(res.status).toBe(200)
  })

  it('rejects a payload whose hash has been tampered with (single bit flip)', async () => {
    const payload = await buildPayload()
    // Flip a single bit in the hash — last hex char toggled to its neighbor.
    // SignWell hash is a 64-char hex string; flipping one char invalidates HMAC.
    const event = (payload as { event: { hash: string } }).event
    const lastChar = event.hash.slice(-1)
    const flipped = lastChar === '0' ? '1' : '0'
    event.hash = event.hash.slice(0, -1) + flipped

    const res = await POST(buildContext(payload))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a payload signed with the wrong secret', async () => {
    const payload = await buildPayload({ secret: WRONG_SECRET })
    const res = await POST(buildContext(payload))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('invalid_signature')
  })

  it('rejects a payload missing the hash field', async () => {
    const payload = await buildPayload({ hash: null })
    const res = await POST(buildContext(payload))
    expect(res.status).toBe(400)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('validation_failed')
  })

  it('rejects a stale payload (timestamp older than 5 minutes)', async () => {
    // Build a payload whose hash is correct for an old timestamp — that way
    // we exercise the freshness check, not the hash check.
    const staleTime = nowSeconds() - 6 * 60
    const payload = await buildPayload({ time: staleTime })
    const res = await POST(buildContext(payload))
    expect(res.status).toBe(401)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('stale')
  })

  it('returns 500 when the webhook secret is not configured (defense in depth)', async () => {
    delete (testEnv as unknown as Record<string, unknown>).SIGNWELL_WEBHOOK_SECRET
    const payload = await buildPayload()
    const res = await POST(buildContext(payload))
    expect(res.status).toBe(500)
  })

  it('rejects a signed document_completed payload with a malformed document object', async () => {
    const payload = await buildPayload({ type: 'document_completed' })
    ;(payload.data as { object: Record<string, unknown> }).object = {
      name: 'Missing ID',
      status: 'completed',
      completed_at: null,
    }

    const res = await POST(buildContext(payload))

    expect(res.status).toBe(400)
    const json = await parseJson<{ error: string }>(res)
    expect(json.error).toBe('validation_failed')
  })
})
