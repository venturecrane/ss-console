import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'

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
 * Rate limit: 100 events per session_id per minute (D1-backed fixed
 * window). Bursts above that are silently clamped — the endpoint returns
 * 204 regardless to keep the client script simple.
 *
 * Request shape:
 *   {
 *     session_id: string,        // client-generated UUID (optional; if
 *                                //   missing, server sets a cookie and
 *                                //   generates one)
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

const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
const SAFE_STRING_RE = /^[a-zA-Z0-9_\-.:/]+$/

interface IncomingEvent {
  event_name: string
  path?: string
  metadata?: Record<string, unknown>
}

export const POST: APIRoute = async ({ request }) => {
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const rawEvents = Array.isArray(body.events) ? body.events : null
  if (!rawEvents || rawEvents.length === 0) {
    return new Response(JSON.stringify({ error: 'events array required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Resolve session id: client-supplied (if valid UUID) or cookie or new.
  const cookieSid = parseCookie(request.headers.get('cookie'), COOKIE_NAME)
  const clientSid = typeof body.session_id === 'string' ? body.session_id : null
  const sessionId =
    (clientSid && UUID_RE.test(clientSid) && clientSid) ||
    (cookieSid && UUID_RE.test(cookieSid) && cookieSid) ||
    crypto.randomUUID()
  const needSetCookie = sessionId !== cookieSid

  // Rate limit per session.
  const allowed = await checkRateLimit(env.DB, sessionId)
  if (!allowed) {
    return buildResponse(204, needSetCookie ? sessionId : null, request)
  }

  // Cap per-batch size before validation to keep loops bounded.
  const events = rawEvents.slice(0, MAX_EVENTS_PER_BATCH)

  const userAgent = truncate(request.headers.get('user-agent'), MAX_UA_LEN)
  const referrer = truncate(request.headers.get('referer'), MAX_REFERRER_LEN)
  const country = readCfCountry(request)
  const now = Date.now()

  const rows: Array<{
    id: string
    event_name: string
    path: string | null
    metadata: string | null
  }> = []

  for (const raw of events) {
    const parsed = validateEvent(raw)
    if (!parsed) continue
    rows.push({
      id: crypto.randomUUID(),
      event_name: parsed.event_name,
      path: parsed.path ?? null,
      metadata: parsed.metadata ? JSON.stringify(parsed.metadata) : null,
    })
  }

  if (rows.length === 0) {
    return buildResponse(204, needSetCookie ? sessionId : null, request)
  }

  try {
    const stmts = rows.map((row) =>
      env.DB.prepare(
        `INSERT INTO events (id, session_id, event_name, path, ts, metadata, user_agent, referrer, country)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        row.id,
        sessionId,
        row.event_name,
        row.path,
        now,
        row.metadata,
        userAgent,
        referrer,
        country
      )
    )
    await env.DB.batch(stmts)
  } catch (err) {
    console.error('[api/events] D1 insert failed:', err)
    return new Response(JSON.stringify({ error: 'Failed to persist events' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  return buildResponse(204, needSetCookie ? sessionId : null, request)
}

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
 * Strip query strings and fragments. Defensive even though the client
 * already does this — an attacker bypassing the client could submit raw
 * URLs with tokens or PII in query params.
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
      return new URL(stripped).pathname
    } catch {
      return '/'
    }
  }
  return stripped.startsWith('/') ? stripped : `/${stripped}`
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
