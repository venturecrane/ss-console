/**
 * The refused-or-unsent escalation pager (ss#2547, migration 0109).
 *
 * WHY THIS IS NOT A CONDITION. Every other alert in this Worker is
 * level-shaped: something is wrong, it opens; it stops being wrong, it closes
 * with a RECOVERED email. A refused send has no green state to return to. On
 * 2026-08-19 the pilot seat's deadline escalator was refused five times inside
 * 35 seconds; on 2026-08-20 it woke with five needs-you items and attempted
 * nothing. Those are two separate things a human needed to know on two separate
 * days, not one condition that stayed open. Modelled as a level it would have
 * paged once, gone quiet through the rest of the outage, and eventually mailed
 * a RECOVERED that meant only "the seat stopped trying".
 *
 * So this module pages on a MARKER ADVANCE, the same shape `sink-notify.ts`
 * uses for webhook rows. The seat reports the timestamp of its newest refusal;
 * if that is later than the marker stored on the seat's `send_refused` row, one
 * email goes out and the marker moves. The row is written with
 * `status='resolved'` deliberately and permanently: a row that is never `open`
 * cannot be reported as a stranded hold by `stale-holds.ts` and can never emit
 * a RECOVERED notice.
 *
 * NULL and undefined both hold. NULL is a seat that cannot answer; undefined is
 * this Worker reading a database where migration 0109 has not been applied yet,
 * which is a real state during a deploy. Neither is evidence that nothing was
 * refused, so neither pages and neither moves the marker.
 */

import { escapeHtml } from './html'
import type { Env, FleetStatusRow } from './index'

/** The `fleet_alert_state` condition this module owns. Never `open`. */
export const SEND_REFUSED_CONDITION = 'send_refused'

/** Max reason characters in the subject line, per the ss#2547 design. */
const SUBJECT_REASON_CHARS = 60

/** One page decided for one seat. */
export interface SendRefusedPage {
  customer_slug: string
  /** The marker that earned the page; becomes `last_seen_marker` on success. */
  marker: string
  /** `refused` or `unsent`, taken from the newest event, or `unknown`. */
  kind: string
  /** The newest event's reason verbatim, or an empty string. */
  reason: string
  events: SendRefusalEvent[]
}

/** One entry of `send_refusals_json`, as the ingest handler stored it. */
export interface SendRefusalEvent {
  ts: string
  kind: string
  routine?: string
  tool?: string
  reason?: string
  needs_you?: number
}

/** A delivered (or attempted) page, surfaced in the run summary. */
export interface SendRefusedNotification {
  customer_slug: string
  marker: string
  kind: string
  emailed: boolean
  resendId?: string
}

/**
 * Parse the stored event list defensively.
 *
 * The ingest already validated every entry; this is the Worker's own trust
 * boundary. A corrupt list degrades to an empty one rather than throwing,
 * because the marker alone is enough to page and the detail is a courtesy.
 */
export function parseSendRefusalEvents(json: string | null | undefined): SendRefusalEvent[] {
  if (typeof json !== 'string') return []
  let raw: unknown
  try {
    raw = JSON.parse(json)
  } catch {
    return []
  }
  if (!Array.isArray(raw)) return []
  const out: SendRefusalEvent[] = []
  for (const value of raw) {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) continue
    const entry = value as Record<string, unknown>
    if (typeof entry.ts !== 'string' || typeof entry.kind !== 'string') continue
    const parsed: SendRefusalEvent = { ts: entry.ts, kind: entry.kind }
    if (typeof entry.routine === 'string') parsed.routine = entry.routine
    if (typeof entry.tool === 'string') parsed.tool = entry.tool
    if (typeof entry.reason === 'string') parsed.reason = entry.reason
    if (typeof entry.needs_you === 'number') parsed.needs_you = entry.needs_you
    out.push(parsed)
  }
  return out
}

/**
 * Decide the page for one seat, given the marker it was last paged for.
 *
 * Pure, so the decision is testable without a database or a mail transport.
 * Returns null on every hold: no reported marker, or a marker no later than the
 * one already paged. Comparison is on the parsed instant rather than the raw
 * string so a seat that changes how it formats a timestamp cannot re-page its
 * whole backlog; an unparseable stored marker is treated as never-paged, which
 * errs toward one extra page rather than toward silence.
 */
export function decideSendRefusedPage(
  row: Pick<FleetStatusRow, 'customer_slug' | 'send_refusals_last_ts' | 'send_refusals_json'>,
  lastSeenMarker: string | null
): SendRefusedPage | null {
  const marker = row.send_refusals_last_ts
  if (typeof marker !== 'string' || marker.length === 0) return null
  const markerMs = Date.parse(marker)
  if (Number.isNaN(markerMs)) return null
  if (lastSeenMarker !== null) {
    const seenMs = Date.parse(lastSeenMarker)
    if (!Number.isNaN(seenMs) && markerMs <= seenMs) return null
  }
  const events = parseSendRefusalEvents(row.send_refusals_json)
  const newest = events.find((e) => e.ts === marker) ?? events[0]
  return {
    customer_slug: row.customer_slug,
    marker,
    kind: newest?.kind ?? 'unknown',
    reason: newest?.reason ?? '',
    events,
  }
}

