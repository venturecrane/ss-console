/**
 * Behavioural tests for POST /api/contact, the public contact form.
 *
 * Until 2026-09-11 this file matched the route's source text (review
 * 2026-09-10, Testing 3). The route now runs against an in-memory KV for
 * the rate limiter and a mocked email transport, and the assertions are on
 * the responses and on what reaches the transport.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { bindEnv, jsonRequest, memoryKv, readJson } from './_stubs/behavioural'

const sendEmail = vi.fn()
vi.mock('../src/lib/email/resend', () => ({
  sendEmail: (...args: unknown[]) => sendEmail(...args),
}))

// Import AFTER the mock so the route binds the mocked transport.
import { POST } from '../src/pages/api/contact'

const URL_ = 'https://smd.services/api/contact'
const GOOD = { name: 'Dana Reyes', email: 'dana@example.com', message: 'Hello\nthere' }

async function call(body: unknown, ip = '203.0.113.7'): Promise<Response> {
  // A string body is sent verbatim (the malformed-JSON cases); anything else is serialized.
  const request = jsonRequest(URL_, body, { 'cf-connecting-ip': ip })
  return await POST({ request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0])
}

describe('POST /api/contact', () => {
  beforeEach(() => {
    sendEmail.mockReset()
    sendEmail.mockResolvedValue({ success: true, id: 'email-1' })
    bindEnv({ BOOKING_CACHE: memoryKv(), RESEND_API_KEY: 're_test' })
  })

  it('sends the notification to the team with the sender as reply-to, escaped, and answers ok', async () => {
    const res = await call({ ...GOOD, message: 'Hi <b>there</b>\nsecond line' })
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true })

    expect(sendEmail).toHaveBeenCalledTimes(1)
    const [apiKey, payload] = sendEmail.mock.calls[0] as [
      string,
      { to: string; reply_to: string; subject: string; html: string },
    ]
    expect(apiKey).toBe('re_test')
    expect(payload.to).toBe('team@smd.services')
    expect(payload.reply_to).toBe('dana@example.com')
    expect(payload.subject).toBe('Contact form: Dana Reyes')
    expect(payload.html).toContain('&lt;b&gt;there&lt;/b&gt;')
    expect(payload.html).toContain('<br>second line')
    expect(payload.html).not.toContain('<b>there</b>')
  })

  it('a filled honeypot is answered ok and silently dropped', async () => {
    const res = await call({ ...GOOD, website: 'http://spam.example' })
    expect(res.status).toBe(200)
    expect(sendEmail).not.toHaveBeenCalled()
  })

  it('a body that is not JSON is invalid_json', async () => {
    const res = await call('{not json')
    expect(res.status).toBe(400)
    expect(await res.json()).toMatchObject({ error: 'invalid_json' })
  })

  it('validation names each failing field: required, length, control characters, address shape', async () => {
    const res = await call({ name: '', email: 'not-an-email', message: 'x'.repeat(5001) })
    expect(res.status).toBe(400)
    const body = await readJson<{ error: string; fields: Record<string, string> }>(res)
    expect(body.error).toBe('validation_failed')
    expect(Object.keys(body.fields).sort()).toEqual(['email', 'message', 'name'])
    expect(sendEmail).not.toHaveBeenCalled()

    const control = await call({ ...GOOD, name: 'Dana\r\nBcc: x' })
    const controlBody = await readJson<{ fields: Record<string, string> }>(control)
    expect(controlBody.fields).toEqual({ name: 'Name contains invalid characters' })
  })

  it('a transport failure is reported as unavailable, never as ok', async () => {
    sendEmail.mockResolvedValue({ success: false, error: 'resend down' })
    const res = await call(GOOD)
    expect(res.status).toBe(500)
    expect(await res.json()).toMatchObject({ error: 'unavailable' })
  })

  it('the fourth request from one address within the hour is rate_limited, before the body is even read', async () => {
    for (let i = 0; i < 3; i++) expect((await call(GOOD)).status).toBe(200)
    const blocked = await call('{not json')
    expect(blocked.status).toBe(429)
    expect(await blocked.json()).toMatchObject({ error: 'rate_limited' })
    expect(sendEmail).toHaveBeenCalledTimes(3)

    const otherAddress = await call(GOOD, '198.51.100.2')
    expect(otherAddress.status).toBe(200)
  })

  it('with no rate-limit store bound, the endpoint refuses rather than opens (fails closed)', async () => {
    bindEnv({ RESEND_API_KEY: 're_test' })
    const res = await call(GOOD)
    expect(res.status).toBe(429)
    expect(sendEmail).not.toHaveBeenCalled()
  })
})
