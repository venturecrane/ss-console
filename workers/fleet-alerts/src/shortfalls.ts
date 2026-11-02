/**
 * The shortfall pager (2026-10-05, migration 0120).
 *
 * WHAT IT IS FOR. When the Operator could not give a client what they asked
 * for, SMD hears about it before the client has to raise it. The seat reports
 * the trailing day's events on its heartbeat (overlay
 * shared/heartbeat.count_shortfalls), from every session, person and cron
 * alike. Four classes:
 *
 *   limit       a size, page or allowance cap stopped the work
 *   failed      a call failed and nothing in the session recovered it, a reply
 *               was held or failed, a delivery was unproven, or a reply went
 *               out saying the Operator could not do what was asked
 *               (`reply_could_not:<code>`, 2026-11-01)
 *   partial     the mail routine filed fewer pages than it was handed
 *   not_allowed the request was outside what the seat is entitled to do
 *
 * not_allowed is working as designed, so it waits for the Monday digest, where a
 * person reads it as either a scope conversation or a misconfigured entitlement
 * (Captain, 2026-10-05).
 *
 * WHO IS EMAILED (Captain, 2026-11-02: "mostly noise"). Every event is still
 * recorded, and operator/bin/work-health.py lists them for the triage agent. Only
 * two kinds email, because only they need a person: a reply a check held or that
 * failed (someone may be waiting, and the NO REPLY alarm stands down while a reply
 * is held), and a limit (someone decides whether to go past it). The rest are for
 * the agent or are already said elsewhere: a reply that said no and a job that held
 * or failed each have their own request email, and a late delivery proof was the
 * false alarm that repeated one paragraph four times on 2026-11-02.
 *
 * WHY A LEDGER AND NOT A MARKER. send_refused pages on a marker advance and
 * shows the newest five. A shortfall digest has to hold a week of events,
 * longer than the heartbeat's 24-hour window, so every event is written to
 * `operator_shortfalls` keyed by the seat's own stable `key`; the same event
 * arriving on every beat for a day is one row. `notified_at` is stamped only
 * after the email sends, so a Resend outage retries rather than swallows.
 *
 * VOLUME. One email per seat per THROTTLE_MINUTES at most: rows accumulate
 * between sends and go out together. The seat already drops a refusal the model
 * then retried successfully, so what arrives here is what stayed broken.
 *
 * NULL and undefined hold, as everywhere in this Worker: a seat that cannot
 * answer adds nothing to the ledger. Rows it reported earlier and that have not
 * been emailed yet (a Resend outage, the throttle) still go out.
 */

import { seatIsQuiet } from './mail-policy'
import { escapeHtml } from './html'
import type { Env } from './index'
import type { FleetStatusRow } from './fleet-status'

export const SHORTFALL_CLASSES = ['not_allowed', 'limit', 'failed', 'partial'] as const
export type ShortfallClass = (typeof SHORTFALL_CLASSES)[number]

/** Classes that page within the tick. */
export const IMMEDIATE_CLASSES: readonly ShortfallClass[] = ['limit', 'failed', 'partial']

/** At most one shortfall email per seat in this many minutes. */
export const THROTTLE_MINUTES = 30

/** Rows listed in one email; the rest are counted, never dropped. */
const MAX_LISTED = 25

/** The Monday digest cron, Phoenix 08:11. Must match wrangler.toml `crons`. */
export const DIGEST_CRON = '11 15 * * 1'

export interface ShortfallEvent {
  ts: string
  class: ShortfallClass
  tool: string
  routine: string | null
  code: string
  key: string
  /**
   * 2026-10-27 (0143): when the request this event was serving reached the
   * seat. It finds that request's card in team@, where who asked and what they
   * wrote live; ADR 0052 keeps that text out of D1, so only the time is here.
   */
  request_at: string | null
}

export interface ShortfallNotification {
  customer_slug: string
  events: number
  emailed: boolean
  resendId?: string
}

