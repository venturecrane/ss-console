/**
 * request-cards.ts -- the terminal reader for Operator request cards
 * (migration 0122). Run it through `.claude/bin/requests`.
 *
 *   .claude/bin/requests [--client <slug>] [--since 7d|48h] [--json]
 *
 * One line per card, newest request first, then a footer of counts. The
 * footer's "still unanswered" line is the one to read: a NO REPLY alarm for a
 * request that has no later `replied` card means a person asked and, as far
 * as any seat has reported, nobody answered.
 *
 * A reply line says what the request queued (`QUEUED medchron`, `queued
 * none`, or `queued ?` for a card recorded before migration 0134). Read that,
 * not job_lane: job_lane fills only when a job ENDS.
 *
 * A `REQUESTER WROTE on held <lane> job` line (migration 0136) is a message a
 * person sent in the thread of a request whose job is held: the firm's answer
 * to the one question a hold can ask it, or a chase. Either way it is SMD's to
 * act on; the Operator cannot record a decision on a held job. The words are
 * in the email.
 *
 * The table holds numbers and keys only (ADR 0052 s5): who asked and what
 * they asked live in the email in team@smd.services, found by the short
 * request id this prints (the email's "Card:" line carries the full key).
 *
 * Exit codes: 0 read (including "no cards"), 1 bad arguments or the read
 * failed. A failed read says why and never prints as an empty list.
 *
 * Env: SS_REQUESTS_D1_CMD  override the wrangler invocation (tests); called as
 *                          `<cmd> <database> <sql>`
 *      SS_REQUESTS_DB      D1 database name (default ss-console-db)
 */

import { seatsOf } from './lib/seat-clients.mjs'
import { wranglerD1 } from './lib/wrangler-d1.ts'

const D1_TIMEOUT_MS = 12_000
const ROW_LIMIT = 500

export interface CardRow {
  customer_slug: string
  card_key: string
  kind: string
  received_at: string
  event_at: string
  minutes: number
  tools_count: number
  replies: number
  refused: number
  failed: number
  job_lane: string | null
  job_state: string | null
  status: string
  attempts: number
  /** The lane a lane_unproven card names (migration 0125); absent before it. */
  lane_key?: string | null
  /**
   * The job lanes the request's turn queued (migration 0134): '' none, NULL
   * recorded before 0134 (unknown, never "none").
   */
  jobs_queued?: string | null
}

export interface RequestsArgs {
  client: string | null
  sinceMs: number
  json: boolean
}

const UNIT_MS: Record<string, number> = { h: 3_600_000, d: 86_400_000 }

export function parseSince(value: string): number | null {
  const m = /^(\d{1,4})([hd])$/.exec(value)
  if (!m) return null
  const n = Number(m[1])
  return n > 0 ? n * UNIT_MS[m[2]] : null
}

export function parseArgs(argv: string[]): RequestsArgs | { error: string } {
  const args: RequestsArgs = { client: null, sinceMs: 7 * UNIT_MS.d, json: false }
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i]
    if (flag === '--json') {
      args.json = true
    } else if (flag === '--client' || flag === '--since') {
      const value = argv[++i]
      if (!value) return { error: `${flag} needs a value` }
      if (flag === '--client') {
        if (!/^[a-z0-9-]{1,64}$/.test(value)) return { error: `not a client slug: ${value}` }
        args.client = value
      } else {
        const ms = parseSince(value)
        if (ms === null) return { error: `--since takes e.g. 7d or 48h, not ${value}` }
        args.sinceMs = ms
      }
    } else {
      return { error: `unknown argument: ${flag}` }
    }
  }
  return args
}

/** The request half of a card key; every card for one request shares it. */
export function requestIdOf(cardKey: string): string {
  return cardKey.split(':')[0] ?? cardKey
}

/** What a reply card's turn queued; unknown is said, never shown as none. */
export function queuedLabel(row: CardRow): string {
  if (row.jobs_queued === null || row.jobs_queued === undefined) return 'queued ?'
  return row.jobs_queued === '' ? 'queued none' : `QUEUED ${row.jobs_queued}`
}

function outcome(row: CardRow): string {
  if (row.kind === 'replied') return `replied in ${row.minutes}m, ${queuedLabel(row)}`
  if (row.kind === 'no_reply') return `NO REPLY after ${row.minutes}m`
  if (row.kind === 'lane_unproven') {
    return `NEW KIND ${row.lane_key ?? '?'} ${row.job_lane ? 'job waiting' : 'first use'}`
  }
  if (row.kind === 'held_reply') {
    return `REQUESTER WROTE on held ${row.job_lane ?? 'job'} job at +${row.minutes}m`
  }
  if (row.kind === 'followup') return `later reply sent at +${row.minutes}m`
  return `${row.job_lane ?? 'job'} ${row.job_state ?? 'ended'} at +${row.minutes}m`
}

