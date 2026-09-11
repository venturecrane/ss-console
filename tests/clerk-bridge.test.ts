import { beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import {
  ensureLocalUser,
  resolveClerkPortalContext,
  type PortalUserRow,
} from '../src/lib/auth/clerk-bridge'
import { clerkProfile, verifiedPrimaryEmail } from '../src/lib/auth/clerk-profile'
import { ORG_ID } from '../src/lib/constants'

/** Narrow ensureLocalUser's nullable return for the cases that expect a row. */
function expectRow(row: PortalUserRow | null): PortalUserRow {
  expect(row).not.toBeNull()
  return row!
}

const migrationsDir = resolve(process.cwd(), 'migrations')

const PRE_CLERK_USER_ID = 'user-pre-clerk'
const PRE_CLERK_ENTITY_ID = 'entity-pre-clerk'
const PRE_CLERK_EMAIL = 'preclerk@example.com'

describe('ensureLocalUser', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()

    // Seed an entity so we can bind the pre-Clerk user row to it.
    // Only the columns required by the schema's NOT NULL constraints
    // are populated; stage defaults to 'signal'.
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug)
         VALUES (?, ?, ?, ?)`
      )
      .bind(PRE_CLERK_ENTITY_ID, ORG_ID, 'Pre-Clerk Entity', 'pre-clerk-entity')
      .run()

    // Pre-Clerk users row: created from a legacy magic-link invite or
    // admin-side seed, never authenticated through Clerk yet.
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
         VALUES (?, ?, ?, ?, 'client', ?, NULL)`
      )
      .bind(PRE_CLERK_USER_ID, ORG_ID, PRE_CLERK_EMAIL, 'Pre Clerk Client', PRE_CLERK_ENTITY_ID)
      .run()
  })

  it('returns the existing row when clerk_user_id already matches', async () => {
    // Bind the row first, then call ensureLocalUser with the same Clerk id.
    const boundClerkId = 'user_already_bound'
    await db
      .prepare('UPDATE users SET clerk_user_id = ? WHERE id = ?')
      .bind(boundClerkId, PRE_CLERK_USER_ID)
      .run()

    const result = expectRow(
      await ensureLocalUser(db, boundClerkId, {
        email: PRE_CLERK_EMAIL,
        name: 'Pre Clerk Client',
      })
    )

    expect(result.id).toBe(PRE_CLERK_USER_ID)
    expect(result.clerk_user_id).toBe(boundClerkId)
    expect(result.entity_id).toBe(PRE_CLERK_ENTITY_ID)
    expect(result.role).toBe('client')
  })

  it('auto-links regardless of email CASE (IdP casing must never strand a seeded seat)', async () => {
    // The bug this guards: users.email carries no COLLATE NOCASE, so an
    // exact-match lookup missed when the seeded casing differed from what
    // Clerk returned (Microsoft/Google OAuth echo the directory's casing).
    // The JIT INSERT then succeeded — UNIQUE(org_id,email) is case-sensitive
    // too — stranding the person on a second, entity-less row: signed in but
    // "not connected to a customer", locked out of a waiting seat.
    const newClerkId = 'user_uppercase_idp'
    const upper = PRE_CLERK_EMAIL.toUpperCase()
    expect(upper).not.toBe(PRE_CLERK_EMAIL)

    const result = expectRow(
      await ensureLocalUser(db, newClerkId, { email: upper, name: 'Pre Clerk Client' })
    )

    // Linked the SEEDED row (with its entity binding) — not a new orphan.
    expect(result.id).toBe(PRE_CLERK_USER_ID)
    expect(result.entity_id).toBe(PRE_CLERK_ENTITY_ID)

    const rows = await db
      .prepare('SELECT id FROM users WHERE lower(email) = lower(?)')
      .bind(PRE_CLERK_EMAIL)
      .all<{ id: string }>()
    expect(rows.results).toHaveLength(1)
  })

  it('auto-links a pre-Clerk row by email when clerk_user_id is NULL', async () => {
    // The bug this guards: ensureLocalUser used to only match on
    // clerk_user_id, then INSERT a new row. UNIQUE(org_id, email) made
    // that INSERT crash and left the pre-Clerk row orphaned.
    const newClerkId = 'user_first_sign_in'

    const result = expectRow(
      await ensureLocalUser(db, newClerkId, {
        email: PRE_CLERK_EMAIL,
        name: 'Pre Clerk Client',
      })
    )

    expect(result.id).toBe(PRE_CLERK_USER_ID)
    expect(result.clerk_user_id).toBe(newClerkId)
    expect(result.entity_id).toBe(PRE_CLERK_ENTITY_ID)

    // Verify it actually persisted (not just the returned object).
    const persisted = await db
      .prepare('SELECT clerk_user_id, entity_id FROM users WHERE id = ?')
      .bind(PRE_CLERK_USER_ID)
      .first<{ clerk_user_id: string | null; entity_id: string | null }>()
    expect(persisted?.clerk_user_id).toBe(newClerkId)
    expect(persisted?.entity_id).toBe(PRE_CLERK_ENTITY_ID)

    // No duplicate row was inserted.
    const count = await db
      .prepare('SELECT COUNT(*) AS n FROM users WHERE org_id = ? AND email = ?')
      .bind(ORG_ID, PRE_CLERK_EMAIL)
      .first<{ n: number }>()
    expect(count?.n).toBe(1)
  })

  it('does NOT overwrite an existing clerk_user_id when emails match', async () => {
    // Defense-in-depth: if a different Clerk identity signs up with the
    // same email after the row is already bound to a Clerk id, we must
    // not silently rebind. The email-match query filters by
    // `clerk_user_id IS NULL`, so the linked row is excluded and a new
    // row is JIT-created instead.
    const originalClerkId = 'user_original'
    await db
      .prepare('UPDATE users SET clerk_user_id = ? WHERE id = ?')
      .bind(originalClerkId, PRE_CLERK_USER_ID)
      .run()

    const impostorClerkId = 'user_impostor'
    // Use a different email — UNIQUE(org_id, email) blocks a duplicate
    // of the bound row. The impostor sign-in carries a verified Clerk
    // email; this asserts that even on email collision against a bound
    // row, the existing binding is preserved.
    const result = expectRow(
      await ensureLocalUser(db, impostorClerkId, {
        email: 'impostor@example.com',
        name: 'Impostor',
      })
    )

    expect(result.clerk_user_id).toBe(impostorClerkId)
    expect(result.id).not.toBe(PRE_CLERK_USER_ID)

    const original = await db
      .prepare('SELECT clerk_user_id FROM users WHERE id = ?')
      .bind(PRE_CLERK_USER_ID)
      .first<{ clerk_user_id: string | null }>()
    expect(original?.clerk_user_id).toBe(originalClerkId)
  })

  it('JIT-creates a fresh client row when no email or clerk_user_id matches', async () => {
    const newClerkId = 'user_brand_new'
    const result = expectRow(
      await ensureLocalUser(db, newClerkId, {
        email: 'brand-new@example.com',
        name: 'Brand New',
      })
    )

    expect(result.clerk_user_id).toBe(newClerkId)
    expect(result.email).toBe('brand-new@example.com')
    expect(result.role).toBe('client')
    expect(result.entity_id).toBeNull()
    expect(result.id).not.toBe(PRE_CLERK_USER_ID)
  })

  // ── No trusted email (2026-08-14 review finding) ─────────────────────────
  // clerk-profile.ts yields email:null when Clerk has no VERIFIED PRIMARY
  // address. The bridge must then refuse email-based linking and JIT
  // creation: an unverified address auto-linking to a seeded row would hand
  // one person another person's client seat.

  it('does NOT auto-link a pre-Clerk row when the profile carries no trusted email', async () => {
    const result = await ensureLocalUser(db, 'user_no_trusted_email', {
      email: null,
      name: 'Untrusted',
    })

    expect(result).toBeNull()

    // The seeded row stayed unlinked — the seat is still waiting for its
    // real owner.
    const seeded = await db
      .prepare('SELECT clerk_user_id FROM users WHERE id = ?')
      .bind(PRE_CLERK_USER_ID)
      .first<{ clerk_user_id: string | null }>()
    expect(seeded?.clerk_user_id).toBeNull()
  })

  it('does NOT JIT-create a row when the profile carries no trusted email', async () => {
    const before = await db.prepare('SELECT COUNT(*) AS n FROM users').first<{ n: number }>()

    const result = await ensureLocalUser(db, 'user_no_trusted_email_2', {
      email: null,
      name: '',
    })
    expect(result).toBeNull()

    const after = await db.prepare('SELECT COUNT(*) AS n FROM users').first<{ n: number }>()
    expect(after?.n).toBe(before?.n)
  })

  it('still resolves an already-bound row when the profile carries no trusted email', async () => {
    // A user whose row is already keyed by clerk_user_id needs no email at
    // all — losing primary-email verification later must not lock them out.
    const boundClerkId = 'user_bound_then_unverified'
    await db
      .prepare('UPDATE users SET clerk_user_id = ? WHERE id = ?')
      .bind(boundClerkId, PRE_CLERK_USER_ID)
      .run()

    const result = expectRow(
      await ensureLocalUser(db, boundClerkId, { email: null, name: 'Whoever' })
    )
    expect(result.id).toBe(PRE_CLERK_USER_ID)
    expect(result.entity_id).toBe(PRE_CLERK_ENTITY_ID)
  })
})

