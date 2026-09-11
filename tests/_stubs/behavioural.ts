/**
 * Shared seams for the behavioural test files (2026-09-11, review 2026-09-10,
 * Testing 3): a migrated miniflare D1, the org/entity rows every test needs,
 * the `cloudflare:workers` env binding, an in-memory KV and R2, and the
 * Astro APIContext shape the route handlers read (request, params, locals,
 * redirect). Nothing here knows about any one subject; each test file owns
 * its own seeding beyond the org and entity rows.
 */

import {
  createTestD1,
  discoverNumericMigrations,
  installWorkerdPolyfills,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')

export async function migratedDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

export async function seedOrg(db: D1Database, id: string): Promise<void> {
  await db
    .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(id, `Org ${id}`, id)
    .run()
}

export async function seedEntity(
  db: D1Database,
  args: { id: string; orgId: string; stage?: string; name?: string }
): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug, stage) VALUES (?, ?, ?, ?, ?)')
    .bind(args.id, args.orgId, args.name ?? `Entity ${args.id}`, args.id, args.stage ?? 'signal')
    .run()
}

export async function seedAssessment(
  db: D1Database,
  args: { id: string; orgId: string; entityId: string }
): Promise<void> {
  await db
    .prepare(
      "INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')"
    )
    .bind(args.id, args.orgId, args.entityId)
    .run()
}

/** A quote row in the given status; the assessment it references is seeded too. */
export async function seedQuote(
  db: D1Database,
  args: { id: string; orgId: string; entityId: string; status?: string }
): Promise<void> {
  await seedAssessment(db, {
    id: `assessment-${args.id}`,
    orgId: args.orgId,
    entityId: args.entityId,
  })
  await db
    .prepare(
      `INSERT INTO quotes (id, org_id, entity_id, assessment_id, line_items, total_hours, rate, total_price, status)
       VALUES (?, ?, ?, ?, '[]', 10, 175, 1750, ?)`
    )
    .bind(args.id, args.orgId, args.entityId, `assessment-${args.id}`, args.status ?? 'accepted')
    .run()
}

/** An engagement row (FK-complete: its quote and assessment are seeded too). */
export async function seedEngagement(
  db: D1Database,
  args: { id: string; orgId: string; entityId: string; status?: string }
): Promise<void> {
  await seedQuote(db, { id: `quote-${args.id}`, orgId: args.orgId, entityId: args.entityId })
  await db
    .prepare(
      `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, actual_hours) VALUES (?, ?, ?, ?, ?, 0)`
    )
    .bind(args.id, args.orgId, args.entityId, `quote-${args.id}`, args.status ?? 'active')
    .run()
}

/** Replace every binding on the `cloudflare:workers` env stub. */
export function bindEnv(bindings: Record<string, unknown>): void {
  const bindingsInPlace = testEnv as unknown as Record<string, unknown>
  for (const key of Object.keys(bindingsInPlace)) delete bindingsInPlace[key]
  Object.assign(testEnv, bindings)
}

export interface AdminSessionLike {
  userId: string
  orgId: string
  role: 'admin'
  email: string
  expiresAt: string
}

export function adminSession(orgId: string, userId = `admin-${orgId}`): AdminSessionLike {
  return {
    userId,
    orgId,
    role: 'admin',
    email: `${userId}@smd.services`,
    expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
  }
}

export function formRequest(url: string, fields: Record<string, string | string[]>): Request {
  const form = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (Array.isArray(value)) for (const v of value) form.append(key, v)
    else form.set(key, value)
  }
  return new Request(url, { method: 'POST', body: form })
}

export function jsonRequest(url: string, body: unknown, headers: Record<string, string> = {}) {
  return new Request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  })
}

/**
 * The slice of Astro's APIContext the admin routes read. `session` null
 * stands for a request the middleware let through with no admin identity;
 * the guard answers 401 before any handler code runs.
 */
