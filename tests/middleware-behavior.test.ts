/**
 * Behavioral tests for src/middleware.ts.
 *
 * The sibling tests/middleware.test.ts enforces architectural invariants by
 * matching the middleware SOURCE TEXT. This file exercises the same code at
 * RUNTIME: it invokes the exported `onRequest` against a fake APIContext, a
 * real migrated D1 (via @venturecrane/crane-test-harness), and an in-memory
 * KV, and asserts on the Response the middleware actually produces.
 *
 * How the composed pipeline is driven under test
 * -----------------------------------------------
 * Production wires `onRequest = sequence(clerkMiddleware(), ssMiddleware)`.
 * Clerk's middleware is the obstacle for a runtime test — it expects a live
 * Clerk session + signing keys and would clobber the `locals.auth()` we want
 * to control. We mock `@clerk/astro/server` so `clerkMiddleware()` is a
 * pass-through `(ctx, next) => next()`. That leaves `ssMiddleware` — the
 * SS-owned subdomain rewrites, legacy redirects, admin shim, and auth
 * enforcement — running exactly as in production, while the test owns
 * `context.locals.auth()` (the Clerk session state Clerk would otherwise set).
 *
 * `context.rewrite` / `context.redirect` are supplied by Astro in production;
 * here the fake context provides equivalents that return a sentinel Response so
 * the test can distinguish a rewrite (200, X-Rewrite-To header) from a redirect
 * (3xx, Location header) from a fall-through to `next()` (200, X-Next header).
 *
 * Branch coverage map (src/middleware.ts):
 *   - handleSubdomainRewrite: portal. + admin. host → path prepend; exempt paths
 *   - redirectToAdminHost: strict `hostname === 'smd.services'` apex guard
 *   - enforceAdminAuth: no Clerk userId → redirect / 401; Clerk userId but
 *     no admin row (or role != admin) → /portal redirect / 403
 *   - enforcePortalAuth: Clerk userId OR legacy magic-link client session →
 *     allow; neither → redirect / 401
 *   - resolveLegacyPortalSession: validates the session_token cookie against D1
 *
 * Deferred (documented, not faked):
 *   - The apex admin-cookie clearing described in CLAUDE.md is performed by the
 *     admin page/layout chrome on the next visit, NOT by src/middleware.ts —
 *     there is no cookie-clearing branch in the middleware to exercise here.
 *   - clerkMiddleware's own session parsing is owned by Clerk and mocked out;
 *     this file tests the SS middleware's response to a given auth() state, not
 *     Clerk's derivation of it.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database, KVNamespace } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import { ORG_ID } from '../src/lib/constants'
import { SESSION_COOKIE_NAME } from '../src/lib/auth/session'

// Replace Clerk's middleware with a transparent pass-through so ssMiddleware
// runs against the auth() state the test plants on locals. The real
// clerkMiddleware would require live Clerk config and overwrite locals.auth().
vi.mock('@clerk/astro/server', () => ({
  clerkMiddleware: () => (_ctx: unknown, next: () => unknown) => next(),
}))

// Import AFTER the mock is declared so the middleware module picks up the
// stubbed clerkMiddleware.
import { onRequest } from '../src/middleware'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')

// ---------------------------------------------------------------------------
// In-memory KV backing SESSIONS (admin-session-shim cache + magic-link cache).
// ---------------------------------------------------------------------------
function createMemoryKv(): KVNamespace {
  const store = new Map<string, string>()
  return {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    put: vi.fn(async (key: string, value: string) => {
      store.set(key, value)
    }),
    delete: vi.fn(async (key: string) => {
      store.delete(key)
    }),
    list: vi.fn(),
    getWithMetadata: vi.fn(),
  } as unknown as KVNamespace
}

// ---------------------------------------------------------------------------
// Fake APIContext. `next`, `rewrite`, and `redirect` each return a sentinel
// Response so the test can classify which branch the middleware took.
// ---------------------------------------------------------------------------
const NEXT_MARKER = 'X-Next'
const REWRITE_MARKER = 'X-Rewrite-To'

type AuthState = { userId: string | null; orgId?: string | null }

function buildContext(opts: { url: string; auth?: AuthState; cookie?: string }): {
  context: unknown
  next: ReturnType<typeof vi.fn>
} {
  const headers = new Headers()
  if (opts.cookie) headers.set('cookie', opts.cookie)
  const request = new Request(opts.url, { headers })
  const url = new URL(opts.url)
  const auth: AuthState = opts.auth ?? { userId: null, orgId: null }

  const next = vi.fn(
    async () => new Response('next', { status: 200, headers: { [NEXT_MARKER]: '1' } })
  )

  const context = {
    request,
    url,
    locals: {
      auth: () => auth,
      // session is set to null by the middleware itself before resolution.
      session: null as unknown,
    },
    // Astro's context.rewrite — returns a transparent 200 carrying the target
    // so assertions can confirm the destination path without a real pipeline.
    rewrite: (req: Request) =>
      new Response('rewrite', {
        status: 200,
        headers: { [REWRITE_MARKER]: new URL(req.url).pathname },
      }),
    // Astro's context.redirect.
    redirect: (location: string, status = 302) =>
      new Response(null, { status, headers: { Location: location } }),
  }

  return { context, next }
}

async function invoke(opts: {
  url: string
  auth?: AuthState
  cookie?: string
}): Promise<{ res: Response; next: ReturnType<typeof vi.fn> }> {
  const { context, next } = buildContext(opts)
  // onRequest is Astro's MiddlewareHandler; the fake context/next satisfy the
  // shape the SS middleware actually reads. A single structural cast keeps the
  // call honest without per-arg gymnastics.
  const handler = onRequest as (ctx: unknown, n: unknown) => Promise<Response>
  const res = await handler(context, next)
  return { res, next }
}

// ---------------------------------------------------------------------------
// Seeding helpers.
// ---------------------------------------------------------------------------
const ADMIN_CLERK_ID = 'user_admin_clerk'
const NONADMIN_CLERK_ID = 'user_client_clerk'
const CLIENT_TOKEN = 'client-session-token-001'

async function seedBaseOrg(db: D1Database): Promise<void> {
  await db
    .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(ORG_ID, 'SMD Services', 'smd-services')
    .run()
}

async function seedAdminUser(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id)
       VALUES (?, ?, ?, ?, 'admin', ?)`
    )
    .bind('u-admin', ORG_ID, 'admin@smd.services', 'Admin', ADMIN_CLERK_ID)
    .run()
}

async function seedNonAdminUser(db: D1Database): Promise<void> {
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, clerk_user_id)
       VALUES (?, ?, ?, ?, 'client', ?)`
    )
    .bind('u-client', ORG_ID, 'client@example.com', 'Client', NONADMIN_CLERK_ID)
    .run()
}

async function seedClientSession(db: D1Database): Promise<void> {
  // users row for the FK target, then a non-expired client session.
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role)
       VALUES (?, ?, ?, ?, 'client')`
    )
    .bind('u-portal', ORG_ID, 'portal@example.com', 'Portal Client')
    .run()
  const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString()
  await db
    .prepare(
      `INSERT INTO sessions (id, token, user_id, org_id, role, email, expires_at)
       VALUES (?, ?, ?, ?, 'client', ?, ?)`
    )
    .bind('sess-1', CLIENT_TOKEN, 'u-portal', ORG_ID, 'portal@example.com', expiresAt)
    .run()
}

// ---------------------------------------------------------------------------
// Suite.
// ---------------------------------------------------------------------------
describe('middleware runtime: behavior', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedBaseOrg(db)
    Object.assign(testEnv, { DB: db, SESSIONS: createMemoryKv() })
    // SENTRY_DSN unset → withSentryRequestHandler is a transparent pass-through.
    delete (testEnv as unknown as Record<string, unknown>).SENTRY_DSN
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
    vi.clearAllMocks()
  })

  // ---- Subdomain rewrite -------------------------------------------------
  describe('subdomain rewrite', () => {
    it('rewrites a bare path on portal.smd.services to /portal<path>', async () => {
      const { res } = await invoke({ url: 'https://portal.smd.services/dashboard' })
      expect(res.headers.get(REWRITE_MARKER)).toBe('/portal/dashboard')
    })

    it('rewrites "/" on portal.smd.services to /portal (no trailing slash)', async () => {
      const { res } = await invoke({ url: 'https://portal.smd.services/' })
      expect(res.headers.get(REWRITE_MARKER)).toBe('/portal')
    })

    it('rewrites a bare path on admin.smd.services to /admin<path>', async () => {
      const { res } = await invoke({
        url: 'https://admin.smd.services/entities',
        auth: { userId: ADMIN_CLERK_ID },
      })
      expect(res.headers.get(REWRITE_MARKER)).toBe('/admin/entities')
    })

    it('does NOT rewrite a path already under /portal on the portal subdomain', async () => {
      // Already-prefixed paths fall through to auth enforcement, not rewrite.
      // With no auth, the portal page path redirects to sign-in (not a rewrite).
      const { res } = await invoke({ url: 'https://portal.smd.services/portal/engagement' })
      expect(res.headers.get(REWRITE_MARKER)).toBeNull()
      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toBe('/auth/sign-in')
    })

    it('does NOT rewrite /auth paths on the admin subdomain (exempt)', async () => {
      const { res, next } = await invoke({ url: 'https://admin.smd.services/auth/sign-in' })
      expect(res.headers.get(REWRITE_MARKER)).toBeNull()
      // /auth is neither admin nor portal protected → falls through to next().
      expect(next).toHaveBeenCalledOnce()
    })
  })

  // ---- Apex admin-host redirect (strict hostname guard) ------------------
  describe('apex → admin-host redirect', () => {
    it('301s smd.services/admin/* to admin.smd.services', async () => {
      const { res } = await invoke({ url: 'https://smd.services/admin/entities' })
      expect(res.status).toBe(301)
      expect(new URL(res.headers.get('Location')!).hostname).toBe('admin.smd.services')
    })

    it('does NOT redirect admin.smd.services/admin/* back to itself (no loop)', async () => {
      // The strict `hostname === 'smd.services'` guard means the admin
      // subdomain skips the apex redirect entirely; the request proceeds to
      // auth enforcement instead of 301-looping.
      const { res } = await invoke({
        url: 'https://admin.smd.services/admin/entities',
        auth: { userId: ADMIN_CLERK_ID },
      })
      expect(res.status).not.toBe(301)
    })
  })

  // ---- Extracted legacy-redirect table (src/lib/routing/legacy-redirects) --
  // Behavioral coverage of the redirect rules moved out of middleware.ts (code
  // review 2026-07-02 §1.3). Drives the real onRequest so the extraction is
  // proven behavior-preserving, not just structurally present.
  describe('legacy redirects (rule table)', () => {
    it('301s the /ai-employee product rename to /operator (before subdomain rewrite)', async () => {
      const { res } = await invoke({ url: 'https://smd.services/ai-employee/pricing' })
      expect(res.status).toBe(301)
      expect(res.headers.get('Location')).toBe('/operator/pricing')
    })

    it('301s a portal-relative /products/ai-employee path to /products/operator', async () => {
      const { res } = await invoke({ url: 'https://smd.services/products/ai-employee' })
      expect(res.status).toBe(301)
      expect(res.headers.get('Location')).toBe('/products/operator')
    })

    it('301s a legacy auth path to the unified sign-in, preserving the query', async () => {
      const { res } = await invoke({ url: 'https://smd.services/auth/login?status=signed_out' })
      expect(res.status).toBe(301)
      const loc = new URL(res.headers.get('Location')!)
      expect(loc.pathname).toBe('/auth/sign-in')
      expect(loc.searchParams.get('status')).toBe('signed_out')
    })

    it('301s a retired marketing route (/scan) to home', async () => {
      const { res } = await invoke({ url: 'https://smd.services/scan' })
      expect(res.status).toBe(301)
      expect(res.headers.get('Location')).toBe('/')
    })

    it('301s /why to /operator#compare', async () => {
      const { res } = await invoke({ url: 'https://smd.services/why' })
      expect(res.status).toBe(301)
      expect(res.headers.get('Location')).toBe('/operator#compare')
    })

    it('301s bare /get-started to home but leaves /get-started?booked=1 alone', async () => {
      const retired = await invoke({ url: 'https://smd.services/get-started' })
      expect(retired.res.status).toBe(301)
      expect(retired.res.headers.get('Location')).toBe('/')

      const booked = await invoke({ url: 'https://smd.services/get-started?booked=1' })
      expect(booked.res.status).not.toBe(301)
    })
  })

  // ---- Admin auth enforcement -------------------------------------------
  describe('admin auth enforcement', () => {
    it('redirects an unauthenticated admin PAGE request to /auth/sign-in', async () => {
      const { res } = await invoke({ url: 'https://admin.smd.services/admin/entities' })
      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toBe('/auth/sign-in')
    })

    it('returns 401 JSON for an unauthenticated admin API request', async () => {
      const { res } = await invoke({ url: 'https://admin.smd.services/api/admin/entities' })
      expect(res.status).toBe(401)
      expect(await res.json()).toEqual({ error: 'unauthorized', message: 'Unauthorized.' })
    })

    it('gates /api/admin/fleet/health like every other admin route (carve-out ripped 2026-07-24)', async () => {
      // The machine-bearer carve-out died with the health-monitor rip —
      // deterministic work-liveness alerting lives in ss-fleet-alerts, and no
      // admin path bypasses the Clerk gate anymore.
      const { res } = await invoke({
        url: 'https://admin.smd.services/api/admin/fleet/health',
      })
      expect(res.status).toBe(401)
    })

    it('gates sibling /api/admin/fleet/* paths too', async () => {
      const { res } = await invoke({ url: 'https://admin.smd.services/api/admin/fleet/other' })
      expect(res.status).toBe(401)
    })

    it('redirects a Clerk-authenticated NON-admin to /portal on an admin page', async () => {
      await seedNonAdminUser(db)
      const { res } = await invoke({
        url: 'https://admin.smd.services/admin/entities',
        auth: { userId: NONADMIN_CLERK_ID },
      })
      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toBe('/portal')
    })

    it('returns 403 JSON for a Clerk-authenticated non-admin on an admin API route', async () => {
      await seedNonAdminUser(db)
      const { res } = await invoke({
        url: 'https://admin.smd.services/api/admin/entities',
        auth: { userId: NONADMIN_CLERK_ID },
      })
      expect(res.status).toBe(403)
      expect(await res.json()).toEqual({ error: 'forbidden', message: 'Forbidden.' })
    })

    it('allows a Clerk-authenticated admin (role=admin in D1) through to next()', async () => {
      await seedAdminUser(db)
      const { res, next } = await invoke({
        url: 'https://admin.smd.services/admin/entities',
        auth: { userId: ADMIN_CLERK_ID },
      })
      expect(next).toHaveBeenCalledOnce()
      expect(res.headers.get(NEXT_MARKER)).toBe('1')
    })
  })

  // ---- Portal auth enforcement ------------------------------------------
  describe('portal auth enforcement', () => {
    it('redirects an unauthenticated portal PAGE request to /auth/sign-in', async () => {
      const { res } = await invoke({ url: 'https://portal.smd.services/portal/engagement' })
      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toBe('/auth/sign-in')
    })

    it('returns 401 JSON for an unauthenticated portal API request', async () => {
      const { res } = await invoke({ url: 'https://portal.smd.services/api/portal/quotes' })
      expect(res.status).toBe(401)
      expect(await res.json()).toEqual({ error: 'unauthorized', message: 'Unauthorized.' })
    })

    it('allows a Clerk-authenticated user through (primary portal path)', async () => {
      const { res, next } = await invoke({
        url: 'https://portal.smd.services/portal/engagement',
        auth: { userId: 'user_any_clerk' },
      })
      expect(next).toHaveBeenCalledOnce()
      expect(res.headers.get(NEXT_MARKER)).toBe('1')
    })

    it('allows a legacy magic-link client session (cookie) as a Clerk fallback', async () => {
      await seedClientSession(db)
      const { res, next } = await invoke({
        url: 'https://portal.smd.services/portal/engagement',
        cookie: `${SESSION_COOKIE_NAME}=${CLIENT_TOKEN}`,
      })
      expect(next).toHaveBeenCalledOnce()
      expect(res.headers.get(NEXT_MARKER)).toBe('1')
    })

    it('rejects an unknown/invalid session_token cookie with no Clerk session', async () => {
      const { res } = await invoke({
        url: 'https://portal.smd.services/portal/engagement',
        cookie: `${SESSION_COOKIE_NAME}=not-a-real-token`,
      })
      expect(res.status).toBe(302)
      expect(res.headers.get('Location')).toBe('/auth/sign-in')
    })
  })

  // ---- Marketing/public fall-through ------------------------------------
  describe('public paths', () => {
    it('lets a marketing path on the apex fall through to next()', async () => {
      const { res, next } = await invoke({ url: 'https://smd.services/operator' })
      expect(next).toHaveBeenCalledOnce()
      expect(res.headers.get(NEXT_MARKER)).toBe('1')
    })
  })
})
