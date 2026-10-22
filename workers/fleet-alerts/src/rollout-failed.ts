/**
 * The failed-rollout pager (2026-10-21, migration 0133).
 *
 * WHY. On 2026-10-20 captain-mark's rollout was refused at preflight (a staged
 * TELEGRAM_BOT_TOKEN the release did not declare) and the row sat in
 * fleet_rollouts as `failed`. A release completes only when every seat is done,
 * so nothing could complete behind it, and nobody knew until a reprovision a
 * day later. A rollout runs from a laptop; the person who started it may have
 * walked away. The record is the only witness, so the record is what pages.
 *
 * SHAPE. Event-shaped, like ./send-refused: a failed rollout has no green state
 * to return to (the seat is rolled back, or was never touched). Each seat gets
 * one `fleet_alert_state` row under `rollout_failed`, status pinned `resolved`,
 * whose last_seen_marker is the newest `finished_at` already paged. A row newer
 * than the marker pages once and moves it. The marker moves only after a
 * successful send, so a Resend outage retries on the next tick.
 *
 * FIRST PAGE. A seat with no marker yet pages only for failures in the last
 * day, so deploying this module does not mail the whole history of the table.
 *
 * Fail-soft end to end: a read error is logged and pages nothing this tick,
 * and can never stop the seat pagers that ran before it.
 */

import { escapeHtml } from './html'
import type { Env } from './index'

/** The `fleet_alert_state` condition this module owns. Never `open`. */
export const ROLLOUT_FAILED_CONDITION = 'rollout_failed'

/** The rollout states that mean a seat did not get the release and a human must look. */
export const FAILED_ROLLOUT_STATES = ['failed', 'rolled_back', 'tooling_error'] as const

/** How far back the read looks at all. */
const READ_WINDOW_MS = 7 * 24 * 3600 * 1000
/** A seat never paged before pages only for failures this recent. */
const FIRST_PAGE_WINDOW_MS = 24 * 3600 * 1000
const SUBJECT_DETAIL_CHARS = 60

/** One fleet_rollouts row, as read. */
export interface FailedRolloutRow {
  rollout_id: string
  release_id: string
  customer_slug: string
  state: string
  detail: string | null
  finished_at: string | null
}

export interface RolloutFailedPage {
  customer_slug: string
  /** The newest finished_at in `rows`; becomes last_seen_marker on success. */
  marker: string
  /** Newest first. */
  rows: FailedRolloutRow[]
}

export interface RolloutFailedNotification {
  customer_slug: string
  marker: string
  releases: string[]
  emailed: boolean
  resendId?: string
}

/** D1 stores `YYYY-MM-DDTHH:MM:SSZ`; compare as instants, never as strings. */
function instant(ts: string | null | undefined): number {
  return typeof ts === 'string' && ts.length > 0 ? Date.parse(ts) : Number.NaN
}

/**
 * Decide one seat's page. Pure.
 *
 * Holds (null) when nothing is newer than the marker, or, with no marker, when
 * nothing failed inside the first-page window. An unparseable stored marker is
 * treated as no marker, which errs toward one page rather than toward silence.
 */
export function decideRolloutFailedPage(
  slug: string,
  rows: FailedRolloutRow[],
  lastSeenMarker: string | null,
  nowMs: number
): RolloutFailedPage | null {
  const seenMs = instant(lastSeenMarker)
  const floor = Number.isNaN(seenMs) ? nowMs - FIRST_PAGE_WINDOW_MS : seenMs
  const fresh = rows
    .filter((r) => r.customer_slug === slug)
    .filter((r) => (FAILED_ROLLOUT_STATES as readonly string[]).includes(r.state))
    .filter((r) => {
      const at = instant(r.finished_at)
      return !Number.isNaN(at) && at > floor
    })
    .sort((a, b) => instant(b.finished_at) - instant(a.finished_at))
  if (fresh.length === 0) return null
  return { customer_slug: slug, marker: fresh[0].finished_at as string, rows: fresh }
}

/** Subject: the seat, the state, the release, and the head of the detail. */
export function rolloutFailedSubject(page: RolloutFailedPage): string {
  const newest = page.rows[0]
  const detail = (newest.detail ?? '').slice(0, SUBJECT_DETAIL_CHARS)
  return `[${page.customer_slug}] fleet rollout ${newest.state}: ${newest.release_id}${detail ? ` (${detail})` : ''}`
}

