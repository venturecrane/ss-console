/**
 * Email-as-identity normalization (ss#2315, #2280 hardening item 12).
 *
 * `email` is the join key at several identity sites and was normalized a
 * different way at each — three of them with no trim, one case-sensitive on
 * BOTH sides. Neither `users.email` nor `contacts.email` carries
 * `COLLATE NOCASE` (there is no `COLLATE` anywhere in `migrations/`), so
 * SQLite equality is case-sensitive and the JS side is the only defence.
 *
 * These tests pin the canonical form at the sites a caller can reach, plus a
 * source-level guard so the next case-sensitive `email = ?` cannot land
 * silently. Each was run against the unfixed tree first; the failures are in
 * the PR body.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve, extname, relative } from 'path'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'

import { normalizeEmail } from '../src/lib/identity/email'
import { processIntakeSubmission } from '../src/lib/booking/intake-core'
import { ensureLocalUser } from '../src/lib/auth/clerk-bridge'
import {
  dedupePendingAgainstMembers,
  type ClerkOrgMember,
  type ClerkOrgPendingInvite,
} from '../src/lib/portal/clerk-org-members'
import { ORG_ID } from '../src/lib/constants'

const migrationsDir = resolve(process.cwd(), 'migrations')

describe('normalizeEmail', () => {
  it('trims and lowercases', () => {
    expect(normalizeEmail('  Owner@Example.COM \n')).toBe('owner@example.com')
  })

  it('is idempotent', () => {
    const once = normalizeEmail(' A@B.com ')
    expect(normalizeEmail(once)).toBe(once)
  })
})

// ---------------------------------------------------------------------------
// The live defect: intake dedupe was case-sensitive on both sides
// ---------------------------------------------------------------------------

describe('processIntakeSubmission — contact dedupe is case-insensitive', () => {
  let db: D1Database
  const orgId = 'org-email-identity'

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(orgId, 'Org EI', 'org-ei')
      .run()
  })

  it('does not create a second contact when the same person books with different casing', async () => {
    const first = await processIntakeSubmission(db, orgId, {
      name: 'Dana Owner',
      email: 'Owner@Example.com',
      businessName: 'Dana Plumbing',
    })
    expect(first.contactCreated).toBe(true)

    const second = await processIntakeSubmission(db, orgId, {
      name: 'Dana Owner',
      email: '  owner@example.COM ',
      businessName: 'Dana Plumbing',
    })

    // The observable: a second `contacts` row for one human. The returned
    // ids alone would not catch it — a fresh row also returns an id.
    const rows = await db
      .prepare('SELECT id, email FROM contacts WHERE org_id = ?')
      .bind(orgId)
      .all<{ id: string; email: string }>()

    expect(rows.results).toHaveLength(1)
    expect(second.contactCreated).toBe(false)
    expect(second.contactId).toBe(first.contactId)
  })
})

// ---------------------------------------------------------------------------
// clerk-bridge: lower() on both sides already, but no trim
// ---------------------------------------------------------------------------

describe('ensureLocalUser — auto-link tolerates surrounding whitespace', () => {
  let db: D1Database
  const PRE_CLERK_USER_ID = 'user-pre-clerk-ei'
  const PRE_CLERK_ENTITY_ID = 'entity-pre-clerk-ei'

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(PRE_CLERK_ENTITY_ID, ORG_ID, 'Pre Clerk EI', 'pre-clerk-ei')
      .run()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
         VALUES (?, ?, ?, ?, 'client', ?, NULL)`
      )
      .bind(PRE_CLERK_USER_ID, ORG_ID, 'Seeded@Example.com', 'Seeded Client', PRE_CLERK_ENTITY_ID)
      .run()
  })

  it('links the seeded row instead of stranding the person on a new one', async () => {
    const result = await ensureLocalUser(db, 'user_clerk_ei', {
      email: ' seeded@example.com ',
      name: 'Seeded Client',
    })

    expect(result?.id).toBe(PRE_CLERK_USER_ID)
    expect(result?.entity_id).toBe(PRE_CLERK_ENTITY_ID)

    // No second, entity-less row was minted alongside the seeded one. Scoped
    // to the address: migrations/0005_seed_admin_user.sql seeds an unrelated
    // admin into this org.
    const rows = await db
      .prepare('SELECT id FROM users WHERE org_id = ? AND lower(email) = ?')
      .bind(ORG_ID, 'seeded@example.com')
      .all<{ id: string }>()
    expect(rows.results).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// Pure-function join sites
// ---------------------------------------------------------------------------

describe('dedupePendingAgainstMembers', () => {
  it('drops a pending invite whose address differs only by case or whitespace', () => {
    const pending: ClerkOrgPendingInvite[] = [
      {
        kind: 'pending_invite',
        email: ' Joined@Example.com ',
        name: '',
        clerkUserId: null,
        role: 'member',
        joinedAt: null,
        invitedAt: null,
        expiresAt: null,
      },
    ]
    const members: ClerkOrgMember[] = [
      {
        kind: 'member',
        email: 'joined@example.com',
        name: 'Joined Person',
        clerkUserId: 'user_1',
        role: 'member',
        joinedAt: null,
        invitedAt: null,
        expiresAt: null,
      },
    ]

    expect(dedupePendingAgainstMembers(pending, members)).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// Permanent falsifier: no new case-sensitive identity lookup
// ---------------------------------------------------------------------------

/**
 * A WHERE/AND predicate comparing the `email` column with a bind parameter.
 * Deliberately does not match `SET email = ?` (an update writes, it does not
 * join) or `resolved_by_email = ?`.
 */
const CASE_SENSITIVE_EMAIL_PREDICATE = /(?:\bWHERE\b|\bAND\b)\s+email\s*=\s*\?/gi

function collectSourceFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir)) {
    const fullPath = `${dir}/${entry}`
    if (statSync(fullPath).isDirectory()) {
      files.push(...collectSourceFiles(fullPath))
      continue
    }
    const ext = extname(entry)
    if (['.astro', '.ts', '.tsx'].includes(ext) && !/\.test\.tsx?$/.test(entry)) {
      files.push(fullPath)
    }
  }
  return files
}

describe('email identity guard', () => {
  it('no shipped source compares the email column case-sensitively', () => {
    const offenders: string[] = []
    for (const file of collectSourceFiles(resolve('src'))) {
      const source = readFileSync(file, 'utf-8')
      for (const match of source.matchAll(CASE_SENSITIVE_EMAIL_PREDICATE)) {
        const line = source.slice(0, match.index).split('\n').length
        offenders.push(`${relative(process.cwd(), file)}:${line} — ${match[0].trim()}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