/** Parse the stored list defensively; ingest validated it, this is our own boundary. */
export function parseShortfallEvents(json: string | null | undefined): ShortfallEvent[] {
  if (typeof json !== 'string') return []
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return []
  }
  if (!Array.isArray(raw)) return []
  const out: ShortfallEvent[] = []
  for (const item of raw as unknown[]) {
    const event = parseShortfallEvent(item)
    if (event !== null) out.push(event)
  }
  return out
}

function parseShortfallEvent(item: unknown): ShortfallEvent | null {
  if (typeof item !== 'object' || item === null) return null
  const e = item as Record<string, unknown>
  if (
    typeof e.ts !== 'string' ||
    typeof e.class !== 'string' ||
    !(SHORTFALL_CLASSES as readonly string[]).includes(e.class) ||
    typeof e.tool !== 'string' ||
    typeof e.code !== 'string' ||
    typeof e.key !== 'string'
  ) {
    return null
  }
  return {
    ts: e.ts,
    class: e.class as ShortfallClass,
    tool: e.tool,
    routine: typeof e.routine === 'string' ? e.routine : null,
    code: e.code,
    key: e.key,
    request_at: typeof e.request_at === 'string' && e.request_at ? e.request_at : null,
  }
}

/**
 * Insert new events. A row an older beat stored without `request_at` (the seat
 * took the 0143 image mid-window, or the index caught up) gets it filled,
 * never overwritten.
 */
async function recordEvents(db: D1Database, slug: string, events: ShortfallEvent[]): Promise<void> {
  for (const e of events) {
    await db
      .prepare(
        `INSERT INTO operator_shortfalls
           (customer_slug, event_key, ts, class, tool, routine, code, request_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (customer_slug, event_key) DO UPDATE SET
           request_at = COALESCE(operator_shortfalls.request_at, excluded.request_at)`
      )
      .bind(slug, e.key, e.ts, e.class, e.tool, e.routine, e.code, e.request_at)
      .run()
  }
}

interface LedgerRow {
  event_key: string
  ts: string
  class: ShortfallClass
  tool: string
  routine: string | null
  code: string
  request_at: string | null
}

const LEDGER_COLUMNS = 'event_key, ts, class, tool, routine, code, request_at'

function classPlaceholders(classes: readonly string[]): string {
  return classes.map(() => '?').join(', ')
}

async function pendingRows(
  db: D1Database,
  slug: string,
  classes: readonly ShortfallClass[]
): Promise<LedgerRow[]> {
  const res = await db
    .prepare(
      `SELECT ${LEDGER_COLUMNS} FROM operator_shortfalls
        WHERE customer_slug = ? AND notified_at IS NULL
          AND class IN (${classPlaceholders(classes)})
        ORDER BY ts ASC`
    )
    .bind(slug, ...classes)
    .all<LedgerRow>()
  return res.results ?? []
}

/** The kinds that need a person. Everything else is the triage agent's. */
export function emailsAPerson(r: Pick<LedgerRow, 'class' | 'code'>): boolean {
  return (
    r.class === 'limit' || r.code.startsWith('reply_held:') || r.code.startsWith('reply_failed:')
  )
}

