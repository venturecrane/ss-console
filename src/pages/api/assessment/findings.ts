/**
 * POST /api/assessment/findings
 *
 * Drafts the evidence-bound findings (ADR 0039 node [2]) from a completed
 * assessment. Body: { turns: { speaker, text }[] }. The transcript is rebuilt
 * server-side from the turns (never trusts a client-assembled transcript).
 * Returns: { findings: string } — markdown, the X-ray that withholds the read.
 *
 * Preview surface: unlinked, rate-limited, dogfood-only.
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { draftFindings, type Turn } from '../../../lib/claude/assessment'
import { rateLimitByIp } from '../../../lib/booking/rate-limit'
import { jsonResponse, errorResponse } from '../../../lib/api/helpers'

const RATE_LIMIT_PER_HOUR = 40
const MAX_TURNS = 60
const MAX_TURN_CHARS = 4000

function parseTurns(body: unknown): Turn[] | null {
  if (typeof body !== 'object' || body === null) return null
  const raw = (body as { turns?: unknown }).turns
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > MAX_TURNS) return null
  const turns: Turn[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) return null
    const speaker = (item as { speaker?: unknown }).speaker
    const text = (item as { text?: unknown }).text
    if (speaker !== 'owner' && speaker !== 'operator') return null
    if (typeof text !== 'string' || text.length === 0 || text.length > MAX_TURN_CHARS) return null
    turns.push({ speaker, text })
  }
  return turns
}

export const POST: APIRoute = async ({ request, clientAddress }: APIContext) => {
  const rate = await rateLimitByIp(
    env.BOOKING_CACHE,
    'assessment_findings',
    clientAddress,
    RATE_LIMIT_PER_HOUR
  )
  if (!rate.allowed) return errorResponse(429, 'rate_limited')

  if (!env.ANTHROPIC_API_KEY)
    return errorResponse(503, 'unavailable', 'Findings are temporarily unavailable.')

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  const turns = parseTurns(body)
  if (turns === null) return errorResponse(400, 'validation_failed', 'Invalid request.')

  try {
    const findings = await draftFindings(env.ANTHROPIC_API_KEY, turns)
    return jsonResponse(200, { findings })
  } catch {
    return errorResponse(502, 'unavailable', 'The findings could not be drafted. Please try again.')
  }
}
