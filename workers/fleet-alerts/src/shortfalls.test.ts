/**
 * The shortfall pager, executed against real SQLite (`node:sqlite`) with the
 * `operator_shortfalls` table read out of migration 0120 itself, so the SQL in
 * ./shortfalls is the thing under test (the stale-holds lesson: a fake D1 that
 * reimplements a query cannot observe a wrong column).
 */

import { DatabaseSync } from 'node:sqlite'
import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DIGEST_CRON,
  nextStep,
  notifyShortfalls,
  parseShortfallEvents,
  phoenixTime,
  emailsAPerson,
  THROTTLE_MINUTES,
  whatHappened,
} from './shortfalls'
import { sendShortfallDigest, toolWords } from './shortfall-digest'
import type { Env } from './index'
import type { FleetStatusRow } from './fleet-status'

const MIGRATION = readFileSync(
  new URL('../../../migrations/0120_operator_shortfalls.sql', import.meta.url).pathname,
  'utf8'
)

const REQUEST_AT = readFileSync(
  new URL('../../../migrations/0143_operator_shortfall_request_at.sql', import.meta.url).pathname,
  'utf8'
)

function ledgerDdl(): string {
  const table = MIGRATION.match(/CREATE TABLE operator_shortfalls \([\s\S]*?\n\);/)
  const index = MIGRATION.match(/CREATE INDEX idx_operator_shortfalls_unnotified[\s\S]*?;/)
  if (!table || !index) throw new Error('migration 0120 no longer defines operator_shortfalls')
  const added = REQUEST_AT.match(/^ALTER TABLE operator_shortfalls ADD COLUMN request_at TEXT;$/m)
  if (!added) throw new Error('migration 0143 no longer adds operator_shortfalls.request_at')
  return `${table[0]}\n${index[0]}\n${added[0]}`
}

/** Just enough of the D1 surface for ./shortfalls, over a real SQLite. */
function d1(db: DatabaseSync): D1Database {
  return {
    prepare(sql: string) {
      let args: unknown[] = []
      const stmt = {
        bind(...a: unknown[]) {
          args = a
          return stmt
        },
        async run() {
          db.prepare(sql).run(...(args as never[]))
          return { success: true }
        },
        async all<T>() {
          return { results: db.prepare(sql).all(...(args as never[])) as T[] }
        },
        async first<T>() {
          return (db.prepare(sql).get(...(args as never[])) ?? null) as T | null
        },
      }
      return stmt
    },
  } as unknown as D1Database
}

function setup(): { db: DatabaseSync; env: Env } {
  const db = new DatabaseSync(':memory:')
  db.exec(ledgerDdl())
  const env = { DB: d1(db), RESEND_API_KEY: 'test-key' } as unknown as Env
  return { db, env }
}

function stubResend(ok = true): ReturnType<typeof vi.fn> {
  const mock = vi
    .fn()
    .mockImplementation(async () =>
      ok
        ? new Response(JSON.stringify({ id: 'resend-shortfall-1' }), { status: 200 })
        : new Response('resend down', { status: 500 })
    )
  vi.stubGlobal('fetch', mock)
  return mock
}

function sent(mock: ReturnType<typeof vi.fn>): Array<{ subject: string; html: string }> {
  return mock.mock.calls.map((call) => JSON.parse(String((call[1] as RequestInit).body)))
}

const ev = (over: Record<string, unknown> = {}) => ({
  ts: '2026-10-01T15:37:33.000Z',
  class: 'partial',
  tool: 'mcp_smokeball_file_attachment_pages_to_matter',
  routine: 'combined-post-intake',
  code: 'filed 0 of 52',
  key: 'partial:abc123',
  ...over,
})

function row(events: unknown[] | null, extra: Partial<FleetStatusRow> = {}): FleetStatusRow {
  return {
    customer_slug: 'ashton-price',
    shortfalls: events ? events.length : null,
    shortfalls_json: events ? JSON.stringify(events) : null,
    ...extra,
  } as FleetStatusRow
}

const NOW = Date.parse('2026-10-05T20:00:00Z')

