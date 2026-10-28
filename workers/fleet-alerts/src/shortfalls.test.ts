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
  classSummary,
  DIGEST_CRON,
  nextStep,
  notifyShortfalls,
  parseShortfallEvents,
  phoenixTime,
  sendShortfallDigest,
  THROTTLE_MINUTES,
  toolSummary,
  whatHappened,
} from './shortfalls'
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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('notifyShortfalls', () => {
  it('pages the 2026-10-01 shape: a mail bundle filed short', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    const out = await notifyShortfalls(env, [row([ev()])], NOW)
    expect(out).toEqual([
      expect.objectContaining({ customer_slug: 'ashton-price', events: 1, emailed: true }),
    ])
    const [mail] = sent(mock)
    expect(mail.subject).toBe(
      '[SMD Ops] SHORTFALL ashton-price: 1 partial (mcp_smokeball_file_attachment_pages_to_matter)'
    )
    expect(mail.html).toContain('filed 0 of 52')
    const stored = db.prepare('SELECT notified_at FROM operator_shortfalls').all() as Array<{
      notified_at: string | null
    }>
    expect(stored).toHaveLength(1)
    expect(stored[0].notified_at).not.toBeNull()
  })

  // 2026-10-24: a missing routine says only that the seat named none. Job-lane
  // delivery failures carry no routine whether a person or a schedule started
  // them, so the cell must not claim a person asked.
  it('says no request is linked when the seat named none, never "person request"', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(
      env,
      [
        row([
          ev({ routine: null, class: 'failed', code: 'proof:review_held', key: 'failed:1' }),
          ev({ routine: '', class: 'failed', code: 'proof:gate_held', key: 'failed:2' }),
        ]),
      ],
      NOW
    )
    const [mail] = sent(mock)
    expect(mail.html.match(/No request is linked to this event/g)).toHaveLength(2)
    expect(mail.html).not.toContain('person request')
  })

  // 2026-10-27, the Captain on an A&P page that read only "job_status failed
  // error": "I cannot tell what happened or what the next steps should be."
  it('says when, which request, what happened and what to do, in Phoenix time', async () => {
    const { db, env } = setup()
    const mock = stubResend()
    const explained = ev({
      ts: '2026-10-28T01:03:34.941Z',
      class: 'failed',
      tool: 'mcp_smokeball_get_matter',
      routine: null,
      code: 'http_500',
      key: 'failed:explained',
      request_at: '2026-10-28T00:54:43.000Z',
    })
    await notifyShortfalls(env, [row([explained])], NOW)
    const [mail] = sent(mock)
    expect(mail.subject).toBe(
      '[SMD Ops] SHORTFALL ashton-price: 1 failed (mcp_smokeball_get_matter)'
    )
    expect(mail.html).toContain('Oct 27, 6:03 PM MST')
    expect(mail.html).toContain('Serving the request received Oct 27, 5:54 PM MST')
    expect(mail.html).toContain('the other system answered HTTP 500')
    expect(mail.html).toContain(
      '<strong>What to do:</strong> Check the request card for the ashton-price request received Oct 27, 5:54 PM MST'
    )
    expect(mail.html).not.toContain('UTC')
    const stored = db.prepare('SELECT request_at FROM operator_shortfalls').get() as {
      request_at: string
    }
    expect(stored.request_at).toBe('2026-10-28T00:54:43.000Z')
  })

  it('fills request_at on a row an older beat stored without it, never overwrites', async () => {
    const { db, env } = setup()
    stubResend()
    await notifyShortfalls(env, [row([ev({ key: 'k1' })])], NOW)
    await notifyShortfalls(
      env,
      [row([ev({ key: 'k1', request_at: '2026-10-01T15:00:00.000Z' })])],
      NOW
    )
    await notifyShortfalls(
      env,
      [row([ev({ key: 'k1', request_at: '2026-10-01T16:00:00.000Z' })])],
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
    await notifyShortfalls(env, [row([ev()])], NOW)
    await notifyShortfalls(env, [row([ev()])], NOW + 2 * 60_000)
    await notifyShortfalls(env, [row([ev()])], NOW + (THROTTLE_MINUTES + 5) * 60_000)
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
    await notifyShortfalls(env, [row([ev()])], NOW)
    const later = [
      ev(),
      ev({ class: 'limit', code: 'over_page_cap', key: 'limit:2' }),
      ev({ class: 'failed', code: 'api_error', key: 'failed:3' }),
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
      '[SMD Ops] SHORTFALL ashton-price: 1 limit, 1 failed (mcp_smokeball_file_attachment_pages_to_matter)'
    )
  })

  it('keeps the rows un-notified when Resend fails, so the next tick retries', async () => {
    const { db, env } = setup()
    stubResend(false)
    const out = await notifyShortfalls(env, [row([ev()])], NOW)
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
    const r = row([ev()], {
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
        [row([ev()], { audit_write_failures: 22, audit_write_failures_rose_at: rose })],
        NOW
      )
      expect(sent(mock)[0].html).not.toContain('lost audit write')
    }
  })

  it('escapes seat-supplied text', async () => {
    const { env } = setup()
    const mock = stubResend()
    await notifyShortfalls(env, [row([ev({ code: 'a<b' })])], NOW)
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
    expect(mail.subject).toBe('[SMD Ops] Weekly: what the Operator refused (2)')
    expect(mail.html).toContain('ashton-price: 1')
    expect(mail.html).toContain('scott: 1')
    expect(await sendShortfallDigest(env)).toBeNull()
  })
})

