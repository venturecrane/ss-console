import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * Regression guard: the booking manage token must never reach the events table.
 *
 * THE DEFECT THIS PINS (found 2026-08-24). `/book/manage/<token>` carries the
 * token as a PATH SEGMENT, and that token IS the auth — `api/booking/manage/
 * [token].ts` says so outright ("The token itself IS the auth — no session
 * required"), and it returns guest_name/guest_email and grants cancel and
 * reschedule. `lib/booking/tokens.ts` states the raw token is "never written to
 * the DB or logged".
 *
 * It was. `Base.astro` rendered `<EventsTracker />` unconditionally — outside
 * the `!disableAnalytics` guard that its two siblings sit behind — so the
 * tracker fired on a page that had explicitly asked not to be tracked. It posted
 * `location.pathname`, and `scrubPath` stripped only `?` and `#`. The token
 * landed in `events.path` verbatim.
 *
 * Three independent things had to hold for the leak to happen, so this test
 * pins all three. Any one of them regressing re-opens it.
 */

const ROOT = resolve(__dirname, '..')
const read = (p: string) => readFileSync(resolve(ROOT, p), 'utf8')

describe('booking manage token never reaches the events pipeline', () => {
  it('the manage page still asks not to be tracked', () => {
    // If this ever stops being true the layout guard below protects nothing.
    const page = read('src/pages/book/manage/[token].astro')
    expect(page).toMatch(/\bdisableAnalytics\b/)
  })

  it('EventsTracker is gated on disableAnalytics, like Analytics and MetaPixel', () => {
    const base = read('src/layouts/Base.astro')
    // The exact shape matters: an ungated <EventsTracker /> is the original bug.
    expect(base).toMatch(/\{!disableAnalytics && <EventsTracker \/>\}/)
    expect(base).not.toMatch(/^\s*<EventsTracker \/>\s*$/m)
  })

  it('both scrubPath implementations redact opaque path segments', () => {
    // Server side is the load-bearing one — the client is untrusted, and the
    // events rate limit is keyed on a client-supplied session id.
    const server = read('src/pages/api/events.ts')
    expect(server).toMatch(/redactOpaqueSegments/)
    expect(server).toMatch(/OPAQUE_SEGMENT_PREFIXES/)

    const client = read('src/components/EventsTracker.astro')
    expect(client).toMatch(/redactOpaqueSegments/)
  })
})

/**
 * Behavioural half. The checks above assert wiring; these assert the redactor
 * actually redacts. The implementation is duplicated here rather than imported
 * because `events.ts` keeps it module-private — so this reproduces the
 * published contract, and the wiring assertions above are what tie that
 * contract to the real call site.
 */
const OPAQUE_SEGMENT_PREFIXES = ['/book/manage/'] as const
const OPAQUE_SEGMENT_MIN_LEN = 24
const OPAQUE_SEGMENT_RE = /^[A-Za-z0-9_-]+$/

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

describe('redactOpaqueSegments', () => {
  // A real-shaped manage token: 32 random bytes render as 43 url-safe-base64
  // chars. BUILT rather than pasted — a realistic literal here trips the repo's
  // own gitleaks guard, which is the guard working correctly. Only the shape
  // matters to this test.
  const TOKEN = 'x'.repeat(43)

  it('redacts the manage token but keeps the route shape', () => {
    expect(TOKEN.length).toBe(43)
    const out = redactOpaqueSegments(`/book/manage/${TOKEN}`)
    expect(out).toBe('/book/manage/:redacted')
    expect(out).not.toContain(TOKEN)
  })

  it('redacts a token-shaped segment on a route nobody has allowlisted yet', () => {
    // The generic guard is what makes this a class fix rather than a one-off.
    const out = redactOpaqueSegments(`/some/future/route/${TOKEN}`)
    expect(out).toBe('/some/future/route/:redacted')
  })

  it('leaves ordinary marketing paths untouched', () => {
    for (const path of ['/', '/operator', '/about', '/industries', '/patterns', '/contact']) {
      expect(redactOpaqueSegments(path)).toBe(path)
    }
    // Authored slugs are words, not 24+ chars of opaque alphabet.
    expect(redactOpaqueSegments('/packs/med-spa')).toBe('/packs/med-spa')
  })

  it('preserves any trailing segments after the redacted one', () => {
    expect(redactOpaqueSegments(`/book/manage/${TOKEN}/cancel`)).toBe(
      '/book/manage/:redacted/cancel'
    )
  })

  it('is a no-op on the bare prefix', () => {
    expect(redactOpaqueSegments('/book/manage/')).toBe('/book/manage/')
  })
})