export function routeContext(args: {
  request: Request
  params?: Record<string, string | undefined>
  session?: AdminSessionLike | null
  locals?: Record<string, unknown>
}) {
  return {
    request: args.request,
    params: args.params ?? {},
    locals: { session: args.session ?? null, ...(args.locals ?? {}) },
    redirect: (url: string, status = 302) =>
      new Response(null, { status, headers: { Location: url } }),
  }
}

/** Parse a JSON response body without depending on which lib types Response.json(). */
export async function readJson<T>(res: Response): Promise<T> {
  const parsed: unknown = JSON.parse(await res.text())
  return parsed as T
}

export function locationOf(res: Response): string {
  return res.headers.get('Location') ?? ''
}

export function locationQuery(res: Response): URLSearchParams {
  return new URL(locationOf(res), 'http://test.local').searchParams
}

export function memoryKv(): KVNamespace & { store: Map<string, string> } {
  const store = new Map<string, string>()
  return {
    store,
    get: (key: string) => Promise.resolve(store.get(key) ?? null),
    put: (key: string, value: string) => {
      store.set(key, value)
      return Promise.resolve()
    },
    delete: (key: string) => {
      store.delete(key)
      return Promise.resolve()
    },
  } as unknown as KVNamespace & { store: Map<string, string> }
}

export interface MemoryBucket {
  bucket: R2Bucket
  store: Map<string, Uint8Array>
}

export function memoryBucket(): MemoryBucket {
  const store = new Map<string, Uint8Array>()
  const bucket = {
    put(key: string, body: ArrayBuffer | Uint8Array | string) {
      const bytes =
        typeof body === 'string'
          ? new TextEncoder().encode(body)
          : body instanceof Uint8Array
            ? body
            : new Uint8Array(body)
      store.set(key, bytes)
      return Promise.resolve({ key })
    },
    get(key: string) {
      const hit = store.get(key)
      if (!hit) return Promise.resolve(null)
      return Promise.resolve({
        key,
        body: new Blob([new Uint8Array(hit)]).stream(),
        arrayBuffer: () =>
          Promise.resolve(hit.buffer.slice(hit.byteOffset, hit.byteOffset + hit.byteLength)),
        text: () => Promise.resolve(new TextDecoder().decode(hit)),
      })
    },
    list({ prefix }: { prefix: string }) {
      return Promise.resolve({
        objects: [...store.entries()]
          .filter(([key]) => key.startsWith(prefix))
          .map(([key, bytes]) => ({ key, size: bytes.byteLength, uploaded: new Date(0) })),
      })
    },
  } as unknown as R2Bucket
  return { bucket, store }
}

/**
 * The Clerk seam the portal routes read: `locals.auth()` and
 * `locals.currentUser()`. A verified primary email is what lets the bridge
 * JIT-create or auto-link the local users row.
 */
export function portalLocals(args: {
  clerkUserId: string | null
  email?: string
  name?: string
  clerkOrgId?: string | null
}) {
  const email = args.email ?? 'client@example.com'
  return {
    auth: () => ({ userId: args.clerkUserId, orgId: args.clerkOrgId ?? null, sessionId: null }),
    currentUser: () =>
      Promise.resolve(
        args.clerkUserId
          ? {
              primaryEmailAddress: { emailAddress: email, verification: { status: 'verified' } },
              emailAddresses: [{ emailAddress: email, verification: { status: 'verified' } }],
              firstName: args.name ?? 'Client',
              lastName: null,
              username: null,
            }
          : null
      ),
    cfContext: undefined,
    session: null,
  }
}

export async function seedPortalUser(
  db: D1Database,
  args: { id: string; orgId: string; clerkUserId: string; email?: string; entityId: string | null }
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
       VALUES (?, ?, ?, ?, 'client', ?, ?)`
    )
    .bind(
      args.id,
      args.orgId,
      args.email ?? `${args.id}@example.com`,
      `User ${args.id}`,
      args.entityId,
      args.clerkUserId
    )
    .run()
}

export function daysFrom(iso: string, days: number): string {
  const date = new Date(iso)
  date.setDate(date.getDate() + days)
  return date.toISOString()
}