function rolloutFailedHtml(page: RolloutFailedPage): string {
  const items = page.rows
    .map(
      (r) =>
        `<li>${escapeHtml(r.finished_at ?? '')} &middot; ${escapeHtml(r.state)} &middot; ` +
        `${escapeHtml(r.release_id)}<br>${escapeHtml(r.detail ?? '(no detail recorded)')}</li>`
    )
    .join('')
  const release = escapeHtml(page.rows[0].release_id)
  const seat = escapeHtml(page.customer_slug)
  return (
    `<p><strong>ALERT</strong>: a fleet rollout did not land on ${seat}.</p>` +
    `<ul>${items}</ul>` +
    `<p>The seat is on the image it ran before (rolled back, or never touched). ` +
    `Until it is done, <code>${release}</code> cannot complete, so a NEW seat cannot be ` +
    `provisioned on it. Reprovisioning existing seats is unaffected.</p>` +
    `<p>Fix the cause in the detail above, then <code>operator/bin/fleet release ${release} ` +
    `--only ${seat}</code>. <code>operator/bin/fleet status</code> shows every seat.</p>`
  )
}

async function sendEmail(
  env: Env,
  page: RolloutFailedPage
): Promise<{ ok: boolean; resendId?: string }> {
  if (!env.RESEND_API_KEY) {
    console.log(`[fleet-alerts] DEV: would email rollout_failed for ${page.customer_slug}`)
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
        subject: `[SMD Ops] ${rolloutFailedSubject(page)}`,
        html: rolloutFailedHtml(page),
      }),
    })
    if (!resp.ok) {
      console.error(`[fleet-alerts] resend ${resp.status}: ${await resp.text()}`)
      return { ok: false }
    }
    const data: { id?: string } = await resp.json()
    return { ok: true, resendId: data.id }
  } catch (err) {
    console.error('[fleet-alerts] rollout_failed email failed:', err)
    return { ok: false }
  }
}

/** D1 timestamp form, so the window compares against stored values directly. */
function d1Ts(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

async function readFailures(db: D1Database, nowMs: number): Promise<FailedRolloutRow[]> {
  const result = await db
    .prepare(
      `SELECT rollout_id, release_id, customer_slug, state, detail, finished_at
         FROM fleet_rollouts
        WHERE state IN ('failed', 'rolled_back', 'tooling_error')
          AND finished_at >= ?`
    )
    .bind(d1Ts(nowMs - READ_WINDOW_MS))
    .all<FailedRolloutRow>()
  return result.results ?? []
}

async function readMarker(db: D1Database, slug: string): Promise<string | null> {
  const row = await db
    .prepare(
      'SELECT last_seen_marker FROM fleet_alert_state WHERE customer_slug = ? AND condition = ?'
    )
    .bind(slug, ROLLOUT_FAILED_CONDITION)
    .first<{ last_seen_marker: string | null }>()
  return row?.last_seen_marker ?? null
}

/** Move the marker. status stays 'resolved': this row is never a standing condition. */
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
    .bind(slug, ROLLOUT_FAILED_CONDITION, resendId, marker)
    .run()
}

/** Page every seat with a failed rollout newer than the one it was last paged for. */
export async function notifyRolloutFailures(
  env: Env,
  nowMs: number
): Promise<RolloutFailedNotification[]> {
  let rows: FailedRolloutRow[]
  try {
    rows = await readFailures(env.DB, nowMs)
  } catch (err) {
    console.error('[fleet-alerts] rollout_failed read failed:', err)
    return []
  }
  const out: RolloutFailedNotification[] = []
  for (const slug of new Set(rows.map((r) => r.customer_slug))) {
    try {
      const page = decideRolloutFailedPage(slug, rows, await readMarker(env.DB, slug), nowMs)
      if (page === null) continue
      const sent = await sendEmail(env, page)
      if (sent.ok) await writeMarker(env.DB, slug, page.marker, sent.resendId ?? null)
      out.push({
        customer_slug: slug,
        marker: page.marker,
        releases: [...new Set(page.rows.map((r) => r.release_id))],
        emailed: sent.ok,
        resendId: sent.resendId,
      })
    } catch (err) {
      console.error('[fleet-alerts] rollout_failed evaluation failed for', slug, err)
    }
  }
  return out
}
