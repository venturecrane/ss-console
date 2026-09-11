/**
 * POST /api/assessment/llm  — OpenAI-compatible `/chat/completions` for the
 * ElevenLabs voice agent's custom LLM (ADR 0039 node [1], voice channel).
 *
 * ElevenLabs calls this with the conversation in OpenAI format; we inject our
 * interviewer system prompt and stream back Anthropic's reply translated to
 * OpenAI `chat.completion.chunk` SSE. The brain is our eval-proven operator.
 *
 * Auth (fail-closed): ELEVENLABS_LLM_SECRET MUST be set. A matching
 * `Authorization: Bearer <secret>` is required (set the same secret on the
 * agent's custom-LLM config). If the secret is unset the route returns 503 —
 * it never serves open. Serving open would let anyone proxy Anthropic on our
 * budget (this route is public: not under /api/admin or /api/portal).
 *
 * NOTE (2026-06-30): the secret is currently deferred/unset in prod, so this
 * route returns 503 to the real ElevenLabs caller until the secret is
 * provisioned in /ss prod + on the ElevenLabs agent config. That is the safe
 * state; the voice channel stays dark rather than open.
 *
 * No per-IP rate limit here on purpose: ElevenLabs calls server-to-server from
 * a single egress IP, so a per-IP bucket would be shared across the entire
 * voice channel and throttle every customer at once. The bearer secret is the
 * gate; content-size caps below are the topology-safe defense-in-depth.
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import {
  streamInterviewerCompletion,
  type OpenAIChatMessage,
} from '../../../lib/claude/assessment-llm'
import { jsonResponse } from '../../../lib/api/helpers'
import { constantTimeEqual } from '../../../lib/auth/constant-time'

function json(status: number, body: unknown): Response {
  return jsonResponse(status, body)
}

/** Normalize OpenAI content, which may arrive as a string OR an array of parts (ElevenLabs/OpenAI multimodal). */
function contentToString(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        typeof part === 'object' &&
        part !== null &&
        typeof (part as { text?: unknown }).text === 'string'
          ? (part as { text: string }).text
          : ''
      )
      .join('')
  }
  return ''
}

/** Storage-abuse caps for the (public) proxy. Bounds a single request's size. */
const MAX_MESSAGE_CHARS = 8_000
const MAX_TOTAL_CHARS = 64_000

/**
 * Lenient parse: coerce each message rather than rejecting the whole batch on a
 * single odd one (the agent may send array-content or empty placeholder turns).
 * Returns null if there is no usable conversation at all, or if the batch
 * exceeds the size caps (rejected whole — never silently truncated).
 */
function parseMessages(body: unknown): OpenAIChatMessage[] | null {
  if (typeof body !== 'object' || body === null) return null
  const raw = (body as { messages?: unknown }).messages
  if (!Array.isArray(raw) || raw.length === 0 || raw.length > 200) return null
  const messages: OpenAIChatMessage[] = []
  let totalChars = 0
  for (const item of raw) {
    if (typeof item !== 'object' || item === null) continue
    const role = (item as { role?: unknown }).role
    if (typeof role !== 'string') continue
    const content = contentToString((item as { content?: unknown }).content)
    if (content.length === 0) continue
    if (content.length > MAX_MESSAGE_CHARS) return null
    totalChars += content.length
    if (totalChars > MAX_TOTAL_CHARS) return null
    messages.push({ role, content })
  }
  return messages.length > 0 ? messages : null
}

export const POST: APIRoute = async ({ request }: APIContext) => {
  const expected = env.ELEVENLABS_LLM_SECRET
  // Fail closed: no secret configured ⇒ refuse, never serve open. An open proxy
  // would let anyone spend our Anthropic budget.
  if (!expected) return json(503, { error: 'unavailable' })
  const auth = request.headers.get('authorization') ?? ''
  // Constant-time like every other shared-secret check in the tree; this is
  // the one public endpoint whose compromise spends the Anthropic budget.
  if (!constantTimeEqual(auth, `Bearer ${expected}`)) return json(401, { error: 'unauthorized' })

  if (!env.ANTHROPIC_API_KEY) return json(503, { error: 'unavailable' })

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return json(400, { error: 'invalid json' })
  }

  const messages = parseMessages(body)
  if (messages === null) return json(400, { error: 'invalid messages' })

  // Diagnostic: metadata only (counts + roles), never content. Aids wrangler tail
  // while the voice channel is being stabilized.
  console.error(
    `[assessment-llm] in ${messages.length} msgs [${messages.map((m) => m.role).join(',')}]`
  )

  try {
    return await streamInterviewerCompletion(env.ANTHROPIC_API_KEY, messages, Date.now())
  } catch {
    // Always answer the custom-LLM caller in SSE shape, never JSON.
    return new Response('data: {"error":"upstream error"}\n\ndata: [DONE]\n\n', {
      status: 200,
      headers: { 'content-type': 'text/event-stream; charset=utf-8' },
    })
  }
}
