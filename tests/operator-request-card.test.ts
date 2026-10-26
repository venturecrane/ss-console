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
  jobsQueued,
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
    ['card_key', { card_key: `${HASH}:replied:delivered-2` }], // only job_done carries an ending
    ['card_key', { card_key: `${HASH}:job_done:delivered-2` }], // kind replied, key job_done
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
    ['request_opening', { request_opening: 42 }],
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

  it('a job_done key may carry its job ending, so a resumed job cards again', () => {
    // FALSIFIER: the pre-fix key pattern (`<hash>:job_done` only) refuses these,
    // and a resumed job's real ending never reaches SMD.
    const job = { lane: 'demand', state: 'delivered', reason: null }
    for (const ending of ['delivered-2', 'failed-1', 'held-12']) {
      const r = parseRequestCard(
        body({ card_key: `${HASH}:job_done:${ending}`, kind: 'job_done', job })
      )
      expect(r.ok).toBe(true)
      if (r.ok) expect(r.card.cardKey.split(':')[0]).toBe(HASH)
    }
    for (const bad of ['running-1', 'delivered-0', 'delivered-x', 'delivered-12345', 'delivered']) {
      const r = parseRequestCard(
        body({ card_key: `${HASH}:job_done:${bad}`, kind: 'job_done', job })
      )
      expect(r.ok).toBe(false)
    }
  })

  it('a held_reply card carries the requester words and a HELD job, and nothing else does (0136)', () => {
    // FALSIFIER: before 0136 the kind is refused outright; a held_reply whose
    // job is delivered, or that names no job, is refused on `job`.
    const held = { lane: 'medchron', state: 'held', reason: 'identity', id: HELD }
    const r = parseRequestCard(heldReplyBody())
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.card.kind).toBe('held_reply')
      expect(r.card.requestOpening).toBe(WROTE)
      expect(r.card.job).toEqual(held)
    }
    const bad = [
      heldReplyBody({ job: null }),
      heldReplyBody({ job: { ...held, state: 'delivered' } }),
      heldReplyBody({ job: { ...held, id: 'not-a-ulid' } }),
      body({ card_key: `${HASH}:held_reply` }), // kind replied, key held_reply
    ]
    for (const b of bad) expect(parseRequestCard(b).ok).toBe(false)
    // A requester message with no words (an attachment alone) is still a card.
    expect(parseRequestCard(heldReplyBody({ request_opening: null })).ok).toBe(true)
    // The id is optional on every other job card; a job_done without one still parses.
    const done = parseRequestCard(
      body({
        card_key: `${HASH}:job_done`,
        kind: 'job_done',
        job: { lane: 'medchron', state: 'held', reason: null },
      })
    )
    expect(done.ok && done.card.job?.id).toBeNull()
  })

  it('strips line breaks and bidi controls and caps display text', () => {
    expect(cleanText(`a\r\nBcc: x@y${String.fromCharCode(0x202e)}`, 200)).toBe('a Bcc: x@y')
    expect(cleanText('x'.repeat(500), 200)).toHaveLength(200)
    const card = parsed({ subject: `line one\nline two ${'y'.repeat(300)}` })
    expect(card.subject).not.toMatch(/\n/)
    expect(card.subject.length).toBeLessThanOrEqual(200)
  })
})

const HELD = '01M4RW99P9XHWPJ228MSSWEXGG'
const WROTE = 'Just leave it out of the chronology. UNIQUEWROTEMARK'
function heldReplyBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return body({
    card_key: `${HASH}:held_reply`,
    kind: 'held_reply',
    reply_opening: null,
    request_opening: WROTE,
    tools: [],
    replies: 0,
    job: { lane: 'medchron', state: 'held', reason: 'identity', id: HELD },
    ...overrides,
  })
}
function laneBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return body({
    card_key: `${HASH}:lane_unproven:document-drafter.discovery_set`,
    kind: 'lane_unproven',
    reply_opening: null,
    lane: {
      key: 'document-drafter.discovery_set',
      job_lane: 'drafting',
      held_job: HELD,
      why: 'never_proven',
    },
    ...overrides,
  })
}