/** Subject line: the seat, the kind, and the head of the reason verbatim. */
export function sendRefusedSubject(page: SendRefusedPage): string {
  const reason = page.reason.slice(0, SUBJECT_REASON_CHARS)
  return `[${page.customer_slug}] send refused: ${page.kind}${reason ? `/${reason}` : ''}`
}

function eventLine(event: SendRefusalEvent): string {
  const parts = [
    escapeHtml(event.ts),
    escapeHtml(event.kind),
    escapeHtml(event.routine ?? '(routine not recorded)'),
    escapeHtml(event.tool ?? '(tool not recorded)'),
    escapeHtml(event.reason ?? '(reason not recorded)'),
  ]
  const needsYou =
    event.needs_you === undefined
      ? ''
      : ` <em>(${escapeHtml(String(event.needs_you))} needs-you)</em>`
  return `<li>${parts.join(' &middot; ')}${needsYou}</li>`
}

function sendRefusedHtml(env: Env, page: SendRefusedPage): string {
  const dashboard = `${env.ADMIN_BASE_URL ?? 'https://admin.smd.services'}/operator`
  // Escaped throughout: every field below is text a customer Machine wrote.
  const body =
    page.events.length > 0
      ? `<ul>${page.events.map(eventLine).join('')}</ul>`
      : '<p>No per-event detail was reported on this beat.</p>'
  return (
    `<p><strong>ALERT</strong>: a routine could not reach a human.</p>` +
    `<ul><li>Seat: ${escapeHtml(page.customer_slug)}</li>` +
    `<li>Newest event: ${escapeHtml(page.marker)}</li></ul>` +
    body +
    `<p>A refusal is the gate working. The escalation not arriving is the harm. ` +
    `Read the routine and the reason above before touching the gate.</p>` +
    `<p><a href="${dashboard}">Fleet dashboard</a>. No automatic action was taken (ADR 0064/0065).</p>`
  )
}

async function sendRefusedEmail(
  env: Env,
  page: SendRefusedPage
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email send_refused for ${page.customer_slug}`)
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
        subject: `[SMD Ops] ${sendRefusedSubject(page)}`,
        html: sendRefusedHtml(env, page),
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] send_refused email failed:', err)
    return { ok: false }
  }
}

async function readMarker(db: D1Database, slug: string): Promise<string | null> {
  const row = await db
    .prepare(
      'SELECT last_seen_marker FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?'
    )
    .bind(slug, SEND_REFUSED_CONDITION)
    .first<{ last_seen_marker: string | null }>()
  return row?.last_seen_marker ?? null
}

/**
 * Move the marker forward.
 *
 * `status` is pinned to `'resolved'` on both the insert and the update. That is
 * not a lie about the seat's health: it is this row declaring that it is not a
 * standing condition, so the stale-holds sweep (which reads only `open` rows)
 * never reports it stranded and `processTransition` never sees a close to
 * announce.
 */
async function writeMarker(
  db: D1Database,
  slug: string,
  marker: string,
  resendId: string | null
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO fleet_alert_state (
         customer_slug, condition, status, opened_at, resolved_at,
         last_alert_id, last_seen_marker, updated_at)
       VALUES (?, ?, 'resolved', datetime('now'), datetime('now'), ?, ?, datetime('now'))
       ON CONFLICT (customer_slug, condition) DO UPDATE SET
         status = 'resolved', resolved_at = datetime('now'),
         last_alert_id = excluded.last_alert_id,
         last_seen_marker = excluded.last_seen_marker,
         updated_at = datetime('now')`
    )
    .bind(slug, SEND_REFUSED_CONDITION, resendId, marker)
    .run()
}

/**
 * Page every seat whose newest refusal is newer than the one already paged.
 *
 * The marker moves ONLY after a successful send, for `sink-notify.ts`'s reason:
 * a Resend outage must retry on the next cron rather than swallow the alert
 * forever. Fail-soft per seat, so one seat's broken row cannot stop the others
 * from paging.
 */
export async function notifySendRefusals(
  env: Env,
  rows: FleetStatusRow[]
): Promise<SendRefusedNotification[]> {
  const out: SendRefusedNotification[] = []
  for (const row of rows) {
    // Cheap pre-check before touching the database: the overwhelmingly common
    // case is a healthy seat reporting no events at all.
    if (typeof row.send_refusals_last_ts !== 'string' || row.send_refusals_last_ts.length === 0) {
      continue
    }
    try {
      const page = decideSendRefusedPage(row, await readMarker(env.DB, row.customer_slug))
      if (page === null) continue
      const sent = await sendRefusedEmail(env, page)
      if (sent.ok) {
        await writeMarker(env.DB, row.customer_slug, page.marker, sent.resendId ?? null)
      }
      out.push({
        customer_slug: page.customer_slug,
        marker: page.marker,
        kind: page.kind,
        emailed: sent.ok,
        resendId: sent.resendId,
      })
    } catch (err) {
      console.error('[fleet-alerts] send_refused evaluation failed for', row.customer_slug, err)
    }
  }
  return out
}