/** Only rows that emailed start the throttle; a quiet row must not delay a real one. */
async function throttled(db: D1Database, slug: string, nowMs: number): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT MAX(notified_at) AS last FROM operator_shortfalls
        WHERE customer_slug = ?
          AND (class = 'limit' OR code LIKE 'reply_held:%' OR code LIKE 'reply_failed:%')`
    )
    .bind(slug)
    .first<{ last: string | null }>()
  if (!row?.last) return false
  const lastMs = Date.parse(row.last.replace(' ', 'T') + 'Z')
  return Number.isFinite(lastMs) && nowMs - lastMs < THROTTLE_MINUTES * 60_000
}

async function markNotified(db: D1Database, slug: string, keys: string[]): Promise<void> {
  for (const key of keys) {
    await db
      .prepare(
        `UPDATE operator_shortfalls SET notified_at = datetime('now')
          WHERE customer_slug = ? AND event_key = ?`
      )
      .bind(slug, key)
      .run()
  }
}

/**
 * "Oct 27, 6:03 PM MST": the Captain reads times in Phoenix time, which has no
 * daylight saving, so MST is always right. The raw value when it will not parse.
 */
export function phoenixTime(ts: string): string {
  const ms = Date.parse(ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z')
  if (Number.isNaN(ms)) return ts
  const text = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Phoenix',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(ms))
  return `${text} MST`
}

/** What happened, in a sentence. Only the kinds emailsAPerson lets through reach here. */
export function whatHappened(r: Pick<LedgerRow, 'class' | 'code'>): string {
  if (r.code.startsWith('reply_held:')) {
    return 'The Operator wrote a reply, but a check held it before it was sent.'
  }
  if (r.code.startsWith('reply_failed:')) return "The Operator's reply did not go out."
  return `A limit stopped the work (${r.code}).`
}

/** Where who asked and what lives: that request's email (ADR 0052 keeps it out of D1). */
function requestEmail(slug: string, requestAt: string | null): string {
  return requestAt
    ? `the email for the ${slug} request received ${phoenixTime(requestAt)}`
    : `the ${slug} request emails from just before this`
}

/** What the reader does. Every answer starts from: did the person get what they asked for? */
export function nextStep(
  slug: string,
  r: Pick<LedgerRow, 'class' | 'code' | 'request_at'>
): string {
  const request = requestEmail(slug, r.request_at)
  if (r.class === 'limit') {
    return (
      `Decide whether it should go past the limit (${request} says what was asked). ` +
      `If yes, an agent can raise it for this job. If no, tell the person what does fit.`
    )
  }
  return (
    `Someone may be waiting. Check ${request} for a later reply. If there is none, ` +
    `give this email to an agent to send it, or answer them yourself.`
  )
}

/** One problem, however many times it happened: what happened, then what to do. */
function problemBlock(slug: string, rows: LedgerRow[]): string {
  const r = rows[rows.length - 1]
  const times = rows.length > 1 ? ` This happened ${rows.length} times.` : ''
  const request = r.request_at
    ? `The request received ${phoenixTime(r.request_at)}.`
    : `No person's request is linked${r.routine ? ` (a scheduled run of ${r.routine})` : ''}.`
  return (
    `<div style="margin:0 0 16px 0">` +
    `<p style="margin:0 0 4px 0"><strong>WHAT HAPPENED:</strong> ${escapeHtml(whatHappened(r))}${times}</p>` +
    `<p style="margin:0 0 4px 0"><strong>WHICH REQUEST:</strong> ${escapeHtml(request)}</p>` +
    `<p style="margin:0"><strong>WHAT YOU NEED TO DO:</strong> ${escapeHtml(nextStep(slug, r))}</p>` +
    `</div>`
  )
}

/** Identical events are one problem: 2026-11-02 printed one paragraph four times. */
function problemBlocks(slug: string, rows: LedgerRow[]): string {
  const groups = new Map<string, LedgerRow[]>()
  for (const r of rows.slice(0, MAX_LISTED)) {
    const key = `${r.tool}|${r.code}|${r.request_at ?? ''}`
    groups.set(key, [...(groups.get(key) ?? []), r])
  }
  const more = rows.length > MAX_LISTED ? `<p>${rows.length - MAX_LISTED} more not listed.</p>` : ''
  return [...groups.values()].map((g) => problemBlock(slug, g)).join('') + more
}

/** The agent's half, below the fold. */
function detailLines(rows: LedgerRow[]): string {
  return rows
    .slice(0, MAX_LISTED)
    .map((r) => `${escapeHtml(phoenixTime(r.ts))}, ${escapeHtml(r.tool)}, ${escapeHtml(r.code)}`)
    .join('<br>')
}

