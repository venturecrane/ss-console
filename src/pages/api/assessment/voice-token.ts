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
    return errorResponse(503, 'unavailable', 'Voice is temporarily unavailable.')

  try {
    const res = await fetch(`${SIGNED_URL_ENDPOINT}?agent_id=${encodeURIComponent(agentId)}`, {
      headers: { 'xi-api-key': apiKey },
    })
    if (!res.ok) return errorResponse(502, 'unavailable', 'Could not start the voice session.')
    const data: unknown = await res.json()
    const signedUrl =
      typeof data === 'object' &&
      data !== null &&
      typeof (data as { signed_url?: unknown }).signed_url === 'string'
        ? (data as { signed_url: string }).signed_url
        : null
    if (!signedUrl) return errorResponse(502, 'unavailable', 'Could not start the voice session.')
    return jsonResponse(200, { signedUrl })
  } catch {
    return errorResponse(502, 'unavailable', 'Could not start the voice session.')
  }
}