const HELD = ev({
  class: 'failed',
  tool: 'reply_relay',
  routine: null,
  code: 'reply_held:output_checklist',
  key: 'failed:held',
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('notifyShortfalls', () => {
  it('emails a reply that did not go out, in words, and marks it notified', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    const out = await notifyShortfalls(env, [row([HELD])], NOW)
    expect(out).toEqual([
      expect.objectContaining({ customer_slug: 'ashton-price', events: 1, emailed: true }),
    ])
    const [mail] = sent(mock)
    expect(mail.subject).toBe('[SMD Ops] ashton-price: a reply did not go out')
    expect(mail.html).toContain('<strong>WHAT HAPPENED:</strong> The Operator wrote a reply')
    expect(mail.html).toContain('<strong>WHAT YOU NEED TO DO:</strong> Someone may be waiting.')
    expect(mail.html).toContain('reply_held:output_checklist')
    expect(mail.html).not.toContain('SHORTFALL')
    const stored = db.prepare('SELECT notified_at FROM operator_shortfalls').all() as Array<{
      notified_at: string | null
    }>
    expect(stored[0].notified_at).not.toBeNull()
  })

  it('names the request in Phoenix time, never UTC', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    const explained = {
      ...HELD,
      ts: '2026-10-28T01:03:34.941Z',
      request_at: '2026-10-28T00:54:43.000Z',
    }
    await notifyShortfalls(env, [row([explained])], NOW)
    const [mail] = sent(mock)
    expect(mail.html).toContain('Oct 27, 6:03 PM MST')
    expect(mail.html).toContain('The request received Oct 27, 5:54 PM MST')
    expect(mail.html).toContain(
      'the email for the ashton-price request received Oct 27, 5:54 PM MST'
    )
    expect(mail.html).not.toContain('UTC')
    const stored = db.prepare('SELECT request_at FROM operator_shortfalls').get() as {
      request_at: string
    }
    expect(stored.request_at).toBe('2026-10-28T00:54:43.000Z')
  })

  it('says one thing once: the four identical paragraphs of 2026-11-02', async () => {
    const { env } = setup()
    const mock = stubResend()
    const same = [1, 2, 3, 4].map((n) => ({ ...HELD, key: `failed:${n}` }))
    await notifyShortfalls(env, [row(same)], NOW)
    const [mail] = sent(mock)
    expect(mail.html.match(/WHAT HAPPENED/g)).toHaveLength(1)
    expect(mail.html).toContain('This happened 4 times.')
    expect(mail.subject).toBe('[SMD Ops] ashton-price: a reply did not go out (4)')
  })

  it.each([
    ['a late delivery proof', 'failed', 'negotiation_job', 'proof:reply_unproven'],
    ['a held job, which has its own request email', 'failed', 'medchron_job', 'proof:review_held'],
    ['a reply that said no, ditto', 'failed', 'reply_relay', 'reply_could_not:cannot_find'],
    ['a failed call', 'failed', 'mcp_smokeball_get_matter', 'http_404'],
    ['a bundle filed short', 'partial', 'file_pages', 'filed 0 of 52'],
  ])('records %s for the triage agent, without an email', async (_n, cls, tool, code) => {
    const { db, env } = setup()
    const mock = stubResend()
    const out = await notifyShortfalls(
      env,
      [row([ev({ class: cls, tool, code, key: 'q:1' })])],
      NOW
    )
    expect(out).toEqual([])
    expect(sent(mock)).toHaveLength(0)
    expect(db.prepare('SELECT code FROM operator_shortfalls').all()).toEqual([{ code }])
  })

  it('a quiet row does not start the throttle and delay a real one', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(env, [row([ev()])], NOW)
    await notifyShortfalls(env, [row([ev(), HELD])], NOW + 60_000)
    expect(sent(mock)).toHaveLength(1)
  })

  it('fills request_at on a row an older beat stored without it, never overwrites', async () => {
    const { db, env } = setup()
    stubResend()
    await notifyShortfalls(env, [row([{ ...HELD, key: 'k1' }])], NOW)
    await notifyShortfalls(
      env,
      [row([{ ...HELD, key: 'k1', request_at: '2026-10-01T15:00:00.000Z' }])],
      NOW
    )
    await notifyShortfalls(
      env,
      [row([{ ...HELD, key: 'k1', request_at: '2026-10-01T16:00:00.000Z' }])],
      NOW
    )
    const stored = db.prepare('SELECT request_at FROM operator_shortfalls').get() as {
      request_at: string
    }
    expect(stored.request_at).toBe('2026-10-01T15:00:00.000Z')
  })

  it('pages an event once though it rides every heartbeat for a day', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(env, [row([HELD])], NOW)
    await notifyShortfalls(env, [row([HELD])], NOW + 2 * 60_000)
    await notifyShortfalls(env, [row([HELD])], NOW + (THROTTLE_MINUTES + 5) * 60_000)
    expect(sent(mock)).toHaveLength(1)
  })

  it('records not_allowed for the digest and does not page it', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    await notifyShortfalls(
      env,
      [row([ev({ class: 'not_allowed', code: 'refuse', key: 'not_allowed:1' })])],
      NOW
    )
    expect(sent(mock)).toHaveLength(0)
    expect(db.prepare('SELECT class FROM operator_shortfalls').all()).toEqual([
      { class: 'not_allowed' },
    ])
  })

  it('holds new events inside the throttle window, then sends them together', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(env, [row([HELD])], NOW)
    const later = [
      HELD,
      ev({ class: 'limit', code: 'over_page_cap', key: 'limit:2' }),
      { ...HELD, code: 'reply_failed:send_error', key: 'failed:3' },
    ]
    await notifyShortfalls(env, [row(later)], NOW + 60_000)
    expect(sent(mock)).toHaveLength(1)
    // The throttle reads notified_at written by SQLite's clock, so step past it
    // by rewriting the stamp rather than waiting.
    ;(
      env.DB as unknown as {
        prepare: (s: string) => { bind: () => { run: () => Promise<unknown> } }
      }
    )
      .prepare(
        `UPDATE operator_shortfalls SET notified_at = '2026-10-05 19:00:00' WHERE notified_at IS NOT NULL`
      )
      .bind()
      .run()
    await notifyShortfalls(env, [row(later)], NOW)
    const mails = sent(mock)
    expect(mails).toHaveLength(2)
    expect(mails[1].subject).toBe(
      '[SMD Ops] ashton-price: a reply did not go out, a limit stopped the work'
    )
  })

  it('keeps the rows un-notified when Resend fails, so the next tick retries', async () => {
    const { db, env } = setup()
    stubResend(false)
    const out = await notifyShortfalls(env, [row([HELD])], NOW)
    expect(out[0].emailed).toBe(false)
    expect(db.prepare('SELECT notified_at FROM operator_shortfalls').all()).toEqual([
      { notified_at: null },
    ])
  })

  it('holds a seat that reports nothing', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    expect(await notifyShortfalls(env, [row(null)], NOW)).toEqual([])
    expect(sent(mock)).toHaveLength(0)
    expect(db.prepare('SELECT COUNT(*) AS n FROM operator_shortfalls').get()).toEqual({ n: 0 })
  })

  it('names audit writes lost inside the window so an undercount is visible', async () => {
    const { env } = setup()
    const mock = stubResend()
    const r = row([HELD], {
      audit_write_failures: 90,
      audit_write_failures_rose_at: '2026-10-05 14:00:00',
    })
    await notifyShortfalls(env, [r], NOW)
    expect(sent(mock)[0].html).toContain('lost audit write(s) in the last 24 hours')
    expect(sent(mock)[0].html).toContain('90 in its life')
  })

  it('does not present an old lifetime loss count as doubt about today', async () => {
    // 2026-10-21: every ashton-price shortfall carried "22 lost audit write(s)"
    // though no row had been lost since 10-20.
    for (const rose of ['2026-10-03 09:00:00', null]) {
      const { env } = setup()
      const mock = stubResend()
      await notifyShortfalls(
        env,
        [row([HELD], { audit_write_failures: 22, audit_write_failures_rose_at: rose })],
        NOW
      )
      expect(sent(mock)[0].html).not.toContain('lost audit write')
    }
  })

  it('escapes seat-supplied text', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(env, [row([{ ...HELD, code: 'reply_held:a<b' }])], NOW)
    expect(sent(mock)[0].html).toContain('a&lt;b')
  })
})

