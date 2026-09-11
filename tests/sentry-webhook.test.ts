/**
 * POST /api/webhooks/sentry: the replay window is a refusal, not a skip.
 *
 * Sentry signs the raw body only, so the timestamp header is the whole replay
 * defence. A missing or non-numeric header used to skip the staleness check
 * (2026-09-09 review, Security LOW 5); it is now a 401.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'
import { POST } from '../src/pages/api/webhooks/sentry'

const SECRET = 'sentry-webhook-secret-for-tests'
const BODY = JSON.stringify({ action: 'triggered', data: { event: { tags: [] } } })

async function sign(body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body))
  return Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function post(headers: Record<string, string>): Promise<Response> {
  const request = new Request('https://smd.services/api/webhooks/sentry', {
    method: 'POST',
    headers: { 'sentry-hook-signature': await sign(BODY), ...headers },
    body: BODY,
  })
  return POST({ request } as unknown as Parameters<typeof POST>[0])
}

describe('sentry webhook replay window', () => {
  beforeEach(() => {
    Object.assign(testEnv, { SENTRY_WEBHOOK_SECRET: SECRET })
  })
  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('refuses a validly signed body with no timestamp header', async () => {
    const res = await post({})
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'invalid_timestamp' })
  })

  it('refuses a validly signed body with a non-numeric timestamp', async () => {
    const res = await post({ 'sentry-hook-timestamp': 'abc' })
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'invalid_timestamp' })
  })

  it('refuses a validly signed body older than the window', async () => {
    const old = String(Math.floor(Date.now() / 1000) - 3600)
    const res = await post({ 'sentry-hook-timestamp': old })
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'stale' })
  })

  it('accepts a fresh timestamp and proceeds to payload validation', async () => {
    const fresh = String(Math.floor(Date.now() / 1000))
    const res = await post({ 'sentry-hook-timestamp': fresh })
    // Past the replay gate: the fixture body has no tenant tag, which is the
    // next check's refusal, and proves the timestamp was accepted.
    expect(res.status).toBe(400)
    expect(await res.json()).toMatchObject({ error: 'missing_tenant_tag' })
  })
})