/** The subject, in words, naming what happened. */
export function shortfallSubject(slug: string, rows: LedgerRow[]): string {
  const replies = rows.filter((r) => r.class !== 'limit').length
  const limits = rows.length - replies
  const parts = [
    ...(replies > 0 ? [`a reply did not go out${replies > 1 ? ` (${replies})` : ''}`] : []),
    ...(limits > 0 ? [`a limit stopped the work${limits > 1 ? ` (${limits})` : ''}`] : []),
  ]
  return `[SMD Ops] ${slug}: ${parts.join(', ')}`
}

function rowsTable(rows: LedgerRow[], withSeat?: string): string {
  const listed = rows.slice(0, MAX_LISTED)
  const body = listed
    .map(
      (r) =>
        `<tr>${withSeat !== undefined ? `<td>${escapeHtml(withSeat)}</td>` : ''}` +
        `<td>${escapeHtml(phoenixTime(r.ts))}</td><td>${escapeHtml(r.tool)}</td>` +
        `<td>${escapeHtml(r.routine || 'not recorded')}</td>` +
        `<td>${escapeHtml(r.request_at ? phoenixTime(r.request_at) : 'none linked')}</td>` +
        `<td>${escapeHtml(r.code)}</td></tr>`
    )
    .join('')
  const more =
    rows.length > listed.length ? `<p>${rows.length - listed.length} more not listed.</p>` : ''
  const seatHead = withSeat !== undefined ? '<th>Seat</th>' : ''
  return (
    `<table border="1" cellpadding="4" cellspacing="0">` +
    `<tr>${seatHead}<th>When (MST)</th><th>Tool</th><th>Routine</th><th>Request received</th><th>Code</th></tr>` +
    `${body}</table>${more}`
  )
}

