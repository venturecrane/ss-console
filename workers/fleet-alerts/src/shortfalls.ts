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
 *   failed      a call failed and nothing in the session recovered it
 *   partial     the mail routine filed fewer pages than it was handed
 *   not_allowed the request was outside what the seat is entitled to do
 *
 * The first three page within the tick. not_allowed is working as designed, so
 * it waits for the Monday digest, where a person reads it as either a scope
 * conversation or a misconfigured entitlement (Captain, 2026-10-05).
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

async function throttled(db: D1Database, slug: string, nowMs: number): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT MAX(notified_at) AS last FROM operator_shortfalls
        WHERE customer_slug = ? AND class IN (${classPlaceholders(IMMEDIATE_CLASSES)})`
    )
    .bind(slug, ...IMMEDIATE_CLASSES)
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

/** "2 limit, 1 partial", in a fixed class order. */
export function classSummary(rows: { class: ShortfallClass }[]): string {
  const counts = new Map<string, number>()
  for (const r of rows) counts.set(r.class, (counts.get(r.class) ?? 0) + 1)
  return SHORTFALL_CLASSES.filter((c) => counts.has(c))
    .map((c) => `${counts.get(c)} ${c.replace('_', ' ')}`)
    .join(', ')
}

/** "job_status" or "job_status, reply_relay +2": what the subject line names. */
export function toolSummary(rows: { tool: string }[]): string {
  const tools = [...new Set(rows.map((r) => r.tool))]
  const shown = tools.slice(0, 2).join(', ')
  return tools.length > 2 ? `${shown} +${tools.length - 2}` : shown
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

/** The seat's error tokens (overlay heartbeat `_error_code`), in words. */
const CODE_WORDS: Record<string, string> = {
  no_such_job: 'it looked up a job that this tool does not know',
  not_found: 'what it looked up was not found',
  permission_denied: 'it was denied permission',
  forbidden: 'the other system refused it (forbidden)',
  unauthorized: 'the other system did not accept its sign-in',
  auth_token_rejected: "the other system rejected the seat's sign-in token",
  rate_limited: 'the other system asked it to slow down (rate limited)',
  timeout: 'it timed out',
  connection_refused: 'the other system refused the connection',
  callback_timeout: 'a check before the call timed out',
}

function codeInWords(code: string): string {
  const http = /^http_(\d{3})$/.exec(code)
  if (http) return `the other system answered HTTP ${http[1]}`
  return CODE_WORDS[code] ?? `the code was ${code}`
}

/** What happened, in a sentence, from the class and the seat's closed code. */
export function whatHappened(r: Pick<LedgerRow, 'class' | 'tool' | 'code'>): string {
  const code = r.code
  if (code.startsWith('reply_held:')) {
    return `The Operator wrote a reply, but a check held it before it was sent (${code.slice(11)}).`
  }
  if (code.startsWith('reply_failed:')) {
    return `The Operator's reply did not go out (${code.slice(13)}).`
  }
  if (code.startsWith('proof:')) {
    return `A ${r.tool.replace(/_job$/, '')} job finished, but its delivery to the firm could not be proven (${code.slice(6)}).`
  }
  switch (r.class) {
    case 'limit':
      return `A limit stopped ${r.tool} (${code}).`
    case 'partial':
      return `The mail routine read a bundle and ${code} pages.`
    case 'not_allowed':
      return `${r.tool} was refused: it is outside what this seat may do (${code}).`
    default:
      return `The Operator called ${r.tool} and ${codeInWords(code)}. Nothing later in the same session retried it successfully.`
  }
}

/**
 * Where to read who asked and what: that request's card in team@. Its subject
 * starts `[SMD Ops] <seat>` (request-card-email.ts `cardSubject`), and it is
 * the only place the request's text lives (ADR 0052).
 */
function cardPointer(slug: string, requestAt: string | null): string {
  return requestAt
    ? `the request card for the ${slug} request received ${phoenixTime(requestAt)} ` +
        `(team@, subject starting "[SMD Ops] ${slug}")`
    : `the ${slug} request cards in team@ from just before this`
}

/**
 * What SMD does about it. Every answer starts from the same question the
 * handbook asks of a SHORTFALL (when-something-goes-wrong.md): did the person
 * get what they asked for? If yes, nothing is owed; if no, they are waiting.
 */
