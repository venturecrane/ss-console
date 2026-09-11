import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../lib/api/helpers'
import { rateLimitByIp } from '../../lib/booking/rate-limit'

/**
 * POST /api/events
 *
 * Batched event ingestion for the marketing surface (apex smd.services).
 * Persists page views and CTA clicks to D1 for the admin aggregate
 * dashboard and future public aggregate-patterns page (parent epic #483,
 * child #488).
 *
 * Privacy posture:
 *   - No PII. Metadata is whitelisted-shape only; form field values are
 *     never captured. The client-side tracker (EventsTracker.astro) is
 *     the authoritative gate on payload shape; this endpoint additionally
 *     validates and strips anything outside that contract.
 *   - No third-party analytics, no fingerprinting. User-agent is stored
 *     for aggregate browser-class queries only; IP is never written —
 *     geo is derived from request.cf.country.
 *   - Paths are scrubbed of query strings before insert, so auth tokens,
 *     booking IDs, etc. that leak into URLs do not land in the events
 *     table.
 *
 * Rate limit, two layers, both silently clamping to 204 so the client script
 * stays simple:
 *   1. Per IP (KV-backed, `rateLimitByIp`): EVENTS_IP_LIMIT_PER_HOUR batches
 *      per hour per `cf-connecting-ip`. This is the bound the caller cannot
 *      move, because the session id below is caller-supplied. Without it a
 *      client rotating session ids had an unbounded D1 write (2026-08-23
 *      review C3, carried three reviews, closed 2026-09-10).
 *   2. Per session (D1-backed fixed window): 100 events per session_id per
 *      minute, counted from the events table itself.
 *
 * Session identity: the `ss_sid` cookie is authoritative when present and
 * well-formed. `body.session_id` only seeds the id when there is no cookie
 * yet (the tracker's first batch races the cookie write). A body id that
 * disagrees with a valid cookie is ignored, so a client cannot spread one
 * browser's events across many sessions by lying in the body. The cookie
 * is NOT HttpOnly on purpose: EventsTracker.astro reads and writes it from
 * document.cookie (readCookie / writeCookie), so the client and server share
 * one id.
 *
 * Request shape:
 *   {
 *     session_id: string,        // client-generated UUID (optional; used only
 *                                //   when no ss_sid cookie is present; if both
 *                                //   are missing the server generates one and
 *                                //   sets the cookie)
 *     events: Array<{
 *       event_name: string,      // e.g. "page_view", "cta_click"
 *       path?: string,           // scrubbed server-side
 *       metadata?: object        // small JSON object (stringified server-side)
 *     }>
 *   }
 *
 * Response: 204 No Content on success. 400 on malformed input. 500 on
 * D1 failure — errors are logged, client-side tracker treats all non-2xx
 * as drop-on-floor.
 */

const COOKIE_NAME = 'ss_sid'
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30 // 30 days
const MAX_EVENTS_PER_BATCH = 50
const MAX_EVENT_NAME_LEN = 64
const MAX_PATH_LEN = 512
const MAX_METADATA_BYTES = 2048
const MAX_UA_LEN = 512
const MAX_REFERRER_LEN = 512
const RATE_LIMIT_PER_MINUTE = 100
/**
 * Per-IP ceiling on POST batches per hour. The tracker flushes at most one
 * batch every FLUSH_DEBOUNCE_MS (2s) plus one on pagehide, so a human on a
 * single IP cannot approach this; an office NAT with dozens of browsers
 * still fits. Above it the endpoint clamps to 204 without writing.
 */
const EVENTS_IP_LIMIT_PER_HOUR = 600

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
const SAFE_STRING_RE = /^[a-zA-Z0-9_\-.:/]+$/

interface IncomingEvent {
  event_name: string
  path?: string
  metadata?: Record<string, unknown>
}

function resolveSessionId(
  request: Request,
  body: Record<string, unknown>
): {
  sessionId: string
  needSetCookie: boolean
} {
  const cookieSid = parseCookie(request.headers.get('cookie'), COOKIE_NAME)
  const clientSid = typeof body.session_id === 'string' ? body.session_id : null
  // Cookie first: it is the identity the browser cannot rewrite per request.
  // The body id is a seed for the cookieless first batch only.
  const sessionId =
    (cookieSid && UUID_RE.test(cookieSid) && cookieSid) ||
    (clientSid && UUID_RE.test(clientSid) && clientSid) ||
    crypto.randomUUID()
  return { sessionId, needSetCookie: sessionId !== cookieSid }
}

interface EventContext {
  sessionId: string
  userAgent: string | null
  referrer: string | null
  country: string | null
  now: number
}

