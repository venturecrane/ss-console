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
 * answer writes nothing and pages nothing.
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
    if (typeof item !== 'object' || item === null) continue
    const e = item as Record<string, unknown>
    if (
      typeof e.ts === 'string' &&
      typeof e.class === 'string' &&
      (SHORTFALL_CLASSES as readonly string[]).includes(e.class) &&
      typeof e.tool === 'string' &&
      typeof e.code === 'string' &&
      typeof e.key === 'string'
    ) {
      out.push({
        ts: e.ts,
        class: e.class as ShortfallClass,
        tool: e.tool,
        routine: typeof e.routine === 'string' ? e.routine : null,
        code: e.code,
        key: e.key,
      })
    }
  }
  return out
}

async function recordEvents(db: D1Database, slug: string, events: ShortfallEvent[]): Promise<void> {
  for (const e of events) {
    await db
      .prepare(
        `INSERT INTO operator_shortfalls (customer_slug, event_key, ts, class, tool, routine, code)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (customer_slug, event_key) DO NOTHING`
      )
      .bind(slug, e.key, e.ts, e.class, e.tool, e.routine, e.code)
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
}

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
      `SELECT event_key, ts, class, tool, routine, code FROM operator_shortfalls
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

const CLASS_MEANING: Record<ShortfallClass, string> = {
  limit: 'a size, page or allowance limit stopped the work',
  failed: 'a call failed and nothing in the session recovered it',
  partial: 'the mail routine filed fewer pages than it was handed',
  not_allowed: 'the request was outside what the seat is entitled to do',
}

function rowsTable(rows: LedgerRow[], withSeat?: string): string {
  const listed = rows.slice(0, MAX_LISTED)
  const body = listed
    .map(
      (r) =>
        `<tr>${withSeat !== undefined ? `<td>${escapeHtml(withSeat)}</td>` : ''}` +
        `<td>${escapeHtml(r.ts)}</td><td>${escapeHtml(r.class)}</td>` +
        `<td>${escapeHtml(r.tool)}</td><td>${escapeHtml(r.routine ?? 'person request')}</td>` +
        `<td>${escapeHtml(r.code)}</td></tr>`
    )
    .join('')
  const more =
    rows.length > listed.length ? `<p>${rows.length - listed.length} more not listed.</p>` : ''
  const seatHead = withSeat !== undefined ? '<th>Seat</th>' : ''
  return (
    `<table border="1" cellpadding="4" cellspacing="0">` +
    `<tr>${seatHead}<th>When (UTC)</th><th>Class</th><th>Tool</th><th>Routine</th><th>Code</th></tr>` +
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

function shortfallHtml(env: Env, row: FleetStatusRow, rows: LedgerRow[]): string {
  const meanings = [...new Set(rows.map((r) => r.class))]
    .map((c) => `<li><strong>${escapeHtml(c)}</strong>: ${escapeHtml(CLASS_MEANING[c])}</li>`)
    .join('')
  const total =
    typeof row.shortfalls === 'number'
      ? `<p>${row.shortfalls} shortfall(s) on this seat in the last 24 hours, all classes; ${rows.length} new here.</p>`
      : ''
  const lost =
    typeof row.audit_write_failures === 'number' && row.audit_write_failures > 0
      ? `<p>Note: this seat reports ${row.audit_write_failures} lost audit write(s), so this list may be short.</p>`
      : ''
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  return (
    `<p><strong>SHORTFALL</strong>: the Operator on <strong>${escapeHtml(row.customer_slug)}</strong> ` +
    `could not give someone what they asked for.</p><ul>${meanings}</ul>${total}${lost}` +
    rowsTable(rows) +
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
      const subject = `[SMD Ops] SHORTFALL ${row.customer_slug}: ${classSummary(pending)}`
      const sent = await sendEmail(env, subject, shortfallHtml(env, row, pending))
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
    `SELECT customer_slug, event_key, ts, class, tool, routine, code FROM operator_shortfalls
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