export function renderLine(row: CardRow): string {
  const when = row.received_at.slice(0, 16).replace('T', ' ')
  const sent = row.status === 'sent' ? 'emailed' : `NOT EMAILED (${row.attempts} tries)`
  return [
    `${when}Z`,
    row.customer_slug.padEnd(16),
    outcome(row).padEnd(40),
    `tools ${row.tools_count}`,
    `refused ${row.refused}`,
    `failed ${row.failed}`,
    sent,
    requestIdOf(row.card_key).slice(0, 12),
  ].join('  ')
}

export interface Summary {
  cards: number
  requests: number
  replied: number
  noReply: number
  jobDone: number
  laneUnproven: number
  /** Messages a requester wrote on a held job (migration 0136): each is SMD's to act on. */
  heldReply: number
  /** Replies sent after the first was carded, or with no request (migration 0141). */
  laterReplies: number
  notEmailed: number
  stillUnanswered: number
}

export function summarize(rows: readonly CardRow[]): Summary {
  const answered = new Set(
    rows.filter((r) => r.kind === 'replied').map((r) => requestIdOf(r.card_key))
  )
  const alarms = rows.filter((r) => r.kind === 'no_reply')
  return {
    cards: rows.length,
    requests: new Set(rows.map((r) => requestIdOf(r.card_key))).size,
    replied: rows.filter((r) => r.kind === 'replied').length,
    noReply: alarms.length,
    jobDone: rows.filter((r) => r.kind === 'job_done').length,
    laneUnproven: rows.filter((r) => r.kind === 'lane_unproven').length,
    heldReply: rows.filter((r) => r.kind === 'held_reply').length,
    laterReplies: rows.filter((r) => r.kind === 'followup').length,
    notEmailed: rows.filter((r) => r.status !== 'sent').length,
    stillUnanswered: alarms.filter((r) => !answered.has(requestIdOf(r.card_key))).length,
  }
}

export function renderReport(rows: readonly CardRow[], sinceLabel: string): string {
  if (rows.length === 0) return `requests: no request cards since ${sinceLabel}.`
  const s = summarize(rows)
  return [
    ...rows.map(renderLine),
    '',
    `${s.requests} requests, ${s.cards} cards since ${sinceLabel}: ` +
      `${s.replied} replied, ${s.noReply} NO REPLY alarms, ${s.jobDone} job endings, ` +
      `${s.laneUnproven} new kinds of ask, ${s.heldReply} requester messages on held jobs, ` +
      `${s.laterReplies} later replies sent.`,
    `Still unanswered (an alarm with no later reply card): ${s.stillUnanswered}.`,
    `Not emailed yet: ${s.notEmailed}.`,
  ].join('\n')
}

export async function readCards(args: RequestsArgs, nowMs: number): Promise<CardRow[]> {
  const db = wranglerD1({
    database: process.env.SS_REQUESTS_DB || 'ss-console-db',
    commandOverride: process.env.SS_REQUESTS_D1_CMD || null,
    timeoutMs: D1_TIMEOUT_MS,
  })
  const since = new Date(nowMs - args.sinceMs).toISOString()
  const seats = args.client ? seatsOf(args.client) : []
  const seatFilter =
    seats.length > 0 ? ` AND customer_slug IN (${seats.map(() => '?').join(', ')})` : ''
  const result = await db
    .prepare(
      `SELECT customer_slug, card_key, kind, received_at, event_at, minutes, tools_count,
              replies, refused, failed, job_lane, job_state, status, attempts, lane_key,
              jobs_queued
         FROM operator_request_cards
        WHERE received_at >= ?${seatFilter}
        ORDER BY received_at DESC, customer_slug, kind
        LIMIT ${ROW_LIMIT}`
    )
    .bind(since, ...seats)
    .all<CardRow>()
  return result.results
}

export async function main(argv: string[], nowMs = Date.now()): Promise<number> {
  const args = parseArgs(argv)
  if ('error' in args) {
    console.error(`requests: ${args.error}`)
    console.error('usage: .claude/bin/requests [--client <slug>] [--since 7d|48h] [--json]')
    return 1
  }
  let rows: CardRow[]
  try {
    rows = await readCards(args, nowMs)
  } catch (err) {
    const why = String(err instanceof Error ? err.message : err).split('\n')[0]
    console.error(`requests: cannot read the request cards (${why})`)
    return 1
  }
  if (args.json) {
    console.log(JSON.stringify({ rows, summary: summarize(rows) }))
    return 0
  }
  const sinceLabel = new Date(nowMs - args.sinceMs).toISOString().slice(0, 16).replace('T', ' ')
  console.log(renderReport(rows, `${sinceLabel}Z`))
  return 0
}

const invokedDirectly = process.argv[1]?.endsWith('request-cards.ts')
if (invokedDirectly) {
  process.exitCode = await main(process.argv.slice(2))
}