describe('shape', () => {
  it('drops malformed events rather than throwing', () => {
    expect(parseShortfallEvents('not json')).toEqual([])
    expect(parseShortfallEvents(JSON.stringify([{ class: 'bogus' }, ev()]))).toHaveLength(1)
  })
  it('summarizes classes in a fixed order', () => {
    expect(classSummary([{ class: 'partial' }, { class: 'limit' }, { class: 'limit' }])).toBe(
      '2 limit, 1 partial'
    )
  })
  it('gives every kind of event a sentence and a step', () => {
    const cases: Array<[Record<string, string>, RegExp, RegExp]> = [
      [
        { class: 'failed', tool: 'reply_relay', code: 'reply_held:output_checklist' },
        /held it/,
        /still waiting/,
      ],
      [
        { class: 'failed', tool: 'reply_relay', code: 'reply_failed:send_error' },
        /did not go out/,
        /still waiting/,
      ],
      [
        { class: 'failed', tool: 'casefile_job', code: 'proof:review_held' },
        /casefile job/,
        /finish the delivery/,
      ],
      [
        { class: 'limit', tool: 'read_pages', code: 'over_page_cap' },
        /limit stopped/,
        /past the limit/,
      ],
      [
        { class: 'partial', tool: 'file_pages', code: 'filed 3 of 9' },
        /filed 3 of 9 pages/,
        /file the pages/,
      ],
      [
        { class: 'failed', tool: 'job_status', code: 'no_such_job' },
        /called job_status and it looked up a job that this tool does not know/,
        /got their answer/,
      ],
      [
        { class: 'failed', tool: 'some_tool', code: 'ValueError' },
        /the code was ValueError/,
        /got their answer/,
      ],
    ]
    for (const [r, happened, step] of cases) {
      const row = { ...r, request_at: null } as never
      expect(whatHappened(row)).toMatch(happened)
      expect(nextStep('ashton-price', row)).toMatch(step)
      if (r.class !== 'partial')
        expect(nextStep('ashton-price', row)).toContain('ashton-price request cards in team@')
      expect(`${whatHappened(row)} ${nextStep('ashton-price', row)}`).not.toMatch(/—/)
    }
  })
  it('reads times as Phoenix time, and leaves an unparseable one alone', () => {
    expect(phoenixTime('2026-10-28T01:03:34.941Z')).toBe('Oct 27, 6:03 PM MST')
    expect(phoenixTime('2026-10-28 01:03:34')).toBe('Oct 27, 6:03 PM MST')
    expect(phoenixTime('garbage')).toBe('garbage')
  })
  it('names at most two tools in the subject', () => {
    expect(toolSummary([{ tool: 'a' }, { tool: 'b' }, { tool: 'a' }, { tool: 'c' }])).toBe(
      'a, b +1'
    )
  })
  it('names the digest cron the wrangler config schedules', () => {
    const toml = readFileSync(new URL('../wrangler.toml', import.meta.url).pathname, 'utf8')
    expect(toml).toContain(`"${DIGEST_CRON}"`)
  })
})
