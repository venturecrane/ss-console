/**
 * Operator request cards (migration 0122): the wire parse, the email, and the
 * endpoint's send ledger.
 *
 * Posted at the REAL handler against a D1 built from the real migrations, so
 * the properties below are the ones the seat depends on: auth fails closed, a
 * card is marked sent only when Resend accepted it, a re-post after a send is
 * a duplicate and not a second email, a failed send is a 502 the seat retries,
 * and no card text (sender, subject, reply opening, matter) reaches D1.
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
  cardSubject,
  cardText,
  cleanText,
  parseRequestCard,
  type RequestCard,
} from '../src/lib/operator/request-card'

installWorkerdPolyfills()

const migrationsDir = path.join(path.resolve(__dirname, '..'), 'migrations')
const MACHINE_KEY = 'test-machine-request-card-key-32ch'
const SLUG = 'card-co'
const HASH = 'c'.repeat(64)

// Distinctive strings so a leak into any D1 column is findable by search.
const WHO = 'Pat Paralegal <pat@firm.example>'
const SUBJECT = 'Please pull the records for UNIQUESUBJECTMARK'
const OPENING = 'Hi Pat, I have pulled the records UNIQUEOPENINGMARK'
const MATTER = 'UNIQUEMATTERMARK 2026-001'

function body(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    card_key: `${HASH}:replied`,
    kind: 'replied',
    received_at: '2026-10-06T15:00:00Z',
    event_at: '2026-10-06T15:12:00Z',
    minutes: 12,
    who: WHO,
    subject: SUBJECT,
    reply_opening: OPENING,
    matter: MATTER,
    tools: ['smokeball_get_matter', 'smokeball_list_documents'],
    replies: 1,
    refused: 0,
    failed: 0,
    job: null,
    ...overrides,
  }
}

function parsed(overrides: Record<string, unknown> = {}): RequestCard {
  const r = parseRequestCard(body(overrides))
  if (!r.ok) throw new Error(`expected a valid card, field ${r.field} refused`)
  return r.card
}

describe('parseRequestCard: the wire contract', () => {
  it('accepts a replied card and normalizes its timestamps', () => {
    const card = parsed()
    expect(card.kind).toBe('replied')
    expect(card.receivedAt).toBe('2026-10-06T15:00:00.000Z')
    expect(card.tools).toEqual(['smokeball_get_matter', 'smokeball_list_documents'])
  })

  it.each([
    ['card_key', { card_key: 'abc:replied' }],
    ['card_key', { card_key: `${HASH}:no_reply` }], // suffix disagrees with kind
    ['kind', { kind: 'other' }],
    ['received_at', { received_at: 'yesterday' }],
    ['event_at', { event_at: 1700000000 }],
    ['minutes', { minutes: -1 }],
    ['minutes', { minutes: 1.5 }],
    ['who', { who: '   ' }],
    ['subject', { subject: null }],
    ['reply_opening', { reply_opening: 42 }],
    ['tools', { tools: ['Bad Token'] }],
    ['tools', { tools: Array.from({ length: 41 }, (_, i) => `t${i}`) }],
    ['refused', { refused: '0' }],
    ['job', { job: { lane: 'demand', state: 'delivered', reason: null } }], // job on a reply card
  ])('refuses a bad %s', (field, overrides) => {
    const r = parseRequestCard(body(overrides))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.field).toBe(field)
  })

  it('accepts the seat placeholders for a missing sender and subject (overlay#428)', () => {
    const card = parsed({ who: '(unknown sender)', subject: '(no subject)' })
    expect(card.who).toBe('(unknown sender)')
    expect(cardSubject(SLUG, card)).toMatch(/: \(no subject\)$/)
  })

  it('a job_done card must carry its job', () => {
    const base = { card_key: `${HASH}:job_done`, kind: 'job_done' }
    expect(parseRequestCard(body(base)).ok).toBe(false)
    const ok = parseRequestCard(
      body({ ...base, job: { lane: 'medchron', state: 'failed', reason: 'ocr_timeout' } })
    )
    expect(ok.ok).toBe(true)
    const badReason = parseRequestCard(
      body({ ...base, job: { lane: 'medchron', state: 'failed', reason: 'free text here' } })
    )
    expect(badReason.ok).toBe(false)
  })

  it('strips line breaks and bidi controls and caps display text', () => {
    expect(cleanText(`a\r\nBcc: x@y${String.fromCharCode(0x202e)}`, 200)).toBe('a Bcc: x@y')
    expect(cleanText('x'.repeat(500), 200)).toHaveLength(200)
    const card = parsed({ subject: `line one\nline two ${'y'.repeat(300)}` })
    expect(card.subject).not.toMatch(/\n/)
    expect(card.subject.length).toBeLessThanOrEqual(200)
  })
})

describe('the email', () => {
  it('subject lines per kind, with the subject cut to 70', () => {
    expect(cardSubject(SLUG, parsed())).toBe(
      `[SMD Ops] ${SLUG} request: first reply in 12m: ${SUBJECT}`
    )
    const alarm = parsed({
      card_key: `${HASH}:no_reply`,
      kind: 'no_reply',
      minutes: 31,
      reply_opening: null,
    })
    expect(cardSubject(SLUG, alarm)).toMatch(/^\[SMD Ops\] card-co NO REPLY after 31m: /)
    const job = parsed({
      card_key: `${HASH}:job_done`,
      kind: 'job_done',
      job: { lane: 'demand', state: 'delivered', reason: null },
      subject: 'z'.repeat(150),
    })
    const s = cardSubject(SLUG, job)
    expect(s.startsWith('[SMD Ops] card-co demand job delivered: ')).toBe(true)
    expect(s.length).toBe('[SMD Ops] card-co demand job delivered: '.length + 70)
  })

  it('the body carries the card and no em dash', () => {
    const text = cardText(SLUG, parsed())
    for (const s of [WHO, SUBJECT, OPENING, MATTER, 'Tools run (2)', 'Refused: 0']) {
      expect(text).toContain(s)
    }
    expect(text).not.toMatch(new RegExp(String.fromCharCode(0x2014)))
  })
})

describe('POST /api/internal/operator-request-card', () => {
  let db: D1Database
  let fetchMock: ReturnType<typeof vi.fn>

  async function seed(): Promise<void> {
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES ('org-card', 'Card Org', 'card-org', datetime('now'), datetime('now'))`
      )
      .run()
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
         VALUES ('ent-card', 'org-card', ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(SLUG, SLUG)
      .run()
    await db
      .prepare(
        `INSERT INTO customer_configs
           (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
         VALUES ('ent-card', 'org-card', ?, '1.0.0', '[]', 'sha', '2026-10-06T00:00:00Z')`
      )
      .bind(SLUG)
      .run()
  }

  async function post(payload: unknown, key = MACHINE_KEY): Promise<Response> {
    const request = new Request('http://test.local/api/internal/operator-request-card', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'X-Tenant-Slug': SLUG,
        'Content-Type': 'application/json',
      },
      body: typeof payload === 'string' ? payload : JSON.stringify(payload),
    })
    return POST({ request, params: {}, locals: {} } as unknown as Parameters<typeof POST>[0])
  }

  async function rows(): Promise<Record<string, unknown>[]> {
    const r = await db
      .prepare('SELECT * FROM operator_request_cards')
      .all<Record<string, unknown>>()
    return r.results
  }

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seed()
    await seedMachineCredential(db, SLUG, MACHINE_KEY)
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, {
      DB: db,
      RESEND_API_KEY: 're_test',
      ALERT_TO_EMAIL: 'team@smd.services',
      ALERT_FROM_EMAIL: 'SMD Services Ops <team@smd.services>',
    })
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: 'r1' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('a wrong key is 401 and nothing is recorded or sent', async () => {
    const res = await post(body(), 'wrong-key-wrong-key-wrong-key-xx')
    expect(res.status).toBe(401)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(await rows()).toHaveLength(0)
  })

  it('a bad body is 400 naming the field, and nothing is sent', async () => {
    const res = await post(body({ minutes: 'twelve' }))
    expect(res.status).toBe(400)
    const refused: { field?: string } = await res.json()
    expect(refused.field).toBe('minutes')
    expect(fetchMock).not.toHaveBeenCalled()
    expect((await post('not json')).status).toBe(400)
  })

  it('emails the card once to SMD ops with an idempotency key, then reports duplicates', async () => {
    const first = await post(body())
    expect(first.status).toBe(200)
    expect(await first.json()).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://api.resend.com/emails')
    const headers = init.headers as Record<string, string>
    expect(headers['Idempotency-Key']).toBe(`${SLUG}:${HASH}:replied`)
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(sent.to).toEqual(['team@smd.services'])
    expect(String(sent.subject)).toContain('first reply in 12m')
    expect(String(sent.text)).toContain(OPENING)

    const again = await post(body())
    expect(again.status).toBe(200)
    expect(await again.json()).toEqual({ ok: true, duplicate: true })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    const [row] = await rows()
    expect(row.status).toBe('sent')
    expect(row.attempts).toBe(1)
    expect(row.tools_count).toBe(2)
  })

  it('a refused send is 502, stays pending, and the retry sends', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 500 }))
    const failed = await post(body())
    expect(failed.status).toBe(502)
    let [row] = await rows()
    expect(row.status).toBe('pending')
    expect(row.attempts).toBe(1)

    const retry = await post(body())
    expect(retry.status).toBe(200)
    ;[row] = await rows()
    expect(row.status).toBe('sent')
    expect(row.attempts).toBe(2)
  })

  it('a drafting job card is recorded and sent for every terminal state (0123)', async () => {
    // FALSIFIER: drop 'drafting' from JOB_LANES and the parse refuses it (400);
    // drop migration 0123 and the job_lane CHECK rejects the INSERT.
    for (const state of ['delivered', 'failed', 'held'] as const) {
      const key = `${state
        .padEnd(64, '0')
        .replace(/[^0-9a-f]/g, 'a')
        .slice(0, 64)}:job_done`
      const res = await post(
        body({ card_key: key, kind: 'job_done', job: { lane: 'drafting', state, reason: null } })
      )
      expect(res.status, state).toBe(200)
    }
    const recorded = (await rows()).filter((r) => r.job_lane === 'drafting')
    expect(recorded.map((r) => r.job_state).sort()).toEqual(['delivered', 'failed', 'held'])
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('a litigation job card is recorded and sent for every terminal state (0124)', async () => {
    // FALSIFIER: drop 'litigation' from JOB_LANES and the parse refuses it (400);
    // drop migration 0124 and the job_lane CHECK rejects the INSERT.
    for (const state of ['delivered', 'failed', 'held'] as const) {
      const key = `${`l${state}`
        .padEnd(64, '0')
        .replace(/[^0-9a-f]/g, 'b')
        .slice(0, 64)}:job_done`
      const res = await post(
        body({ card_key: key, kind: 'job_done', job: { lane: 'litigation', state, reason: null } })
      )
      expect(res.status, state).toBe(200)
    }
    const recorded = (await rows()).filter((r) => r.job_lane === 'litigation')
    expect(recorded.map((r) => r.job_state).sort()).toEqual(['delivered', 'failed', 'held'])
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('no Resend key is a failure, never a pretend send', async () => {
    delete (testEnv as unknown as Record<string, unknown>).RESEND_API_KEY
    expect((await post(body())).status).toBe(502)
    expect((await rows())[0].status).toBe('pending')
  })

  it('no card text reaches D1 (ADR 0052 s5)', async () => {
    await post(body())
    await post(
      body({
        card_key: `${HASH}:job_done`,
        kind: 'job_done',
        job: { lane: 'demand', state: 'held', reason: 'quote_check' },
      })
    )
    const dump = JSON.stringify(await rows())
    for (const mark of ['UNIQUESUBJECTMARK', 'UNIQUEOPENINGMARK', 'UNIQUEMATTERMARK', 'pat@firm']) {
      expect(dump).not.toContain(mark)
    }
    const cols = await db
      .prepare("SELECT name FROM pragma_table_info('operator_request_cards')")
      .all<{ name: string }>()
    expect(cols.results.map((c) => c.name).sort()).toEqual(
      [
        'attempts',
        'card_key',
        'customer_slug',
        'event_at',
        'failed',
        'first_seen_at',
        'job_lane',
        'job_state',
        'kind',
        'minutes',
        'received_at',
        'refused',
        'replies',
        'sent_at',
        'status',
        'tools_count',
      ].sort()
    )
  })
})
