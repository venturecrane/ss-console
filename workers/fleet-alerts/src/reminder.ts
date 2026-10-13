/**
 * Daily reminders for an open alert that carries a deadline (2026-10-13).
 *
 * Alerts here are edge-triggered: one email when a condition opens, one when it
 * resolves, silence in between. That is right for a level that flaps, and wrong
 * for one that counts down to a hard outage. connector_token_expiring opens
 * WARN_DAYS before a credential dies, and the only fix is a person getting a
 * client to re-consent. On 2026-08-27 that single email went unactioned for
 * five days and the token died on schedule (see token-expiry.ts). So while a
 * deadline condition stays open it is re-sent once a day, threaded under the
 * original alert, until it resolves.
 *
 * Only deadline conditions remind. Everything else keeps the one-email shape,
 * because reminding on a flapping level is what filled the inbox with ~200
 * alert mails in 30 days (alert-thread.ts).
 *
 * The last send is kept in fleet_alert_state.updated_at, which markOpen and
 * markReminded write from the tick's clock. No other code reads that column.
 */

import { CONNECTOR_TOKEN_EXPIRING_PREFIX } from './conditions'
import type { ConditionState } from './index'

export const REMINDER_INTERVAL_SECONDS = 86400

/** True when `condition` counts down to an outage and should re-send while open. */
export function remindsWhileOpen(condition: string): boolean {
  return condition.startsWith(CONNECTOR_TOKEN_EXPIRING_PREFIX)
}

/** D1 `datetime()` text ("YYYY-MM-DD HH:MM:SS", UTC) for a clock value. */
export function d1Timestamp(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 19).replace('T', ' ')
}

/**
 * Whether an open alert last sent at `lastSentAt` is due another send. An
 * unparseable or missing timestamp counts as due: a reminder too many is one
 * email, a reminder missed is the outage this exists to prevent.
 */
export function reminderDue(condition: string, lastSentAt: string | null, nowMs: number): boolean {
  if (!remindsWhileOpen(condition)) return false
  const last = lastSentAt === null ? NaN : Date.parse(`${lastSentAt.replace(' ', 'T')}Z`)
  if (!Number.isFinite(last)) return true
  return nowMs - last >= REMINDER_INTERVAL_SECONDS * 1000
}

export async function markReminded(
  db: D1Database,
  s: ConditionState,
  nowMs: number
): Promise<void> {
  await db
    .prepare(
      `UPDATE fleet_alert_state SET updated_at = ? WHERE customer_slug = ? AND condition = ? AND status = 'open'`
    )
    .bind(d1Timestamp(nowMs), s.customer_slug, s.condition)
    .run()
}
