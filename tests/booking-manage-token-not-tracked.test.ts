/**
 * Regression guard: the booking manage token must never reach the events table.
 *
 * THE DEFECT (found 2026-08-24). `/book/manage/<token>` carries the token as a
 * PATH SEGMENT, and that token IS the auth: it returns guest_name/guest_email
 * and grants cancel and reschedule. `lib/booking/tokens.ts` states the raw
 * token is "never written to the DB or logged". It was: the tracker posted
 * `location.pathname`, and the events endpoint stripped only `?` and `#`, so
 * the token landed in `events.path` verbatim.
 *
 * Three independent things had to hold for the leak to happen, so all three
 * are guarded: the manage page opts out of tracking, the layout honours the
 * opt-out for EventsTracker, and both redactors strip opaque segments. The
 * server is the load-bearing redactor (the client is untrusted), so it is
 * also run: paths are posted through the real POST /api/events against a
 * migrated D1 and what was stored is read back.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { bindEnv, memoryKv, migratedDb } from './_stubs/behavioural'
import { POST } from '../src/pages/api/events'

const SID = '11111111-1111-4111-8111-111111111111'

// A real-shaped manage token: 32 random bytes render as 43 url-safe-base64
// chars. BUILT rather than pasted: a realistic literal here trips the repo's
// own gitleaks guard. Only the shape matters to this test.
const TOKEN = 'x'.repeat(43)

let db: D1Database

async function storedPath(path: string): Promise<string> {
  await db.prepare('DELETE FROM events').run()
  const request = new Request('https://smd.services/api/events', {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie: `ss_sid=${SID}` },
    body: JSON.stringify({ events: [{ event_name: 'page_view', path }] }),
  })
  const res = await POST({ request } as unknown as Parameters<typeof POST>[0])
  expect(res.status).toBe(204)
  const row = await db.prepare('SELECT path FROM events').first<{ path: string }>()
  expect(row).not.toBeNull()
  return row?.path ?? ''
}

describe('POST /api/events never stores an opaque path segment', () => {
  beforeEach(async () => {
    db = await migratedDb()
    bindEnv({ DB: db, BOOKING_CACHE: memoryKv() })
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('redacts the manage token but keeps the route shape', async () => {
    const out = await storedPath(`/book/manage/${TOKEN}`)
    expect(out).toBe('/book/manage/:redacted')
    expect(out).not.toContain(TOKEN)
  })

  it('redacts it from a full URL with a query and a fragment too', async () => {
    const out = await storedPath(`https://smd.services/book/manage/${TOKEN}?x=1#y`)
    expect(out).toBe('/book/manage/:redacted')
  })

  it('redacts a token-shaped segment on a route nobody has allowlisted yet', async () => {
    expect(await storedPath(`/some/future/route/${TOKEN}`)).toBe('/some/future/route/:redacted')
  })

  it('preserves any trailing segments after the redacted one', async () => {
    expect(await storedPath(`/book/manage/${TOKEN}/cancel`)).toBe('/book/manage/:redacted/cancel')
  })

  it('leaves ordinary marketing paths untouched', async () => {
    for (const path of ['/', '/operator', '/about', '/packs/med-spa']) {
      expect(await storedPath(path)).toBe(path)
    }
  })
})

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8')

describe('booking manage token never reaches the events pipeline (wiring)', () => {
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
    const server = read('src/pages/api/events.ts')
    expect(server).toMatch(/redactOpaqueSegments/)
    expect(server).toMatch(/OPAQUE_SEGMENT_PREFIXES/)

    const client = read('src/components/EventsTracker.astro')
    expect(client).toMatch(/redactOpaqueSegments/)
  })
})