describe('sendShortfallDigest', () => {
  it('sends nothing for an empty week', async () => {
    const { env } = setup()
    const mock = stubResend()
    expect(await sendShortfallDigest(env)).toBeNull()
    expect(sent(mock)).toHaveLength(0)
  })

  it('groups the week of not_allowed refusals by seat, once', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(
      env,
      [
        row([
          ev({
            class: 'not_allowed',
            tool: 'mcp_smokeball_create_task',
            code: 'refuse',
            key: 'not_allowed:1',
          }),
        ]),
        {
          ...row([
            ev({
              class: 'not_allowed',
              tool: 'smd_send_message',
              code: 'refuse',
              key: 'not_allowed:2',
            }),
          ]),
          customer_slug: 'scott',
        },
      ],
      NOW
    )
    const digest = await sendShortfallDigest(env)
    expect(digest).toMatchObject({ events: 2, emailed: true })
    const [mail] = sent(mock)
    expect(mail.subject).toBe('[SMD Ops] Weekly: what a check stopped the Operator from doing (2)')
    expect(mail.html).toContain('WHAT HAPPENED')
    expect(mail.html).toContain('WHAT YOU NEED TO DO')
    expect(mail.html).toContain('ashton-price: 1')
    expect(mail.html).toContain('scott: 1')
    const above = String(mail.html).split('<hr>')[0]
    expect(above).toContain('Create a task in Smokeball')
    expect(above).not.toContain('mcp_')
    expect(above).not.toContain('\u2014')
    expect(await sendShortfallDigest(env)).toBeNull()
  })

  it('leaves a test seat out of the summary and closes its rows', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    db.exec('CREATE TABLE customer_configs (customer_slug TEXT PRIMARY KEY, seat_json TEXT)')
    db.prepare(
      `INSERT INTO customer_configs (customer_slug, seat_json) VALUES ('pilot-smokeball', ?)`
    ).run(JSON.stringify({ kind: 'proving' }))
    await notifyShortfalls(
      env,
      [
        {
          ...row([ev({ class: 'not_allowed', code: 'plugin_block', key: 'not_allowed:9' })]),
          customer_slug: 'pilot-smokeball',
        },
      ],
      NOW
    )
    expect(await sendShortfallDigest(env)).toBeNull()
    expect(sent(mock)).toHaveLength(0)
    expect(db.prepare('SELECT notified_at FROM operator_shortfalls').all()).not.toEqual([
      { notified_at: null },
    ])
  })

  it('says a tool in words, and still reads for a tool with no entry', () => {
    expect(toolWords('mcp_msgraph_mail_create_draft')).toBe('Draft an email')
    expect(toolWords('mcp_newvendor_do_thing')).toBe('Newvendor do thing')
  })
})

