/**
 * Per-IP rate limiting backed by `BOOKING_CACHE` KV.
 *
 * Used by `/api/booking/reserve` to cap booking attempts at 10/hour per IP.
 * NOT used by `/api/booking/slots` — public availability is uninteresting
 * to scrape and rate-limiting it punishes legitimate users behind shared
 * NAT (offices, CGNAT mobile carriers).
 *
 * Algorithm: simple fixed-window counter. Each request increments a KV key
 * scoped to the IP and a window-id (e.g., `rl:reserve:1.2.3.4:469872`).
 * The window-id is `Math.floor(now / windowSeconds)` so all requests in the
 * same window share a key. KV TTL is set to the window length so old
 * windows expire automatically.
 *
 * Trade-offs:
 *   - Fixed window has the classic burst-at-window-boundary issue (a user
 *     could send 2x the limit by hitting the boundary). Acceptable for
 *     booking which is low-volume; sliding window or token bucket is
 *     overkill.
 *   - KV is eventually consistent. Two near-simultaneous reads from
 *     different colos may both see the old count. Acceptable: at worst
 *     one extra request slips through.
 */

const WINDOW_SECONDS = 60 * 60 // 1 hour
const DEFAULT_LIMIT = 10

export interface RateLimitResult {
  allowed: boolean
  /** Current count after the increment. */
  count: number
  /** The configured limit for this bucket. */
  limit: number
}

/**
 * Check + increment the rate limit bucket for the given key (typically
 * `<endpoint>:<ip>`). Returns whether the request should be allowed.
 *
 * If `kv` is undefined (dev mode without KV binding), allows the request.
 */
export async function checkAndIncrementRateLimit(
  kv: KVNamespace | undefined,
  bucketKey: string,
  limit: number = DEFAULT_LIMIT,
  windowSeconds: number = WINDOW_SECONDS
): Promise<RateLimitResult> {
  if (!kv) {
    // Dev mode — allow
    return { allowed: true, count: 0, limit }
  }

  const windowId = Math.floor(Date.now() / 1000 / windowSeconds)
  const key = `rl:${bucketKey}:${windowId}`

  const currentRaw = await kv.get(key)
  const current = currentRaw ? parseInt(currentRaw, 10) : 0

  if (current >= limit) {
    return { allowed: false, count: current, limit }
  }

  const next = current + 1
  // KV expiration must be >=60s — windowSeconds defaults to 3600 so this is fine
  await kv.put(key, String(next), { expirationTtl: windowSeconds })

  return { allowed: true, count: next, limit }
}

/**
 * Convenience helper: rate-limit by client IP for a specific endpoint.
 * Used by `/api/booking/reserve`.
 */
export async function rateLimitByIp(
  kv: KVNamespace | undefined,
  endpoint: string,
  ip: string | undefined,
  limit?: number
): Promise<RateLimitResult> {
  // Treat missing IP as a single shared bucket — paranoid but safe
  const bucketKey = `${endpoint}:${ip ?? 'unknown'}`
  return checkAndIncrementRateLimit(kv, bucketKey, limit)
}
