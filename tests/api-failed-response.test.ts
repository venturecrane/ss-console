/**
 * failedResponse and misconfiguredResponse (src/lib/api/helpers.ts): the two
 * ways a route answers a 5xx, and both reach Sentry before they respond.
 *
 * What would make this false: a 5xx body without the catalog code, or a
 * capture call that never happens (the review's finding was exactly that:
 * 500s logged to a Worker log nobody pages on).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../src/lib/observability/sentry', () => ({
  captureError: vi.fn(),
  captureWarning: vi.fn(),
}))

import { captureError, captureWarning } from '../src/lib/observability/sentry'
import { failedResponse, misconfiguredResponse } from '../src/lib/api/failures'

beforeEach(() => {
  vi.mocked(captureError).mockClear()
  vi.mocked(captureWarning).mockClear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('failedResponse', () => {
  it('captures the error under the area tag and answers 500 internal_error by default', async () => {
    const boom = new Error('db down')
    const res = failedResponse(boom, 'api/intake')
    expect(res.status).toBe(500)
    expect(await res.json()).toEqual({ error: 'internal_error', message: 'Internal server error.' })
    expect(captureError).toHaveBeenCalledTimes(1)
    expect(captureError).toHaveBeenCalledWith(boom, 'api/intake')
    expect(console.error).toHaveBeenCalledWith('[api/intake]', boom)
  })

  it('passes status, code, the site wording, and extra keys through', async () => {
    const res = failedResponse(new Error('x'), 'api/admin/entities/send-booking-link', {
      status: 502,
      code: 'signing_failed',
      message: 'Could not sign.',
      extra: { stage: 'proposing' },
    })
    expect(res.status).toBe(502)
    expect(await res.json()).toEqual({
      error: 'signing_failed',
      message: 'Could not sign.',
      stage: 'proposing',
    })
    expect(captureError).toHaveBeenCalledWith(
      expect.any(Error),
      'api/admin/entities/send-booking-link'
    )
  })

  it('captures a non-Error value too: a vendor result object is still the failure', () => {
    const vendor = { name: 'validation_error', message: 'bad from' }
    failedResponse(vendor, 'api/contact', { code: 'unavailable' })
    expect(captureError).toHaveBeenCalledWith(vendor, 'api/contact')
  })
})

describe('misconfiguredResponse', () => {
  it('captures a warning naming the missing secret and answers server_misconfigured', async () => {
    const res = misconfiguredResponse('webhook/stripe', 'STRIPE_WEBHOOK_SECRET')
    expect(res.status).toBe(500)
    expect(await res.json()).toEqual({
      error: 'server_misconfigured',
      message: 'Server misconfigured.',
    })
    expect(captureWarning).toHaveBeenCalledWith(
      'STRIPE_WEBHOOK_SECRET not configured',
      'webhook/stripe'
    )
    expect(captureError).not.toHaveBeenCalled()
  })

  it('a public route can answer 503 unavailable in its own words while the warning still lands', async () => {
    const res = misconfiguredResponse('api/assessment/turn', 'ANTHROPIC_API_KEY', {
      status: 503,
      code: 'unavailable',
      message: 'Assessment is temporarily unavailable.',
    })
    expect(res.status).toBe(503)
    expect(await res.json()).toEqual({
      error: 'unavailable',
      message: 'Assessment is temporarily unavailable.',
    })
    expect(captureWarning).toHaveBeenCalledWith(
      'ANTHROPIC_API_KEY not configured',
      'api/assessment/turn'
    )
  })
})
