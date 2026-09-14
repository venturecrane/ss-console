/**
 * GET /api/assessment/voice-token
 *
 * Mints a short-lived signed URL the browser widget uses to connect to the
 * private ElevenLabs assessment agent (ADR 0039 node [1], voice channel). The
 * API key stays server-side; the browser never sees it. Rate-limited.
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { jsonResponse, errorResponse } from '../../../lib/api/helpers'
import { failedResponse, misconfiguredResponse } from '../../../lib/api/failures'

const RATE_LIMIT_PER_HOUR = 60
const SIGNED_URL_ENDPOINT = 'https://api.elevenlabs.io/v1/convai/conversation/get-signed-url'

export const GET: APIRoute = async ({ clientAddress }: APIContext) => {
  const rate = await rateLimitByIp(
    env.BOOKING_CACHE,
    'assessment_voice_token',
    clientAddress,
    RATE_LIMIT_PER_HOUR
  )
  if (!rate.allowed) return errorResponse(429, 'rate_limited')

  const apiKey = env.ELEVENLABS_API_KEY
  const agentId = env.ELEVENLABS_ASSESSMENT_AGENT_ID
  if (!apiKey || !agentId)
    return misconfiguredResponse(
      'api/assessment/voice-token',
      'ELEVENLABS_API_KEY or ELEVENLABS_ASSESSMENT_AGENT_ID',
      { status: 503, code: 'unavailable', message: 'Voice is temporarily unavailable.' }
    )

  const upstream = { status: 502 as const, code: 'unavailable' as const }
  const couldNotStart = 'Could not start the voice session.'
  try {
    const res = await fetch(`${SIGNED_URL_ENDPOINT}?agent_id=${encodeURIComponent(agentId)}`, {
      headers: { 'xi-api-key': apiKey },
    })
    if (!res.ok) {
      return failedResponse(
        new Error(`signed-url endpoint answered ${res.status}`),
        'api/assessment/voice-token',
        { ...upstream, message: couldNotStart }
      )
    }
    const data: unknown = await res.json()
    const signedUrl =
      typeof data === 'object' &&
      data !== null &&
      typeof (data as { signed_url?: unknown }).signed_url === 'string'
        ? (data as { signed_url: string }).signed_url
        : null
    if (!signedUrl) {
      return failedResponse(
        new Error('signed-url endpoint answered 200 without signed_url'),
        'api/assessment/voice-token',
        { ...upstream, message: couldNotStart }
      )
    }
    return jsonResponse(200, { signedUrl })
  } catch (err) {
    return failedResponse(err, 'api/assessment/voice-token', {
      ...upstream,
      message: couldNotStart,
    })
  }
}
