/**
 * `.claude/bin/requests`, the terminal reader for Operator request cards
 * (migration 0122).
 *
 * The property that matters is the footer's "still unanswered" count: a NO
 * REPLY alarm whose request has no reply card is a person who asked and got
 * nothing. It is counted per REQUEST (the hash half of the card key), so a
 * reply that lands after the alarm clears it and an alarm on another request
 * does not. The subprocess cases run the real wrapper against a SQLite file
 * built from the real migration, and prove a failed read exits 1 with a reason
 * rather than printing as an empty list.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { execFileSync } from 'child_process'
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join, resolve } from 'path'
import { parseArgs, renderReport, summarize, type CardRow } from '../scripts/request-cards'

const A = 'a'.repeat(64)
const B = 'b'.repeat(64)

function row(key: string, kind: string, overrides: Partial<CardRow> = {}): CardRow {
  return {
    customer_slug: 'card-co',
    card_key: `${key}:${kind}`,
    kind,
    received_at: '2026-10-06T15:00:00.000Z',
    event_at: '2026-10-06T15:30:00.000Z',
    minutes: 30,
    tools_count: 0,
    replies: 0,
    refused: 0,
    failed: 0,
    job_lane: null,
    job_state: null,
    status: 'sent',
    attempts: 1,
    ...overrides,
  }
}

describe('arguments', () => {
  it('reads client, since and json', () => {
    expect(parseArgs(['--client', 'ashton-price', '--since', '48h', '--json'])).toEqual({
      client: 'ashton-price',
      sinceMs: 48 * 3_600_000,
      json: true,
    })
  })

  it('refuses what it does not understand', () => {
    expect(parseArgs(['--since', 'forever'])).toHaveProperty('error')
    expect(parseArgs(['--client'])).toHaveProperty('error')
    expect(parseArgs(['--client', "x' OR 1=1"])).toHaveProperty('error')
    expect(parseArgs(['--what'])).toHaveProperty('error')
  })
})

describe('the footer', () => {
  it('an alarm cleared by a later reply is not unanswered; one with no reply is', () => {
    const rows = [
      row(A, 'no_reply'),
      row(A, 'replied', { minutes: 41 }),
      row(B, 'no_reply'),
      row(B, 'job_done', { job_lane: 'demand', job_state: 'delivered' }),
    ]
    const s = summarize(rows)
    expect(s).toMatchObject({ requests: 2, noReply: 2, replied: 1, jobDone: 1, stillUnanswered: 1 })
    const text = renderReport(rows, 'then')
    expect(text).toContain('Still unanswered (an alarm with no later reply card): 1.')
    expect(text).toContain('NO REPLY after 30m')
    expect(text).toContain('demand delivered')
  })

  it('a card not yet emailed says so', () => {
    const text = renderReport([row(A, 'replied', { status: 'pending', attempts: 3 })], 'then')
    expect(text).toContain('NOT EMAILED (3 tries)')
    expect(text).toContain('Not emailed yet: 1.')
  })

  it('no rows reads as no cards, not as a blank', () => {
    expect(renderReport([], 'then')).toMatch(/no request cards since then/)
  })
})

describe('the CLI as a subprocess', { timeout: 30_000 }, () => {
  const SQLITE_STUB = `#!/usr/bin/env -S node --no-warnings
const { DatabaseSync } = require('node:sqlite')
const db = new DatabaseSync(process.env.REQUESTS_SQLITE)
const results = db.prepare(process.argv[3] || '').all()
process.stdout.write(JSON.stringify([{ results, success: true }]))
`
  let dir: string

  function run(args: string[], env: Record<string, string>): { code: number; out: string } {
    try {
      const out = execFileSync(resolve('.claude/bin/requests'), args, {
        encoding: 'utf8',
        env: { ...process.env, ...env },
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      return { code: 0, out }
    } catch (err) {
      const e = err as { status?: number; stdout?: string; stderr?: string }
      return { code: e.status ?? -1, out: `${e.stdout ?? ''}${e.stderr ?? ''}` }
    }
  }

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'requests-'))
  })

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  it('a failed read exits 1 with the reason', () => {
    const r = run([], { SS_REQUESTS_D1_CMD: join(dir, 'no-such-binary') })
    expect(r.code).toBe(1)
    expect(r.out).toMatch(/cannot read the request cards/)
  })

  it('lists cards from a database built by the real migration', async () => {
    const { DatabaseSync } = await import('node:sqlite')
    const file = join(dir, 'd1.sqlite')
    const db = new DatabaseSync(file)
    db.exec(readFileSync(resolve('migrations/0122_operator_request_cards.sql'), 'utf8'))
    const recent = new Date(Date.now() - 3_600_000).toISOString()
    const old = new Date(Date.now() - 30 * 86_400_000).toISOString()
    const insert = db.prepare(
      `INSERT INTO operator_request_cards
         (customer_slug, card_key, kind, received_at, event_at, minutes, status, attempts)
       VALUES (?, ?, ?, ?, ?, ?, 'sent', 1)`
    )
    insert.run('ashton-price', `${A}:no_reply`, 'no_reply', recent, recent, 30)
    insert.run('pilot-smokeball', `${B}:replied`, 'replied', recent, recent, 4)
    insert.run('ashton-price', `${B}:replied`, 'replied', old, old, 4)
    db.close()
    const stub = join(dir, 'd1.cjs')
    writeFileSync(stub, SQLITE_STUB)
    chmodSync(stub, 0o755)
    const env = { SS_REQUESTS_D1_CMD: stub, REQUESTS_SQLITE: file }

    const all = run([], env)
    expect(all.code).toBe(0)
    expect(all.out).toContain('2 requests, 2 cards')
    expect(all.out).toContain('Still unanswered (an alarm with no later reply card): 1.')

    const ours = run(['--client', 'smd-services', '--json'], env)
    expect(ours.code).toBe(0)
    const parsed = JSON.parse(ours.out) as { rows: CardRow[] }
    expect(parsed.rows.map((r) => r.customer_slug)).toEqual(['pilot-smokeball'])
  })
})