describe('shape', () => {
  it('drops malformed events rather than throwing', () => {
    expect(parseShortfallEvents('not json')).toEqual([])
    expect(parseShortfallEvents(JSON.stringify([{ class: 'bogus' }, ev()]))).toHaveLength(1)
  })
  it('gives every emailed kind a sentence and a step, with no em dash', () => {
    const cases: Array<[Record<string, string>, RegExp, RegExp]> = [
      [{ class: 'failed', code: 'reply_held:output_checklist' }, /held it/, /may be waiting/],
      [{ class: 'failed', code: 'reply_failed:send_error' }, /did not go out/, /may be waiting/],
      [{ class: 'limit', code: 'over_page_cap' }, /limit stopped/, /past the limit/],
    ]
    for (const [r, happened, step] of cases) {
      const row = { ...r, request_at: null } as never
      expect(emailsAPerson(row)).toBe(true)
      expect(whatHappened(row)).toMatch(happened)
      expect(nextStep('ashton-price', row)).toMatch(step)
      expect(nextStep('ashton-price', row)).toContain('ashton-price request emails')
      expect(`${whatHappened(row)} ${nextStep('ashton-price', row)}`).not.toMatch(/\u2014/)
    }
  })
  it('reads times as Phoenix time, and leaves an unparseable one alone', () => {
    expect(phoenixTime('2026-10-28T01:03:34.941Z')).toBe('Oct 27, 6:03 PM MST')
    expect(phoenixTime('2026-10-28 01:03:34')).toBe('Oct 27, 6:03 PM MST')
    expect(phoenixTime('garbage')).toBe('garbage')
  })
  it('names the digest cron the wrangler config schedules', () => {
    const toml = readFileSync(new URL('../wrangler.toml', import.meta.url).pathname, 'utf8')
    expect(toml).toContain(`"${DIGEST_CRON}"`)
  })
})