describe('clerkProfile / verifiedPrimaryEmail', () => {
  it('trusts a verified primary address', () => {
    const user = {
      primaryEmailAddress: {
        emailAddress: 'owner@example.com',
        verification: { status: 'verified' },
      },
      firstName: 'Pat',
      lastName: 'Owner',
    }
    expect(verifiedPrimaryEmail(user)).toBe('owner@example.com')
    expect(clerkProfile(user)).toEqual({ email: 'owner@example.com', name: 'Pat Owner' })
  })

  it('rejects an UNVERIFIED primary address', () => {
    const user = {
      primaryEmailAddress: {
        emailAddress: 'attacker-added@example.com',
        verification: { status: 'unverified' },
      },
    }
    expect(verifiedPrimaryEmail(user)).toBeNull()
    expect(clerkProfile(user).email).toBeNull()
  })

  it('rejects a primary address with no verification record', () => {
    const user = { primaryEmailAddress: { emailAddress: 'x@example.com' } }
    expect(verifiedPrimaryEmail(user)).toBeNull()
  })

  it('yields null when there is no primary address at all — never falls back to another address', () => {
    // The pre-fix behavior fell back to emailAddresses[0]; the profile shape
    // deliberately no longer even accepts that list.
    expect(verifiedPrimaryEmail({ primaryEmailAddress: null })).toBeNull()
    expect(clerkProfile({ primaryEmailAddress: null }).email).toBeNull()
  })

  it('derives name from username, never from an untrusted email', () => {
    const user = {
      primaryEmailAddress: {
        emailAddress: 'x@example.com',
        verification: { status: 'unverified' },
      },
      username: 'pat-o',
    }
    expect(clerkProfile(user)).toEqual({ email: null, name: 'pat-o' })
  })
})