async function persistEventRows(
  rows: Array<{ id: string; event_name: string; path: string | null; metadata: string | null }>,
  ctx: EventContext
): Promise<void> {
  const stmts = rows.map((row) =>
    env.DB.prepare(
      `INSERT INTO events (id, session_id, event_name, path, ts, metadata, user_agent, referrer, country)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).bind(
      row.id,
      ctx.sessionId,
      row.event_name,
      row.path,
      ctx.now,
      row.metadata,
      ctx.userAgent,
      ctx.referrer,
      ctx.country
    )
  )
  await env.DB.batch(stmts)
}

async function handlePost({ request }: APIContext): Promise<Response> {
  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return errorResponse(400, 'Invalid JSON')
  }

  const rawEvents = Array.isArray(body.events) ? body.events : null
  if (!rawEvents || rawEvents.length === 0) {
    return errorResponse(400, 'events array required')
  }

  const { sessionId, needSetCookie } = resolveSessionId(request, body)
  const cookieSidOrNull = needSetCookie ? sessionId : null

  // Layer 1: the per-IP bound the caller cannot move (see header).
  const clientIp = request.headers.get('cf-connecting-ip') ?? undefined
  const ipLimit = await rateLimitByIp(
    env.BOOKING_CACHE,
    'events',
    clientIp,
    EVENTS_IP_LIMIT_PER_HOUR
  )
  if (!ipLimit.allowed) {
    return buildResponse(204, cookieSidOrNull, request)
  }

  // Layer 2: the per-session minute bucket.
  const allowed = await checkRateLimit(env.DB, sessionId)
  if (!allowed) {
    return buildResponse(204, cookieSidOrNull, request)
  }

  const events = rawEvents.slice(0, MAX_EVENTS_PER_BATCH)
  const eventCtx: EventContext = {
    sessionId,
    userAgent: truncate(request.headers.get('user-agent'), MAX_UA_LEN),
    referrer: truncate(request.headers.get('referer'), MAX_REFERRER_LEN),
    country: readCfCountry(request),
    now: Date.now(),
  }

  const rows = events
    .map((raw: unknown) => validateEvent(raw))
    .filter((p): p is IncomingEvent => p !== null)
    .map((parsed) => ({
      id: crypto.randomUUID(),
      event_name: parsed.event_name,
      path: parsed.path ?? null,
      metadata: parsed.metadata ? JSON.stringify(parsed.metadata) : null,
    }))

  if (rows.length === 0) {
    return buildResponse(204, cookieSidOrNull, request)
  }

  try {
    await persistEventRows(rows, eventCtx)
  } catch (err) {
    console.error('[api/events] D1 insert failed:', err)
    return errorResponse(500, 'Failed to persist events')
  }

  return buildResponse(204, cookieSidOrNull, request)
}

export const POST: APIRoute = (ctx) => handlePost(ctx)

function validateEvent(raw: unknown): IncomingEvent | null {
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>

  const eventName = typeof obj.event_name === 'string' ? obj.event_name.trim() : ''
  if (!eventName || eventName.length > MAX_EVENT_NAME_LEN || !SAFE_STRING_RE.test(eventName)) {
    return null
  }

  let path: string | undefined
  if (typeof obj.path === 'string' && obj.path.length > 0) {
    path = scrubPath(obj.path).slice(0, MAX_PATH_LEN)
  }

  let metadata: Record<string, unknown> | undefined
  if (obj.metadata && typeof obj.metadata === 'object' && !Array.isArray(obj.metadata)) {
    const filtered = filterMetadata(obj.metadata as Record<string, unknown>)
    if (filtered) metadata = filtered
  }

  return { event_name: eventName, path, metadata }
}

/**
 * Route prefixes whose NEXT path segment is a credential rather than a page
 * identity. `/book/manage/<token>` is the live case: that token IS the auth
 * (`api/booking/manage/[token].ts` — "no session required"), and
 * `lib/booking/tokens.ts` states the raw token is never written to the DB or
 * logged. Storing it in `events.path` broke that invariant.
 */
const OPAQUE_SEGMENT_PREFIXES = ['/book/manage/'] as const

/**
 * Length at or above which a single path segment drawn only from the
 * URL-safe-base64 alphabet is treated as opaque and redacted. The manage token
 * is 32 random bytes rendered as 43 chars. No authored slug in this codebase is
 * near this long, so the false-positive cost is nil, and the benefit is that a
 * FUTURE token-bearing route is covered without anyone remembering to add it to
 * the list above.
 */
const OPAQUE_SEGMENT_MIN_LEN = 24
const OPAQUE_SEGMENT_RE = /^[A-Za-z0-9_-]+$/

/**
 * Replace credential-shaped path segments with a placeholder, keeping the route
 * shape so the analytics stay useful.
 */
function redactOpaqueSegments(path: string): string {
  for (const prefix of OPAQUE_SEGMENT_PREFIXES) {
    if (path.startsWith(prefix) && path.length > prefix.length) {
      const rest = path.slice(prefix.length)
      const slash = rest.indexOf('/')
      return `${prefix}:redacted${slash === -1 ? '' : rest.slice(slash)}`
    }
  }
  return path
    .split('/')
    .map((seg) =>
      seg.length >= OPAQUE_SEGMENT_MIN_LEN && OPAQUE_SEGMENT_RE.test(seg) ? ':redacted' : seg
    )
    .join('/')
}

/**
 * Strip query strings and fragments, then redact credential-shaped path
 * segments. Defensive even though the client already does both — an attacker
 * bypassing the client could submit raw URLs with tokens or PII, and the events
 * rate limit is keyed on a client-supplied session id, so "the client would not
 * do that" is not a bound worth relying on.
 */
function scrubPath(input: string): string {
  const qIdx = input.indexOf('?')
  const hIdx = input.indexOf('#')
  let end = input.length
  if (qIdx !== -1) end = Math.min(end, qIdx)
  if (hIdx !== -1) end = Math.min(end, hIdx)
  const stripped = input.slice(0, end)
  // Force leading slash; reject anything that looks like a full URL.
  if (stripped.startsWith('http://') || stripped.startsWith('https://')) {
    try {
      return redactOpaqueSegments(new URL(stripped).pathname)
    } catch {
      return '/'
    }
  }
  return redactOpaqueSegments(stripped.startsWith('/') ? stripped : `/${stripped}`)
}

/**
 * Only pass through simple scalar values (string/number/boolean). Drop
 * nested objects, arrays, and anything oversized. This is the defense
 * against a client accidentally (or deliberately) passing form values,
 * full payloads, etc.
 */
function filterMetadata(input: Record<string, unknown>): Record<string, unknown> | null {
  const out: Record<string, unknown> = {}
  let count = 0
  for (const [key, value] of Object.entries(input)) {
    if (count >= 16) break
    if (typeof key !== 'string' || key.length > 64) continue
    if (typeof value === 'string') {
      if (value.length > 256) continue
      out[key] = value
    } else if (typeof value === 'number' && Number.isFinite(value)) {
      out[key] = value
    } else if (typeof value === 'boolean') {
      out[key] = value
    } else {
      continue
    }
    count++
  }
  if (count === 0) return null
  const serialized = JSON.stringify(out)
  if (serialized.length > MAX_METADATA_BYTES) return null
  return out
}

async function checkRateLimit(db: D1Database, sessionId: string): Promise<boolean> {
  // Fixed-window minute bucket in D1. The events table itself is the
  // source of truth — counting rows for this session in the last minute
  // avoids a separate rate-limit table.
  const windowStart = Date.now() - 60_000
  try {
    const row = await db
      .prepare('SELECT COUNT(*) as count FROM events WHERE session_id = ? AND ts >= ?')
      .bind(sessionId, windowStart)
      .first<{ count: number }>()
    return (row?.count ?? 0) < RATE_LIMIT_PER_MINUTE
  } catch (err) {
    // If the count query fails, allow the write. Better to accept a
    // small number of extra events than to drop legitimate traffic
    // because of a transient D1 hiccup.
    console.error('[api/events] rate-limit count failed:', err)
    return true
  }
}

function readCfCountry(request: Request): string | null {
  const cf = (request as unknown as { cf?: { country?: string } }).cf
  const country = cf?.country
  if (typeof country !== 'string') return null
  if (country.length !== 2) return null
  return country
}

function parseCookie(header: string | null, name: string): string | null {
  if (!header) return null
  const parts = header.split(';')
  for (const part of parts) {
    const trimmed = part.trim()
    const eqIdx = trimmed.indexOf('=')
    if (eqIdx === -1) continue
    if (trimmed.slice(0, eqIdx) === name) {
      return trimmed.slice(eqIdx + 1)
    }
  }
  return null
}

function buildSessionCookie(sessionId: string, secure: boolean): string {
  const attrs = [
    `${COOKIE_NAME}=${sessionId}`,
    'Path=/',
    `Max-Age=${COOKIE_MAX_AGE_SECONDS}`,
    'SameSite=Lax',
  ]
  if (secure) attrs.push('Secure')
  return attrs.join('; ')
}

function buildResponse(status: number, setSessionId: string | null, request: Request): Response {
  const headers = new Headers()
  if (setSessionId) {
    const url = new URL(request.url)
    const secure = url.protocol === 'https:'
    headers.append('Set-Cookie', buildSessionCookie(setSessionId, secure))
  }
  return new Response(null, { status, headers })
}

function truncate(value: string | null, max: number): string | null {
  if (!value) return null
  return value.length > max ? value.slice(0, max) : value
}
