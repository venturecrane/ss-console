/**
 * Couples Telegram deep-link tokens and POST /api/internal/telegram-bind
 * (ADR 0091 s5, plan R4), against a real D1 schema.
 *
 * The token is opaque: it carries no claims, so there is nothing to trust in
 * it. The falsifiers below are the ways a token could be abused if the
 * console trusted anything but its own row: replayed, used late, forged,
 * altered, redeemed by another seat, or used to bind one partner's Telegram
 * to the other.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import {
  createCouplesInstance,
  getCouplesInstance,
  listPeople,
  type CouplesPersonRow,
} from '../src/lib/db/couples-onboarding'
import { issueLinkToken, redeemLinkToken } from '../src/lib/db/couples-link-tokens'
import {
  TOKEN_RE,
  TOKEN_TTL_MS,
  generateLinkToken,
  telegramDeepLink,
} from '../src/lib/hosted-agent/couples/link-token'
import { POST as bindPost } from '../src/pages/api/internal/telegram-bind'
import { seedMachineCredential } from './helpers/machine-credential'

const SLUG = 'couple-test'
const OTHER_SLUG = 'other-seat'
const ORG_ID = '01JQFK0000SMDSERVICES000'
const KEY = 'seat-machine-key-for-tests'
const OTHER_KEY = 'other-seat-machine-key'

let db: D1Database
let p1: CouplesPersonRow
let p2: CouplesPersonRow

async function seatRow(slug: string, entityId: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-10-17T00:00:00Z')`
    )
    .bind(entityId, ORG_ID, slug)
    .run()
}

beforeEach(async () => {
  db = createTestD1()
  await runMigrations(db, {
    files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
  })
  Object.assign(testEnv, { DB: db })
  const intakeId = await createCouplesInstance(db, {
    coupleName: 'Alex and Sam',
    agentName: 'Juniper',
    partners: [
      { name: 'Alex', email: 'alex@example.com' },
      { name: 'Sam', email: 'sam@example.com' },
    ],
  })
  await db
    .prepare('UPDATE hosted_agent_intake SET customer_slug = ? WHERE id = ?')
    .bind(SLUG, intakeId)
    .run()
  const inst = await getCouplesInstance(db, intakeId)
  await seatRow(SLUG, inst!.entity_id)
  await seedMachineCredential(db, SLUG, KEY)
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES ('ent-other', ?, 'Other', 'other', 'engaged', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(ORG_ID)
    .run()
  await seatRow(OTHER_SLUG, 'ent-other')
  await seedMachineCredential(db, OTHER_SLUG, OTHER_KEY)
  ;[p1, p2] = (await listPeople(db, intakeId)) as [CouplesPersonRow, CouplesPersonRow]
})

function bindRequest(body: unknown, opts: { key?: string; slug?: string } = {}) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (opts.key !== '') headers['Authorization'] = `Bearer ${opts.key ?? KEY}`
  headers['X-Tenant-Slug'] = opts.slug ?? SLUG
  const request = new Request('http://test.local/api/internal/telegram-bind', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  return { request, params: {}, locals: {} } as unknown as Parameters<typeof bindPost>[0]
}

async function telegramOf(personId: string): Promise<string | null> {
  const row = await db
    .prepare('SELECT telegram_id FROM hosted_agent_people WHERE id = ?')
    .bind(personId)
    .first<{ telegram_id: string | null }>()
  return row?.telegram_id ?? null
}

describe('token shape', () => {
  it('fits the Telegram start payload alphabet and length limit', () => {
    for (let i = 0; i < 50; i++) {
      const t = generateLinkToken()
      expect(t).toMatch(TOKEN_RE)
      expect(t.length).toBeLessThanOrEqual(64)
    }
    expect(telegramDeepLink('juniper_couple_bot', 'abc')).toBe(
      'https://t.me/juniper_couple_bot?start=abc'
    )
  })

  it('only the SHA-256 of a token is stored', async () => {
    const { token } = await issueLinkToken(db, p1.id)
    const rows = await db
      .prepare('SELECT token_hash FROM hosted_agent_link_tokens')
      .all<{ token_hash: string }>()
    expect(rows.results).toHaveLength(1)
    expect(rows.results[0].token_hash).toMatch(/^[0-9a-f]{64}$/)
    expect(rows.results[0].token_hash).not.toContain(token)
  })
})

describe('redeem', () => {
  it('binds the Telegram id to the partner the token was minted for', async () => {
    const { token } = await issueLinkToken(db, p1.id)
    const r = await redeemLinkToken(db, { token, seatSlug: SLUG, telegramUserId: '111' })
    expect(r).toEqual({ result: 'bound', personKey: 'p1', personId: p1.id })
    expect(await telegramOf(p1.id)).toBe('111')
    expect(await telegramOf(p2.id)).toBeNull()
  })

  it('FALSIFIER: single use, a replayed token is rejected', async () => {
    const { token } = await issueLinkToken(db, p1.id)
    await redeemLinkToken(db, { token, seatSlug: SLUG, telegramUserId: '111' })
    const again = await redeemLinkToken(db, { token, seatSlug: SLUG, telegramUserId: '999' })
    expect(again).toEqual({ result: 'rejected' })
    expect(await telegramOf(p1.id)).toBe('111')
  })

  it('FALSIFIER: an expired token is rejected', async () => {
    const minted = Date.UTC(2026, 9, 17, 12, 0, 0)
    const { token } = await issueLinkToken(db, p1.id, minted)
    const late = await redeemLinkToken(
      db,
      { token, seatSlug: SLUG, telegramUserId: '111' },
      minted + TOKEN_TTL_MS + 1000
    )
    expect(late).toEqual({ result: 'rejected' })
    const inTime = await redeemLinkToken(
      db,
      { token, seatSlug: SLUG, telegramUserId: '111' },
      minted + TOKEN_TTL_MS - 60_000
    )
    expect(inTime.result).toBe('bound')
  })

  it('FALSIFIER: a forged or altered token is rejected', async () => {
    const { token } = await issueLinkToken(db, p1.id)
    const forged = generateLinkToken()
    const altered = (token[0] === 'A' ? 'B' : 'A') + token.slice(1)
    for (const t of [forged, altered, `${token}x`, 'not a token']) {
      expect(
        await redeemLinkToken(db, { token: t, seatSlug: SLUG, telegramUserId: '111' })
      ).toEqual({ result: 'rejected' })
    }
    expect(await telegramOf(p1.id)).toBeNull()
  })

  it("FALSIFIER: another seat cannot redeem this instance's token", async () => {
    const { token } = await issueLinkToken(db, p1.id)
    const r = await redeemLinkToken(db, { token, seatSlug: OTHER_SLUG, telegramUserId: '111' })
    expect(r).toEqual({ result: 'rejected' })
  })

  it('FALSIFIER: minting a new link burns the old one', async () => {
    const first = await issueLinkToken(db, p1.id)
    await issueLinkToken(db, p1.id)
    const r = await redeemLinkToken(db, {
      token: first.token,
      seatSlug: SLUG,
      telegramUserId: '111',
    })
    expect(r).toEqual({ result: 'rejected' })
  })

  it("FALSIFIER: one partner's link cannot bind the other partner's Telegram id", async () => {
    const a = await issueLinkToken(db, p1.id)
    await redeemLinkToken(db, { token: a.token, seatSlug: SLUG, telegramUserId: '111' })
    const b = await issueLinkToken(db, p2.id)
    const r = await redeemLinkToken(db, { token: b.token, seatSlug: SLUG, telegramUserId: '111' })
    expect(r).toEqual({ result: 'rejected' })
    expect(await telegramOf(p2.id)).toBeNull()
  })

  it('a non-numeric Telegram id is rejected', async () => {
    const { token } = await issueLinkToken(db, p1.id)
    const r = await redeemLinkToken(db, { token, seatSlug: SLUG, telegramUserId: '@alex' })
    expect(r).toEqual({ result: 'rejected' })
  })
})

describe('POST /api/internal/telegram-bind', () => {
  it('binds through the seat credential', async () => {
    const { token } = await issueLinkToken(db, p2.id)
    const res = await bindPost(bindRequest({ token, telegram_user_id: '222' }))
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ result: 'bound', person: 'p2' })
    expect(await telegramOf(p2.id)).toBe('222')
  })

  it('FALSIFIER: no credential, no bind', async () => {
    const { token } = await issueLinkToken(db, p2.id)
    const res = await bindPost(bindRequest({ token, telegram_user_id: '222' }, { key: '' }))
    expect(res.status).toBe(401)
    expect(await telegramOf(p2.id)).toBeNull()
  })

  it("FALSIFIER: a seat's credential does not verify for another slug", async () => {
    const { token } = await issueLinkToken(db, p2.id)
    const res = await bindPost(
      bindRequest({ token, telegram_user_id: '222' }, { key: OTHER_KEY, slug: SLUG })
    )
    expect(res.status).toBe(401)
  })

  it('FALSIFIER: another seat authenticated as itself still cannot redeem', async () => {
    const { token } = await issueLinkToken(db, p2.id)
    const res = await bindPost(
      bindRequest({ token, telegram_user_id: '222' }, { key: OTHER_KEY, slug: OTHER_SLUG })
    )
    expect(await res.json()).toEqual({ result: 'rejected' })
    expect(await telegramOf(p2.id)).toBeNull()
  })

  it('a malformed body is a 400', async () => {
    const res = await bindPost(bindRequest({ token: 5 }))
    expect(res.status).toBe(400)
  })
})