describe('resolveClerkPortalContext', () => {
  let db: D1Database
  const BOUND_CLERK_ID = 'user_bound_clerk'

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug)
         VALUES (?, ?, ?, ?)`
      )
      .bind(PRE_CLERK_ENTITY_ID, ORG_ID, 'Pre-Clerk Entity', 'pre-clerk-entity')
      .run()
    // Bind a users row directly to an entity (no Clerk Organization
    // involved). This is the common single-user-portal case.
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
         VALUES (?, ?, ?, ?, 'client', ?, ?)`
      )
      .bind(
        PRE_CLERK_USER_ID,
        ORG_ID,
        PRE_CLERK_EMAIL,
        'Pre Clerk Client',
        PRE_CLERK_ENTITY_ID,
        BOUND_CLERK_ID
      )
      .run()
  })

  it('resolves the entity via users.entity_id when no Clerk org is active', async () => {
    // The regression this guards: getPortalClient used to skip the
    // entity_id path and only try auth.orgId. A user with no active
    // Clerk org but a direct entity binding would land at no_subscription.
    const ctx = await resolveClerkPortalContext(
      db,
      { userId: BOUND_CLERK_ID, orgId: null },
      { email: PRE_CLERK_EMAIL, name: 'Pre Clerk Client' }
    )

    expect(ctx).not.toBeNull()
    expect(ctx!.user.id).toBe(PRE_CLERK_USER_ID)
    expect(ctx!.client).not.toBeNull()
    expect(ctx!.client!.id).toBe(PRE_CLERK_ENTITY_ID)
  })

  it('returns client:null when neither entity_id nor a matching clerk_org_id exists', async () => {
    // A JIT-created user with no binding lands here. Caller renders
    // the "no portal access yet" state.
    const ctx = await resolveClerkPortalContext(
      db,
      { userId: 'user_unbound', orgId: null },
      { email: 'unbound@example.com', name: 'Unbound' }
    )

    expect(ctx).not.toBeNull()
    expect(ctx!.user.entity_id).toBeNull()
    expect(ctx!.client).toBeNull()
  })

  it('returns null when no Clerk session is present', async () => {
    const ctx = await resolveClerkPortalContext(
      db,
      { userId: null, orgId: null },
      { email: '', name: '' }
    )
    expect(ctx).toBeNull()
  })

  // ---- login stamping (portal accountability slice) ----

  async function loginEvents(): Promise<
    { entity_id: string | null; clerk_session_id: string | null }[]
  > {
    const res = await db
      .prepare('SELECT entity_id, clerk_session_id FROM portal_login_events ORDER BY created_at')
      .all<{ entity_id: string | null; clerk_session_id: string | null }>()
    return res.results ?? []
  }

  it('stamps last_login_at + history on first resolve with a session id, no-op on second', async () => {
    const auth = { userId: BOUND_CLERK_ID, orgId: null, sessionId: 'sess_stamp_1' }
    const profile = { email: PRE_CLERK_EMAIL, name: 'Pre Clerk Client' }

    await resolveClerkPortalContext(db, auth, profile)
    let events = await loginEvents()
    expect(events).toHaveLength(1)
    // Entity resolution ran BEFORE stamping: the event carries the entity.
    expect(events[0].entity_id).toBe(PRE_CLERK_ENTITY_ID)

    const stamped = await db
      .prepare('SELECT last_login_at FROM users WHERE id = ?')
      .bind(PRE_CLERK_USER_ID)
      .first<{ last_login_at: string | null }>()
    expect(stamped?.last_login_at).not.toBeNull()

    // Same session again (the users row now carries the skip-cache value).
    await resolveClerkPortalContext(db, auth, profile)
    events = await loginEvents()
    expect(events).toHaveLength(1)
  })

  it('stamps a JIT-created user on the same request (entity null)', async () => {
    const ctx = await resolveClerkPortalContext(
      db,
      { userId: 'user_jit_login', orgId: null, sessionId: 'sess_jit' },
      { email: 'jit@example.com', name: 'JIT' }
    )
    expect(ctx!.client).toBeNull()
    const events = await loginEvents()
    expect(events).toHaveLength(1)
    expect(events[0].entity_id).toBeNull()
    expect(events[0].clerk_session_id).toBe('sess_jit')
  })

  it('does not stamp when no session id is available (legacy callers unaffected)', async () => {
    await resolveClerkPortalContext(
      db,
      { userId: BOUND_CLERK_ID, orgId: null },
      { email: PRE_CLERK_EMAIL, name: 'Pre Clerk Client' }
    )
    expect(await loginEvents()).toHaveLength(0)
  })

  it('returns null (no access, no rows, no login stamp) for an unbound user with no trusted email', async () => {
    const ctx = await resolveClerkPortalContext(
      db,
      { userId: 'user_untrusted_new', orgId: null, sessionId: 'sess_untrusted' },
      { email: null, name: 'Untrusted' }
    )
    expect(ctx).toBeNull()
    expect(await loginEvents()).toHaveLength(0)
  })
})
