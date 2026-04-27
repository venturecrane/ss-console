import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

import { createMagicLink, verifyMagicLink } from '../src/lib/auth/magic-link'
import { POST } from '../src/pages/api/auth/magic-link'
import { ORG_ID } from '../src/lib/constants'

const migrationsDir = resolve(process.cwd(), 'migrations')

const PRIMARY_USER_ID = 'user-primary'
const SECONDARY_ORG_ID = 'org-secondary'
const SECONDARY_USER_ID = 'user-secondary'

describe('magic links', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(SECONDARY_ORG_ID, 'Secondary Org', 'secondary-org')
      .run()

    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role)
         VALUES (?, ?, ?, ?, 'client')`
      )
      .bind(PRIMARY_USER_ID, ORG_ID, 'client@example.com', 'Primary Client')
      .run()
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role)
         VALUES (?, ?, ?, ?, 'client')`
      )
      .bind(SECONDARY_USER_ID, SECONDARY_ORG_ID, 'client@example.com', 'Secondary Client')
      .run()

    Object.assign(testEnv, {
      DB: db,
      APP_BASE_URL: 'https://smd.services',
      PORTAL_BASE_URL: 'https://portal.smd.services',
    })
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('stores org_id and user_id, consumes once, and returns the bound subject', async () => {
    const token = await createMagicLink(db, {
      orgId: ORG_ID,
      userId: PRIMARY_USER_ID,
      email: 'Client@Example.com',
    })

    const stored = await db
      .prepare(
        `SELECT org_id, user_id, email, used_at
         FROM magic_links
         WHERE token = ?`
      )
      .bind(token)
      .first<{
        org_id: string
        user_id: string
        email: string
        used_at: string | null
      }>()

    expect(stored).not.toBeNull()
    expect(stored!.org_id).toBe(ORG_ID)
    expect(stored!.user_id).toBe(PRIMARY_USER_ID)
    expect(stored!.email).toBe('client@example.com')
    expect(stored!.used_at).toBeNull()

    const consumed = await verifyMagicLink(db, token)
    expect(consumed).toEqual({
      id: expect.any(String),
      orgId: ORG_ID,
      userId: PRIMARY_USER_ID,
      email: 'client@example.com',
    })

    const secondAttempt = await verifyMagicLink(db, token)
    expect(secondAttempt).toBeNull()
  })

  it('allows only one winner under concurrent verification', async () => {
    const token = await createMagicLink(db, {
      orgId: ORG_ID,
      userId: PRIMARY_USER_ID,
      email: 'client@example.com',
    })

    const [a, b] = await Promise.all([verifyMagicLink(db, token), verifyMagicLink(db, token)])
    const winners = [a, b].filter(Boolean)

    expect(winners).toHaveLength(1)
    expect(winners[0]).toMatchObject({
      orgId: ORG_ID,
      userId: PRIMARY_USER_ID,
      email: 'client@example.com',
    })
  })

  it('rejects expired tokens', async () => {
    const token = await createMagicLink(db, {
      orgId: ORG_ID,
      userId: PRIMARY_USER_ID,
      email: 'client@example.com',
    })

    await db
      .prepare(`UPDATE magic_links SET expires_at = datetime('now', '-1 minute') WHERE token = ?`)
      .bind(token)
      .run()

    const consumed = await verifyMagicLink(db, token)
    expect(consumed).toBeNull()
  })

  it('magic-link POST scopes the lookup to the current app org', async () => {
    const request = new Request('https://smd.services/api/auth/magic-link', {
      method: 'POST',
      body: new URLSearchParams({ email: 'client@example.com' }),
    })

    const redirect = (location: string, status: number) =>
      new Response(null, {
        status,
        headers: { Location: location },
      })

    const response = await POST({
      request,
      redirect,
    } as unknown as Parameters<typeof POST>[0])

    expect(response.status).toBe(302)
    expect(response.headers.get('Location')).toBe('/auth/portal-login?status=sent')

    const rows = await db
      .prepare(`SELECT org_id, user_id FROM magic_links ORDER BY created_at ASC`)
      .all<{ org_id: string; user_id: string }>()

    expect(rows.results).toHaveLength(1)
    expect(rows.results[0]).toEqual({
      org_id: ORG_ID,
      user_id: PRIMARY_USER_ID,
    })
  })
})
