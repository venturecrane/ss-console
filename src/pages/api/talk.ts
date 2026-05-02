import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { rateLimitByIp } from '../../lib/booking/rate-limit'
import {
  generateConversationReply,
  ConversationApiError,
  type ConversationTurn,
} from '../../lib/claude/conversation'

/**
 * POST /api/talk
 *
 * Voice-intake conversation endpoint. Receives a transcribed utterance
 * (browser-side STT via Web Speech API), sends it to Claude with the
 * conversation doctrine system prompt, returns the agent reply.
 *
 * V1 is single-turn: each request is independent. The `history` and
 * `conversation_id` fields are accepted now so the multi-turn V2 client
 * lands without an API change.
 *
 * Public endpoint, no auth. Rate-limited per IP.
 */

const MAX_TRANSCRIPT_CHARS = 5000
const MAX_HISTORY_TURNS = 40
const MAX_CONVERSATION_ID_LEN = 64
const RATE_LIMIT_PER_HOUR = 30

export const POST: APIRoute = async ({ request }) => {
  const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
  const rateLimitResult = await rateLimitByIp(
    env.BOOKING_CACHE,
    'talk',
    clientIp,
    RATE_LIMIT_PER_HOUR
  )
  if (!rateLimitResult.allowed) {
    return jsonResponse(429, { error: 'Too many requests, please slow down.' })
  }

  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  const transcriptRaw = typeof body.transcript === 'string' ? body.transcript.trim() : ''
  if (!transcriptRaw) {
    return jsonResponse(400, { error: 'transcript is required' })
  }
  if (transcriptRaw.length > MAX_TRANSCRIPT_CHARS) {
    return jsonResponse(400, {
      error: `transcript exceeds maximum length of ${MAX_TRANSCRIPT_CHARS} characters`,
    })
  }

  const history = parseHistory(body.history)
  if (history === null) {
    return jsonResponse(400, { error: 'history must be an array of {role, content} turns' })
  }

  const conversationId =
    typeof body.conversation_id === 'string' &&
    body.conversation_id.length > 0 &&
    body.conversation_id.length <= MAX_CONVERSATION_ID_LEN
      ? body.conversation_id
      : crypto.randomUUID()

  const apiKey = env.ANTHROPIC_API_KEY
  if (!apiKey) {
    console.error('[api/talk] ANTHROPIC_API_KEY not configured')
    return jsonResponse(503, { error: 'Service not configured' })
  }

  try {
    const reply = await generateConversationReply(apiKey, transcriptRaw, history)
    return jsonResponse(200, { ok: true, reply, conversation_id: conversationId })
  } catch (err) {
    if (err instanceof ConversationApiError) {
      console.error('[api/talk] Claude API error:', err.message, {
        status: err.statusCode,
        body: err.responseBody?.slice(0, 500),
      })
      return jsonResponse(500, { error: 'Conversation service failed' })
    }
    console.error('[api/talk] Unexpected error:', err)
    return jsonResponse(500, { error: 'Conversation service failed' })
  }
}

function parseHistory(value: unknown): ConversationTurn[] | null {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) return null
  if (value.length > MAX_HISTORY_TURNS) return null

  const turns: ConversationTurn[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') return null
    const role = (entry as Record<string, unknown>).role
    const content = (entry as Record<string, unknown>).content
    if (role !== 'user' && role !== 'assistant') return null
    if (typeof content !== 'string' || !content.trim()) return null
    if (content.length > MAX_TRANSCRIPT_CHARS) return null
    turns.push({ role, content })
  }
  return turns
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