export function nextStep(
  slug: string,
  r: Pick<LedgerRow, 'class' | 'code' | 'request_at'>
): string {
  const card = cardPointer(slug, r.request_at)
  if (r.code.startsWith('reply_held:') || r.code.startsWith('reply_failed:')) {
    return (
      `Check ${card} for a later reply. If none went out, the person is still waiting: ` +
      `hand this alert to an agent to redraft and send it, or answer them yourself.`
    )
  }
  if (r.code.startsWith('proof:')) {
    return (
      `Check ${card}: if it says the job was delivered and the firm has the result, nothing is owed. ` +
      `If not, hand this alert to an agent to finish the delivery.`
    )
  }
  switch (r.class) {
    case 'limit':
      return (
        `Read ${card} for what was asked, then decide whether it should go past the limit. ` +
        `If yes, an agent can raise it for this job; if no, tell the person what does fit.`
      )
    case 'partial':
      return `Hand this alert to an agent to file the pages that were read and not filed.`
    case 'not_allowed':
      return `Either a scope conversation with the client or an entitlement set wrong.`
    default:
      return (
        `Check ${card}. If the person got their answer anyway, nothing is owed and an agent can ` +
        `stop this from paging. If not, they are waiting: answer them, and hand this alert to an ` +
        `agent to find out why the call failed.`
      )
  }
}

/** One event, as the reader needs it: when, whose request, what happened, what to do. */
function eventBlock(slug: string, r: LedgerRow): string {
  const request = r.request_at
    ? `Serving the request received ${phoenixTime(r.request_at)}. Its card in team@ says who asked and what they wrote.`
    : `No request is linked to this event (a scheduled run${r.routine ? ` of ${r.routine}` : ''}, ` +
      `or a seat image older than 2026-10-27).`
  return (
    `<div style="margin:0 0 16px 0">` +
    `<p style="margin:0 0 4px 0"><strong>${escapeHtml(phoenixTime(r.ts))}</strong> &middot; ` +
    `${escapeHtml(r.class)} &middot; ${escapeHtml(r.tool)}</p>` +
    `<p style="margin:0 0 4px 0"><strong>Request:</strong> ${escapeHtml(request)}</p>` +
    `<p style="margin:0 0 4px 0"><strong>What happened:</strong> ${escapeHtml(whatHappened(r))}</p>` +
    `<p style="margin:0"><strong>What to do:</strong> ${escapeHtml(nextStep(slug, r))}</p>` +
    `</div>`
  )
}

function eventBlocks(slug: string, rows: LedgerRow[]): string {
  const listed = rows.slice(0, MAX_LISTED)
  const more =
    rows.length > listed.length ? `<p>${rows.length - listed.length} more not listed.</p>` : ''
  return listed.map((r) => eventBlock(slug, r)).join('') + more
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

function shortfallHtml(env: Env, row: FleetStatusRow, rows: LedgerRow[], nowMs: number): string {
  const total =
    typeof row.shortfalls === 'number' && row.shortfalls > rows.length
      ? `<p>${row.shortfalls} shortfall(s) on this seat in the last 24 hours; the ${rows.length} below are new.</p>`
      : ''
  const lost = lostWritesNote(row, nowMs)
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  return (
    `<p><strong>SHORTFALL</strong>: the Operator on <strong>${escapeHtml(row.customer_slug)}</strong> ` +
    `may not have given someone what they asked for.</p>${total}${lost}` +
    eventBlocks(row.customer_slug, rows) +
    `<p>This alert went only to SMD; nothing was sent to the client. ` +
    `<a href="${dashboard}">Fleet dashboard</a>.</p>`
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
      if (await throttled(env.DB, row.customer_slug, nowMs)) continue
      const subject = `[SMD Ops] SHORTFALL ${row.customer_slug}: ${classSummary(pending)} (${toolSummary(pending)})`
      const sent = await sendEmail(env, subject, shortfallHtml(env, row, pending, nowMs))
      if (sent.ok) {
        await markNotified(
          env.DB,
          row.customer_slug,
          pending.map((r) => r.event_key)
        )
      }
      out.push({
        customer_slug: row.customer_slug,
        events: pending.length,
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