describe('jobsQueued', () => {
  it('names each known lane a turn submitted, once, sorted; ignores the rest', () => {
    expect(jobsQueued([])).toBe('')
    expect(jobsQueued(['medchron_job_status', 'read_file'])).toBe('')
    expect(jobsQueued(['medchron_job_submit'])).toBe('medchron')
    expect(jobsQueued(['drafting_job_submit', 'demand_job_submit', 'drafting_job_submit'])).toBe(
      'demand,drafting'
    )
    expect(jobsQueued(['unknown_job_submit', 'litigation_job_submit'])).toBe('litigation')
  })
})

describe('parseRequestCard: the lane_unproven card (0125)', () => {
  it('accepts a held-job card and an in-turn first-use card', () => {
    const held = parseRequestCard(laneBody())
    expect(held.ok && held.card.lane).toEqual({
      key: 'document-drafter.discovery_set',
      jobLane: 'drafting',
      heldJob: HELD,
      why: 'never_proven',
    })
    const inTurn = parseRequestCard(
      laneBody({
        card_key: `${HASH}:lane_unproven:file-work-requests`,
        lane: { key: 'file-work-requests', job_lane: null, held_job: null, why: 'never_proven' },
      })
    )
    expect(inTurn.ok && inTurn.card.lane?.heldJob).toBeNull()
  })

  it.each([
    ['the key names a different lane', { card_key: `${HASH}:lane_unproven:demand-letter-drafter` }],
    ['no lane object', { lane: null }],
    [
      'a job id that is not one',
      {
        lane: {
          key: 'document-drafter.discovery_set',
          job_lane: 'drafting',
          held_job: 'x',
          why: 'never_proven',
        },
      },
    ],
    [
      'a held job with no job lane',
      {
        lane: {
          key: 'document-drafter.discovery_set',
          job_lane: null,
          held_job: HELD,
          why: 'never_proven',
        },
      },
    ],
    [
      'an unknown why',
      {
        lane: {
          key: 'document-drafter.discovery_set',
          job_lane: 'drafting',
          held_job: HELD,
          why: 'because',
        },
      },
    ],
  ])('refuses %s', (_name, overrides) => {
    const r = parseRequestCard(laneBody(overrides))
    expect(r.ok).toBe(false)
  })

  it('no other kind may carry a lane', () => {
    const r = parseRequestCard(body({ lane: laneBody().lane }))
    expect(r.ok === false && r.field).toBe('lane')
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

  it('a held_reply email leads with the requester words and tells SMD to record the decision', () => {
    const card = parsed(heldReplyBody())
    expect(cardSubject(SLUG, card)).toBe(
      `[SMD Ops] ${SLUG} REQUESTER WROTE on held medchron job: ${SUBJECT}`
    )
    const text = cardText(SLUG, card)
    for (const s of [
      `Job: medchron ${HELD} is HELD (identity).`,
      'Requester wrote:',
      WROTE,
      'record the',
      'decision on the seat',
      'will not act on it',
    ]) {
      expect(text).toContain(s)
    }
    expect(text).not.toContain('Reply opening:')
    expect(text).not.toMatch(/—/)
    expect(cardText(SLUG, parsed(heldReplyBody({ request_opening: null })))).toContain(
      '(no words in the message)'
    )
  })

  it('names a matter only when the seat named one (no law-firm line on other seats)', () => {
    expect(cardText(SLUG, parsed())).toContain(`Matter: ${MATTER}`)
    expect(cardText(SLUG, parsed({ matter: null }))).not.toContain('Matter:')
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

  it('a reply card records the jobs its turn queued; none is empty, never NULL (0134)', async () => {
    // FALSIFIER: drop jobsQueued from recordCard's INSERT and the first row's
    // jobs_queued is NULL, so a queued chronology reads as no job (2026-10-22).
    const queuing = 'd'.repeat(64)
    await post(
      body({
        card_key: `${queuing}:replied`,
        tools: ['smokeball_get_matter', 'medchron_job_submit', 'medchron_job_status'],
      })
    )
    await post(body())
    const byKey = Object.fromEntries((await rows()).map((r) => [r.card_key, r.jobs_queued]))
    expect(byKey[`${queuing}:replied`]).toBe('medchron')
    expect(byKey[`${HASH}:replied`]).toBe('')
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

  it('a lane_unproven card is recorded with its lane and job lane, and its email says how to prove it (0125)', async () => {
    // FALSIFIER: drop migration 0125 and the kind CHECK rejects the INSERT
    // (500); drop 'lane_unproven' from CARD_KINDS and the parse refuses (400).
    const res = await post(
      laneBody({
        card_key: `${HASH}:lane_unproven:negotiation-watch`,
        lane: { key: 'negotiation-watch', job_lane: 'negotiation', held_job: HELD, why: 'revoked' },
      })
    )
    expect(res.status).toBe(200)
    const [row] = await rows()
    expect(row).toMatchObject({
      kind: 'lane_unproven',
      lane_key: 'negotiation-watch',
      job_lane: 'negotiation',
    })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(String(init.body)) as { subject: string; text: string }
    expect(sent.subject).toContain('NEW KIND OF ASK, job waiting: negotiation-watch')
    expect(sent.text).toContain(`operator/bin/lane-proof.sh ${SLUG} dry-run ${HELD}`)
    expect(sent.text).toContain('QUEUED and will not run until the lane is proven')
    expect(sent.text).not.toMatch(/\u2014/)
  })

  it('a held_reply card is recorded with its held job and emailed with the requester words (0136)', async () => {
    // FALSIFIER: drop migration 0136 and the kind CHECK rejects the INSERT
    // (500); drop 'held_reply' from CARD_KINDS and the parse refuses (400).
    const res = await post(heldReplyBody())
    expect(res.status).toBe(200)
    const [row] = await rows()
    expect(row).toMatchObject({ kind: 'held_reply', job_lane: 'medchron', job_state: 'held' })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(String(init.body)) as { subject: string; text: string }
    expect(sent.subject).toContain('REQUESTER WROTE on held medchron job')
    expect(sent.text).toContain(WROTE)
    expect(sent.text).toContain(HELD)
    // ADR 0052 s5: the words reach the email, never D1.
    expect(JSON.stringify(await rows())).not.toContain('UNIQUEWROTEMARK')
  })

  it('a followup card is recorded and emailed with the later reply words (0141)', async () => {
    // FALSIFIER: drop migration 0141 and the kind CHECK rejects the INSERT
    // (500); drop 'followup' from CARD_KINDS and the parse refuses (400).
    const res = await post(
      body({
        card_key: `${HASH}:followup-${'a'.repeat(16)}`,
        kind: 'followup',
        minutes: 50,
        reply_opening: 'Correction: both records are unreadable UNIQUELATERMARK',
        tools: [],
      })
    )
    expect(res.status).toBe(200)
    const [row] = await rows()
    expect(row).toMatchObject({ kind: 'followup', minutes: 50, job_lane: null })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const sent = JSON.parse(String(init.body)) as { subject: string; text: string }
    expect(sent.subject).toBe(`[SMD Ops] ${SLUG} later reply sent at +50m: ${SUBJECT}`)
    expect(sent.text).toContain('UNIQUELATERMARK')
    expect(sent.text).toContain('It is what the requester was told')
    expect(sent.text).not.toMatch(/—/)
    expect(JSON.stringify(await rows())).not.toContain('UNIQUELATERMARK')
  })

  it('a followup key must name its kind and carry no job (0141)', async () => {
    const followup = { kind: 'followup', tools: [] }
    // A followup key with the wrong suffix, a followup kind on a replied key,
    // and a followup carrying a job are each a 400, never a partial card.
    expect(
      (await post(body({ ...followup, card_key: `${HASH}:followup-${'a'.repeat(15)}` }))).status
    ).toBe(400)
    expect((await post(body({ ...followup, card_key: `${HASH}:replied` }))).status).toBe(400)
    expect(
      (
        await post(
          body({
            ...followup,
            card_key: `${HASH}:followup-${'b'.repeat(16)}`,
            job: { lane: 'medchron', state: 'held', reason: null },
          })
        )
      ).status
    ).toBe(400)
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
        'jobs_queued',
        'kind',
        'lane_key',
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