async function sendEmail(
  env: Env,
  subject: string,
  html: string
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email ${subject}`)
    return { ok: false }
  }
  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: env.ALERT_FROM_EMAIL ?? 'SMD Services Ops <team@smd.services>',
        to: env.ALERT_TO_EMAIL ?? 'team@smd.services',
        subject,
        html,
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] shortfall email failed:', err)
    return { ok: false }
  }
}

/** The window the email's counts cover ("in the last 24 hours"). */
const LOST_WRITES_WINDOW_MS = 24 * 60 * 60 * 1000

/**
 * The lost-writes note, only when the seat lost a row inside the email's
 * window. `audit_write_failures` is cumulative for the seat's life, so printing
 * it on every alert (as this did until 2026-10-21) presented an old loss as a
 * reason to doubt today's list: ashton-price carried "22 lost audit write(s)"
 * with no row lost since 10-20.
 */
function lostWritesNote(row: FleetStatusRow, nowMs: number): string {
  const rose = row.audit_write_failures_rose_at
  if (typeof rose !== 'string' || typeof row.audit_write_failures !== 'number') return ''
  const roseMs = Date.parse(`${rose.replace(' ', 'T')}Z`)
  if (Number.isNaN(roseMs) || nowMs - roseMs > LOST_WRITES_WINDOW_MS) return ''
  return (
    `<p>Note: this seat lost audit write(s) in the last 24 hours (most recently ${escapeHtml(rose)} UTC; ` +
    `${row.audit_write_failures} in its life), so this list may be short.</p>`
  )
}

function shortfallHtml(row: FleetStatusRow, rows: LedgerRow[], nowMs: number): string {
  return (
    problemBlocks(row.customer_slug, rows) +
    `<hr><p><strong>DETAILS, for the agent who picks this up</strong><br>` +
    `Seat: ${escapeHtml(row.customer_slug)}. Nothing was sent to the client.<br>` +
    `${detailLines(rows)}</p>${lostWritesNote(row, nowMs)}`
  )
}

/**
 * Record every seat's events, then page the immediate classes. Fail-soft per
 * seat so one broken row cannot silence the others.
 */
export async function notifyShortfalls(
  env: Env,
  rows: FleetStatusRow[],
  nowMs: number
): Promise<ShortfallNotification[]> {
  const out: ShortfallNotification[] = []
  for (const row of rows) {
    const events = parseShortfallEvents(row.shortfalls_json)
    try {
      if (events.length > 0) await recordEvents(env.DB, row.customer_slug, events)
      const pending = await pendingRows(env.DB, row.customer_slug, IMMEDIATE_CLASSES)
      if (pending.length === 0) continue
      // Recorded and left for the triage agent, not emailed: everything on a test
      // seat (./mail-policy), and on any seat what does not need a person.
      const quietSeat = await seatIsQuiet(env.DB, row.customer_slug)
      const forPerson = quietSeat ? [] : pending.filter(emailsAPerson)
      const forAgent = pending.filter((r) => !forPerson.includes(r))
      await markNotified(
        env.DB,
        row.customer_slug,
        forAgent.map((r) => r.event_key)
      )
      if (forPerson.length === 0) continue
      if (await throttled(env.DB, row.customer_slug, nowMs)) continue
      const sent = await sendEmail(
        env,
        shortfallSubject(row.customer_slug, forPerson),
        shortfallHtml(row, forPerson, nowMs)
      )
      if (sent.ok) {
        await markNotified(
          env.DB,
          row.customer_slug,
          forPerson.map((r) => r.event_key)
        )
      }
      out.push({
        customer_slug: row.customer_slug,
        events: forPerson.length,
        emailed: sent.ok,
        resendId: sent.resendId,
      })
    } catch (err) {
      console.error('[fleet-alerts] shortfall evaluation failed for', row.customer_slug, err)
    }
  }
  return out
}

/**
 * The Monday digest: every not-yet-reported not_allowed refusal, grouped by
 * seat. An empty week sends nothing.
 */
export async function sendShortfallDigest(env: Env): Promise<ShortfallNotification | null> {
  const res = await env.DB.prepare(
    `SELECT customer_slug, ${LEDGER_COLUMNS} FROM operator_shortfalls
      WHERE class = 'not_allowed' AND notified_at IS NULL
      ORDER BY customer_slug, ts ASC`
  ).all<LedgerRow & { customer_slug: string }>()
  const rows = res.results ?? []
  if (rows.length === 0) return null
  const bySeat = new Map<string, LedgerRow[]>()
  for (const r of rows) {
    const arr = bySeat.get(r.customer_slug)
    if (arr) arr.push(r)
    else bySeat.set(r.customer_slug, [r])
  }
  const sections = [...bySeat.entries()]
    .map(([slug, seatRows]) => {
      const byTool = new Map<string, number>()
      for (const r of seatRows) byTool.set(r.tool, (byTool.get(r.tool) ?? 0) + 1)
      const tools = [...byTool.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([tool, n]) => `${escapeHtml(tool)} (${n})`)
        .join(', ')
      return `<h3>${escapeHtml(slug)}: ${seatRows.length}</h3><p>By tool: ${tools}</p>${rowsTable(seatRows)}`
    })
    .join('')
  const html =
    `<p>What the Operator declined because it was outside what the seat is entitled to do. ` +
    `Each one is either a scope conversation with the client or an entitlement set wrong.</p>${sections}`
  const subject = `[SMD Ops] Weekly: what the Operator refused (${rows.length})`
  const sent = await sendEmail(env, subject, html)
  if (sent.ok) {
    for (const [slug, seatRows] of bySeat) {
      await markNotified(
        env.DB,
        slug,
        seatRows.map((r) => r.event_key)
      )
    }
  }
  return { customer_slug: '*', events: rows.length, emailed: sent.ok, resendId: sent.resendId }
}
