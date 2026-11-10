/**
 * A seat that authors `escalation.request_card_text: false` (a guide seat whose
 * people keep private notes and leads, ss-console #3946) sends SMD ops request
 * cards with who and what happened only. Two walls, each tested here through
 * the real endpoint and the real email body:
 *
 *   1. the seat posts the card with `text_withheld: true` and no words, and the
 *      email says the seat does not send them;
 *   2. a seat on an older image that still posts words, but whose projected
 *      config (`customer_configs.escalation_json`) says `request_card_text:
 *      false`, still gets an email with no words.
 *
 * A seat that authors nothing gets exactly the email it got before.
 *
 * FALSIFIER: on main (before the switch) the card's words were always in the
 * email, so every `not.toContain(MARK)` below fails there.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import { seedMachineCredential } from './helpers/machine-credential'
import { POST } from '../src/pages/api/internal/operator-request-card'
import {
  cardText,
  seatSendsCardText,
  withholdCardText,
} from '../src/lib/operator/request-card-email'
import { parseRequestCard } from '../src/lib/operator/request-card'

installWorkerdPolyfills()

const migrationsDir = path.join(path.resolve(__dirname, '..'), 'migrations')
const MACHINE_KEY = 'test-machine-request-card-key-32ch'
const SLUG = 'guide-co'
const HASH = 'd'.repeat(64)
const MARK = 'CANARYWORDS9x2'

function body(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    card_key: `${HASH}:replied`,
    kind: 'replied',
    received_at: '2026-11-10T15:00:00Z',
    event_at: '2026-11-10T15:02:00Z',
    minutes: 2,
    who: 'rep-a@example.test',
    subject: `Acme lead ${MARK}`,
    request_text: `Please keep this: ${MARK}`,
    reply_opening: `Kept: ${MARK}`,
    reply_text: `Kept your note: ${MARK}`,
    matter: null,
    tools: ['record_store_write'],
    replies: 1,
    refused: 0,
    failed: 0,
    job: null,
    ...overrides,
  }
}

describe('the email body for a card whose seat withholds words', () => {
  it('carries who and what happened and none of the words', () => {
    const r = parseRequestCard(body())
    if (!r.ok) throw new Error(r.field)
    const text = cardText(SLUG, withholdCardText(r.card))
    expect(text).toContain('rep-a@example.test')
    expect(text).toContain('answered')
    expect(text).toContain('does not send the words')
    expect(text).not.toContain(MARK)
  })

  it('a card that says it was withheld is rendered withheld, whatever it carries', () => {
    const r = parseRequestCard(body({ text_withheld: true }))
    if (!r.ok) throw new Error(r.field)
    expect(r.card.textWithheld).toBe(true)
    expect(cardText(SLUG, r.card)).not.toContain(MARK)
  })

  it('refuses a text_withheld that is not a boolean', () => {
    const r = parseRequestCard(body({ text_withheld: 'yes' }))
    expect(r.ok).toBe(false)
  })

  it('reads the projected switch, and anything unreadable keeps the words', () => {
    expect(seatSendsCardText(JSON.stringify({ request_card_text: false }))).toBe(false)
    expect(seatSendsCardText(JSON.stringify({ request_card_text: true }))).toBe(true)
    expect(seatSendsCardText(JSON.stringify({ red_flag_recipients: [] }))).toBe(true)
    expect(seatSendsCardText(null)).toBe(true)
    expect(seatSendsCardText('{not json')).toBe(true)
  })
})

describe('POST /api/internal/operator-request-card on a seat that withholds words', () => {
  let db: D1Database
  let fetchMock: ReturnType<typeof vi.fn>

  async function seed(escalation: Record<string, unknown> | null): Promise<void> {
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES ('org-g', 'Guide Org', 'guide-org', datetime('now'), datetime('now'))`
      )
      .run()
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
         VALUES ('ent-g', 'org-g', ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(SLUG, SLUG)
      .run()
    await db
      .prepare(
        `INSERT INTO customer_configs
           (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at,
            escalation_json, seat_json)
         VALUES ('ent-g', 'org-g', ?, '1.0.0', '[]', 'sha', '2026-11-10T00:00:00Z', ?, ?)`
      )
      .bind(
        SLUG,
        escalation === null ? null : JSON.stringify(escalation),
        JSON.stringify({ kind: 'customer', product: 'operator' })
      )
      .run()
  }

  async function post(payload: unknown): Promise<Response> {
    const request = new Request('http://test.local/api/internal/operator-request-card', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${MACHINE_KEY}`,
        'X-Tenant-Slug': SLUG,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    return POST({ request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0])
  }

  async function sentText(): Promise<string> {
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>
    return `${String(sent.subject)}\n${String(sent.text)}`
  }

  async function setUp(escalation: Record<string, unknown> | null): Promise<void> {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed(escalation)
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, RESEND_API_KEY: 're_test' })
  }

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 'r1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('a seat whose config says no words gets an email with no words, even if it posts them', async () => {
    await setUp({ red_flag_recipients: ['a@b.example'], request_card_text: false })
    expect((await post(body())).status).toBe(200)
    const text = await sentText()
    expect(text).toContain('rep-a@example.test')
    expect(text).not.toContain(MARK)
  })

  it('a card the seat marked withheld sends no words on a seat whose projection is silent', async () => {
    await setUp(null)
    expect((await post(body({ text_withheld: true }))).status).toBe(200)
    expect(await sentText()).not.toContain(MARK)
  })

  it('a seat that authors nothing gets the words it always got', async () => {
    await setUp({ red_flag_recipients: ['a@b.example'] })
    expect((await post(body())).status).toBe(200)
    expect(await sentText()).toContain(MARK)
  })
})
